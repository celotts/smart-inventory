"""
Logica de dispositivos: alta, resolucion de codigo y ficha.

POR QUE UN SERVICE Y NO SQL EN EL ROUTER
----------------------------------------
Porque hay tres consultas que se necesitan en mas de un endpoint —la resolucion
del codigo, la ficha y el listado— y el `JOIN` de la ficha tiene ocho columnas.
Duplicado, el JOIN queda desactualizado en un lado y el reporte muestra
`marca` mientras la pantalla muestra `brand`.

ADEMAS DE UNA RAZON QUE NO ES DE ESTILO
----------------------------------------
`is_warehouse` y `current_status` tienen que salir SIEMPRE consistentes. Si cada
endpoint arma su propio SELECT, uno de ellos se olvida del
`CASE WHEN d.current_status = 'IN_REPAIR'` y la pantalla muestra un componente
duro. La regla vive en un solo SELECT que se reutiliza.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.core.config import get_settings
from src.modules.ai.executor import get_ai_engine
from src.modules.locations.service import ids_descendientes, ids_descendientes_categoria

# El SELECT de la ficha. Un unico lugar donde se decide que "esta en el taller"
# significa tener una reparacion abierta.
_SELECT_DISPOSITIVO = """
    SELECT
        d.id,
        d.serial_number,
        d.qr_code,
        d.current_status,
        d.installation_date,
        d.current_location_id,
        d.model_id,
        m.brand                AS marca,
        m.model                AS modelo,
        c.name                 AS categoria,
        l.store_name           AS ubicacion_nombre,
        (r.id IS NOT NULL)     AS esta_en_taller
    FROM devices d
    JOIN models m     ON m.id = d.model_id
    JOIN categories c ON c.id = m.category_id
    LEFT JOIN locations l ON l.id = d.current_location_id
    LEFT JOIN LATERAL (
        SELECT id FROM repairs r2
        WHERE r2.device_id = d.id
          AND r2.status NOT IN ('REPAIRED', 'UNREPAIRABLE')
        ORDER BY r2.entry_date DESC
        LIMIT 1
    ) r ON TRUE
"""


class DispositivoNoEncontrado(LookupError):
    """El codigo no resuelve a ningun componente."""


class CodigoDuplicado(ValueError):
    """El serial o el QR ya existen en otro componente."""


class TransicionInvalida(ValueError):
    """El cambio de estado pedido no es una transicion que el negocio reconozca."""


# La MISMA matriz que valida el trigger `validar_transicion_estado` en la base.
# Vive duplicada a proposito: el trigger es la red de seguridad (nada entra por
# otro camino: importaciones, scripts, psql a mano), y esta copia es para que el
# error llegue como un 409 con un mensaje util ANTES de tocar el motor, en vez de
# un `check_violation` en ingles de Postgres.
#
# Si las dos se desincronizan, gana el trigger: una fila invalida es imposible
# aunque esta tabla diga lo contrario. Por eso esta copia nunca es la ultima
# palabra, solo la primera.
TRANSICIONES_VALIDAS: dict[str, set[str]] = {
    "IN_WAREHOUSE": {"IN_WAREHOUSE", "IN_TRANSIT", "IN_REPAIR"},
    "IN_TRANSIT": {"IN_WAREHOUSE", "IN_REPAIR", "INSTALLED"},
    "INSTALLED": {"IN_TRANSIT", "INSTALLED"},
    "IN_REPAIR": {"IN_WAREHOUSE", "IN_TRANSIT", "INSTALLED", "IN_REPAIR"},
    "PERMANENTLY_DISPOSED": {
        "IN_WAREHOUSE",
        "IN_TRANSIT",
        "INSTALLED",
        "IN_REPAIR",
    },
}


def _validar_transicion(desde: str | None, hacia: str | None) -> None:
    """
    Rechaza un cambio de estado que el negocio no reconoce.

    No valida nada si el estado no cambia: editar el serial de un equipo no tiene
    por que pasar por la matriz, y exigir una transicion valida para un UPDATE
    que no toca el estado haria que "corregir una tilde en la ubicacion" fallara
    por un motivo que no tiene nada que ver.
    """
    if hacia is None or desde == hacia:
        return
    permitidos = TRANSICIONES_VALIDAS.get(hacia)
    if permitidos is None:
        raise TransicionInvalida(
            f"'{hacia}' no es un estado valido del inventario. Los validos son: "
            + ", ".join(TRANSICIONES_VALIDAS)
            + "."
        )
    if desde not in permitidos:
        raise TransicionInvalida(
            f"Transicion de estado invalida: de {desde} a {hacia}. "
            f"Desde {desde} solo se puede pasar a: {', '.join(sorted(permitidos))}."
        )


def _a_resumen(fila: dict[str, Any]) -> dict[str, Any]:
    """Normaliza los tipos que JSON no maneja."""
    fecha = fila.get("installation_date")
    return {
        "id": fila["id"],
        "serial_number": fila["serial_number"],
        "qr_code": fila["qr_code"],
        "current_status": fila["current_status"],
        "marca": fila.get("marca"),
        "modelo": fila.get("modelo"),
        "modelo_id": fila.get("model_id"),
        "categoria": fila.get("categoria"),
        "ubicacion_id": fila.get("current_location_id"),
        "ubicacion_nombre": fila.get("ubicacion_nombre"),
        "installation_date": fecha,
        "esta_en_taller": bool(fila.get("esta_en_taller")),
    }


def generar_qr() -> str:
    """
    Genera un QR propio para el componente.

    POR QUE GENERARLO Y NO DELEGAR EN EL SERIAL
    --------------------------------------------
    Porque el serial es del fabricante, lo elige el proveedor, y hay
    proveedores que reciclan seriales entre modelos o entre clientes. Si el QR
    fuera el serial, un equipo dado de baja y otro nuevo con el mismo serial
    compartirian etiqueta.

    El prefijo `INV-` hace que un codigo deste sistema sea reconocible a simple
    vista frente a uno de otro inventario, que ayuda cuando alguien escanea con
    la app equivocada.

    `secrets.token_hex`, no `uuid4()`: los dos son aleatorios, pero
    `token_hex` no tiene version ni variant en el layout, asi que no hay forma
    de que un escaner que adivina el patron de los UUID lo haga mas facil.
    """
    return f"INV-{secrets.token_hex(8)}"


def _conexion():
    return get_ai_engine().connect()


# Cuantas unidades se devuelven con un producto. Es un tope de prudencia: un
# producto puede tener cientos de equipos, y la lista que se muestra en el
# celular es para ELEGIR uno, no para hacer un inventario. Con 50, el que
# esta en el deposito esta seguro en la lista.
MAX_UNIDADES_PRODUCTO = 50


def _producto_por_codigo(codigo: str) -> dict[str, Any] | None:
    """
    Busca un PRODUCTO del maestro por el codigo de su etiqueta.

    QUE ES ESTO
    -----------
    El producto ("una notebook tal modelo: hay cinco en el deposito") tiene su
    propia etiqueta, con su QR (`PRD-...`) y su codigo de barras. Es LA CAJA,
    no el equipo. Cuando el operador apunta la camara a la caja, lo que lee es
    esta etiqueta.

    POR QUE LA BUSQUEDA TAMBIEN MIRA EL SKU
    ---------------------------------------
    El codigo de barras del producto es, por defecto, su SKU. Y el SKU es lo
    que el proveedor escribe a mano en la caja. Un codigo leido que no
    pertenece a ningun equipo tiene que poder encontrar al producto.

    POR QUE DEVUELVE SUS UNIDADES
    -----------------------------
    Porque la etiqueta de la caja no identifica a un equipo, y el operador
    necesita ver cuales son para elegir con que unidad registrar el
    movimiento. Devolver el producto solo, sin las unidades, lo deja en un
    callejon sin salida: sabe que leyo bien, y no sabe que hacer.
    """
    with _conexion() as conexion:
        producto = conexion.execute(
            text(
                """
                SELECT m.id, m.sku, m.brand, m.model, m.variant,
                       m.qr_code, m.barcode, c.name AS categoria
                FROM models m
                JOIN categories c ON c.id = m.category_id
                WHERE m.deleted_at IS NULL
                  AND (m.qr_code = :codigo OR m.barcode = :codigo OR m.sku = :codigo)
                LIMIT 1
                """
            ),
            {"codigo": codigo},
        ).mappings().first()

        if not producto:
            return None

        unidades = conexion.execute(
            text(
                """
                SELECT d.id, d.serial_number, d.qr_code, d.current_status,
                       l.store_name AS ubicacion_nombre
                FROM devices d
                LEFT JOIN locations l ON l.id = d.current_location_id
                WHERE d.model_id = :model_id AND d.deleted_at IS NULL
                ORDER BY d.current_status, d.serial_number
                LIMIT :limite
                """
            ),
            {"model_id": producto["id"], "limite": MAX_UNIDADES_PRODUCTO},
        ).mappings().all()

    datos = dict(producto)
    datos["unidades"] = [dict(u) for u in unidades]
    datos["equipos"] = len(datos["unidades"])
    datos["disponibles"] = sum(
        1 for u in datos["unidades"] if u["current_status"] == "IN_WAREHOUSE"
    )
    return datos


def resolver_codigo(codigo: str) -> dict[str, Any]:
    """
    Resuelve un codigo leido por el lector a un componente.

    La busqueda es sobre `qr_code` Y `serial_number` en la MISMA consulta: lo que
    el operador escanea puede ser cualquiera de los dos, y el caso mas comun en
    el campo es el equipo viejo con la etiqueta del proveedor y sin QR propio.

    Devuelve `encontrado=False` con un `motivo` en vez de levantar excepcion, y
    la razon esta en el docstring del schema: el operador necesita distinguir
    "no existe" de "esta dado de baja", y son dos instrucciones distintas.

    QUE PASA SI EL CODIGO ES DE UN PRODUCTO
    ---------------------------------------
    Si el codigo es la etiqueta de la CAJA (el producto del maestro, `PRD-...`),
    la respuesta trae `producto` con sus unidades y NO trae `dispositivo`: esa
    etiqueta no identifica a un equipo, y un escaneo de movimiento no se puede
    registrar sin saber cual. Quien llama tiene que distinguir los dos casos:
    `encontrado` sigue siendo True, porque el codigo EXISTE.
    """
    config = get_settings()
    codigo = (codigo or "").strip()

    # El limite de longitud se aplica aca y no solo en el schema, porque esta
    # funcion tambien la llaman los escaneos por lote, que no pasan por pydantic.
    if not codigo:
        return {"encontrado": False, "codigo": codigo, "motivo": "El codigo viene vacio."}

    if len(codigo) > config.scan_code_max_length:
        return {
            "encontrado": False,
            "codigo": codigo[:64],
            "motivo": (
                f"El codigo es demasiado largo: {len(codigo)} caracteres, "
                f"el maximo es {config.scan_code_max_length}."
            ),
        }

    with _conexion() as conexion:
        # Activos primero: si un codigo estuviera en las dos columnas, el
        # componente vivo es el que el operador quiere ver.
        fila = conexion.execute(
            text(
                f"""{_SELECT_DISPOSITIVO}
                WHERE d.deleted_at IS NULL
                  AND (d.qr_code = :codigo OR d.serial_number = :codigo)
                ORDER BY d.deleted_at NULLS FIRST
                LIMIT 1"""
            ),
            {"codigo": codigo},
        ).mappings().first()

        if fila:
            return {"encontrado": True, "codigo": codigo, "dispositivo": _a_resumen(dict(fila))}

        # No esta activo. Se busca tambien entre los dados de baja, porque
        # distinguir los dos casos es lo que le permite al operador actuar.
        baja = conexion.execute(
            text(
                """
                SELECT d.id, d.serial_number, d.current_status, dc.disposal_date, dc.disposal_reason
                FROM devices d
                LEFT JOIN disposal_certificates dc ON dc.device_id = d.id
                WHERE d.qr_code = :codigo OR d.serial_number = :codigo
                LIMIT 1
                """
            ),
            {"codigo": codigo},
        ).mappings().first()

    if baja:
        fecha = baja["disposal_date"]
        fecha_texto = fecha.strftime("%Y-%m-%d") if isinstance(fecha, datetime) else "una fecha desconocida"
        return {
            "encontrado": False,
            "codigo": codigo,
            "motivo": (
                f"El componente {baja['serial_number']} esta dado de baja desde {fecha_texto}."
                + (
                    f" Motivo: {baja['disposal_reason']}."
                    if baja["disposal_reason"]
                    else ""
                )
                + " Para volver a inventariarlo hay que emitir un certificado nuevo."
            ),
        }

    # La etiqueta de la CAJA (el producto del maestro) no es la de un equipo.
    # Se resuelve DESPUES del equipo y no antes, a proposito: si un codigo
    # estuviera en las dos tablas, el equipo vivo es el que el operador quiere
    # ver. El prefijo `PRD-` evita que eso pase, pero el orden lo deja
    # garantizado.
    producto = _producto_por_codigo(codigo)
    if producto:
        return {
            "encontrado": True,
            "codigo": codigo,
            "producto": producto,
            "motivo": (
                f"El codigo es la etiqueta del producto {producto['brand']} "
                f"{producto['model']} (SKU {producto['sku']})."
            ),
        }

    return {
        "encontrado": False,
        "codigo": codigo,
        "motivo": (
            "Ningun componente de este inventario tiene ese codigo. Puede ser una "
            "etiqueta de otro sistema, o el componente todavia no fue dado de alta."
        ),
    }


def obtener_qr_equipo(device_id: int) -> str:
    """
    El codigo de la ETIQUETA del equipo, que es lo que va codificado en el QR.

    No el serial: el serial es del fabricante y hay proveedores que lo
    reciclan, asi que dos equipos distintos pueden tener el mismo serial en
    distintos momentos. La etiqueta es propia del equipo.

    Lanza `DispositivoNoEncontrado` si no existe.
    """
    with _conexion() as conexion:
        fila = conexion.execute(
            text(
                """
                SELECT qr_code
                FROM devices
                WHERE id = :id AND deleted_at IS NULL
                """
            ),
            {"id": device_id},
        ).mappings().first()

        if not fila:
            raise DispositivoNoEncontrado(f"No existe un componente con id {device_id}.")

        return fila["qr_code"]


def obtener_dispositivo(device_id: int) -> dict[str, Any]:
    with _conexion() as conexion:
        fila = conexion.execute(
            text(f"{_SELECT_DISPOSITIVO} WHERE d.id = :id AND d.deleted_at IS NULL"),
            {"id": device_id},
        ).mappings().first()

        if not fila:
            raise DispositivoNoEncontrado(f"No existe un componente con id {device_id}.")

        datos = dict(fila)

        # Ultimo movimiento: alimenta el "donde estuve" de la ficha.
        ultimo = conexion.execute(
            text(
                """
                SELECT mt.name AS movement_type, mh.movement_date
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                WHERE mh.device_id = :id
                ORDER BY mh.movement_date DESC
                LIMIT 1
                """
            ),
            {"id": device_id},
        ).mappings().first()

        total = conexion.execute(
            text("SELECT COUNT(*) AS n FROM movement_history WHERE device_id = :id"),
            {"id": device_id},
        ).scalar_one()

    resumen = _a_resumen(datos)

    # Dias instalados. Es el dato que hace legible el desgaste: un componente
    # instalado 900 dias en el mismo cliente es una señal que un humano necesita
    # ver, y ninguna consulta de "cantidad de equipos" lo muestra.
    dias = None
    fecha_instalacion = datos.get("installation_date")
    if datos.get("current_status") == "INSTALLED" and isinstance(fecha_instalacion, datetime):
        ahora = datetime.now(timezone.utc)
        # La columna es `timestamp without time zone`, o sea que viene naive de
        # psycopg2. Comparar naive con aware revienta con TypeError, y el
        # sintoma aparece en la ficha de un componente instalado, que es justo
        # el caso mas comun.
        if fecha_instalacion.tzinfo is None:
            fecha_instalacion = fecha_instalacion.replace(tzinfo=timezone.utc)
        dias = max(0, (ahora - fecha_instalacion).days)

    return {
        **resumen,
        "descripcion": datos.get("categoria"),
        "dias_instalado": dias,
        "movimientos_totales": int(total or 0),
        "ultimo_movimiento": ultimo["movement_date"] if ultimo else None,
        "ultimo_movimiento_tipo": ultimo["movement_type"] if ultimo else None,
    }


def listar_dispositivos(
    *,
    estado: str | None = None,
    ubicacion_id: int | None = None,
    categoria: str | None = None,
    categoria_id: int | None = None,
    busqueda: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Listado paginado, con filtros combinables.

    LOS FILTROS DE UBICACION Y CATEGORIA BAJAN A LAS HIJAS
    -------------------------------------------------------
    Filtrar por "Deposito Central" trae tambien lo que esta en sus pasillos y
    estantes; filtrar por "Portatiles" trae sus subcategorias. El operador
    pregunta por la BODEGA y por la RAMA, no por cada estante. Sin esto, un
    filtro por bodega mostraria el deposito VACIO en un deposito con pasillos.

    LA BUSQUEDA ES DE TEXTO COMPLETO
    --------------------------------
    `search_vector` es una columna generada con el serial, el codigo de
    etiqueta y los datos del producto (marca, modelo, variante, SKU). Con
    `plainto_tsquery` el operador puede escribir "portatil dell" y encontrar
    "Dell Latitude 5520": un `ILIKE`('%portatil dell%') no lo encuentra nunca.

    El `ILIKE` queda como red de seguridad para los codigos con simbolos (un
    `INV-a1b2/3` no es una palabra del diccionario) y para los prefijos
    parciales ("SN-DEMO" sin el numero).
    """
    condiciones = ["d.deleted_at IS NULL"]
    parametros: dict[str, Any] = {"limite": limite, "offset": offset}

    if estado:
        condiciones.append("d.current_status = :estado")
        parametros["estado"] = estado
    if ubicacion_id:
        # La ubicacion y todas sus hijas (pasillos, estantes, bins).
        ids = ids_descendientes(ubicacion_id)
        if not ids:
            # La ubicacion no existe: el filtro no debe traer la base entera.
            condiciones.append("FALSE")
        else:
            condiciones.append(f"d.current_location_id IN ({','.join(str(int(i)) for i in ids)})")
    if categoria_id:
        # La categoria y todas sus subcategorias.
        ids_categoria = ids_descendientes_categoria(categoria_id)
        if not ids_categoria:
            condiciones.append("FALSE")
        else:
            condiciones.append(
                f"m.category_id IN ({','.join(str(int(i)) for i in ids_categoria)})"
            )
    if categoria:
        condiciones.append("c.name = :categoria")
        parametros["categoria"] = categoria
    if busqueda:
        # Se escapan `%`, `_` y `\`. Sin esto, buscar "a_b" trae tambien
        # "aXb", y buscar "%" trae todo.
        condiciones.append(
            "(d.serial_number ILIKE :busqueda OR d.qr_code ILIKE :busqueda "
            "OR m.sku ILIKE :busqueda OR m.brand ILIKE :busqueda OR m.model ILIKE :busqueda "
            "OR d.search_vector @@ plainto_tsquery('spanish', :busqueda) "
            "OR m.search_vector @@ plainto_tsquery('spanish', :busqueda))"
        )
        parametros["busqueda"] = f"%{_escapar_like(busqueda)}%"

    where = " AND ".join(condiciones)

    with _conexion() as conexion:
        filas = conexion.execute(
            text(
                f"""{_SELECT_DISPOSITIVO}
                WHERE {where}
                ORDER BY d.id
                LIMIT :limite OFFSET :offset"""
            ),
            parametros,
        ).mappings().all()

        total = conexion.execute(
            text(
                f"""
                SELECT COUNT(*) AS n
                FROM devices d
                JOIN models m ON m.id = d.model_id
                JOIN categories c ON c.id = m.category_id
                WHERE {where}
                """
            ),
            parametros,
        ).scalar_one()

    return {
        "items": [_a_resumen(dict(f)) for f in filas],
        "total": int(total or 0),
        "limit": limite,
        "offset": offset,
    }


def _escapar_like(texto: str) -> str:
    r"""Escapa los comodines de LIKE para que se busquen como texto literal."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def crear_dispositivo(datos: dict[str, Any]) -> dict[str, Any]:
    """
    Da de alta un componente.

    El QR se genera si no viene. Y el `INSERT` NO empieza en `IN_WAREHOUSE` si
    el componente se crea instalado: se respeta el estado que pidio el operador
    y, si es `INSTALLED` sin haber pasado por `IN_TRANSIT`, el trigger de estado
    lo va a rechazar. Acertar el error en el alta es preferible a inventar un
    `IN_TRANSIT` que nunca ocurrio.
    """
    engine = get_ai_engine()
    qr_code = datos.get("qr_code") or generar_qr()
    estado = datos.get("current_status") or "IN_WAREHOUSE"
    es_deposito = estado == "IN_WAREHOUSE"

    try:
        with engine.begin() as conexion:
            fila = conexion.execute(
                text(
                    """
                    INSERT INTO devices (
                        model_id, serial_number, qr_code, current_location_id,
                        current_status, is_warehouse, installation_date
                    )
                    VALUES (:model_id, :serial, :qr, :ubicacion, :estado, :es_deposito, :instalado)
                    RETURNING id
                    """
                ),
                {
                    "model_id": datos["model_id"],
                    "serial": datos["serial_number"],
                    "qr": qr_code,
                    "ubicacion": datos.get("location_id"),
                    "estado": estado,
                    "es_deposito": es_deposito,
                    "instalado": datos.get("installation_date"),
                },
            ).scalar_one()
    except IntegrityError as exc:
        # El UNIQUE de serial_number y el de qr_code. Se traduce el nombre de
        # la constraint del error de Postgres, porque el mensaje crudo dice
        # "duplicate key value violates unique constraint" sin decir cual de las
        # dos, y el operador necesita saber si repetir el serial o cambiar el QR.
        detalle = str(getattr(exc, "orig", exc))
        if "serial_number" in detalle:
            raise CodigoDuplicado(
                f"Ya existe un componente con el serial '{datos['serial_number']}'."
            ) from exc
        if "qr_code" in detalle:
            raise CodigoDuplicado(
                f"Ya existe un componente con el codigo QR '{qr_code}'."
            ) from exc
        raise

    return obtener_dispositivo(fila)


def _existe_y_activo(conexion, device_id: int) -> dict[str, Any] | None:
    """Lee el estado actual de un componente activo, o None si no aplica."""
    return conexion.execute(
        text(
            """
            SELECT id, current_status, model_id, current_location_id
            FROM devices
            WHERE id = :id AND deleted_at IS NULL
            """
        ),
        {"id": device_id},
    ).mappings().first()


def actualizar_dispositivo(device_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """
    Edita un componente. Solo los campos que vienen en `datos`.

    EL UPDATE SE ARMA DINAMICO Y ESO NO ES UN DESCUIDO
    --------------------------------------------------
    Un `PATCH` con todos los campos fijos obliga al frontend a reenviar el
    registro entero para cambiar una tilde, y cualquier campo que la pantalla no
    conozca se pisa con su valor por defecto. Con `exclude_unset` del lado del
    schema, acá solo llega lo que el operador toco.

    Los NOMBRES DE COLUMNA no vienen del cliente: la lista de abajo es la unica
    fuente de verdad, y cada campo que llega se traduce a una de esas columnas. Un
    `datos` con una clave desconocida se ignora en el filtro, no se interpola.
    """
    columnas_permitidas = {
        "serial_number": "serial_number",
        "qr_code": "qr_code",
        "model_id": "model_id",
        "location_id": "current_location_id",
        "installation_date": "installation_date",
    }

    engine = get_ai_engine()

    with engine.begin() as conexion:
        actual = _existe_y_activo(conexion, device_id)
        if not actual:
            raise DispositivoNoEncontrado(f"No existe un componente con id {device_id}.")

        asignaciones: list[str] = []
        parametros: dict[str, Any] = {"id": device_id}

        for campo, columna in columnas_permitidas.items():
            if campo in datos:
                asignaciones.append(f"{columna} = :{campo}")
                parametros[campo] = datos[campo]

        # El estado se valida contra la matriz antes de escribir. `is_warehouse`
        # tiene que seguirlo: es la copia denormalizada que usan los reportes, y
        # dejarla desactualizada hace que un equipo instalado cuente como
        # disponible en el deposito.
        if "current_status" in datos:
            nuevo_estado = datos["current_status"]
            _validar_transicion(actual["current_status"], nuevo_estado)
            asignaciones.append("current_status = :current_status")
            asignaciones.append("is_warehouse = :is_warehouse")
            parametros["current_status"] = nuevo_estado
            parametros["is_warehouse"] = nuevo_estado == "IN_WAREHOUSE"

        if not asignaciones:
            return obtener_dispositivo(device_id)

        try:
            conexion.execute(
                text(
                    f"UPDATE devices SET {', '.join(asignaciones)} WHERE id = :id"
                ),
                parametros,
            )
        except IntegrityError as exc:
            detalle = str(getattr(exc, "orig", exc))
            if "serial_number" in detalle:
                raise CodigoDuplicado(
                    f"Ya existe otro componente con el serial '{datos.get('serial_number')}'."
                ) from exc
            if "qr_code" in detalle:
                raise CodigoDuplicado(
                    f"Ya existe otro componente con el codigo QR '{datos.get('qr_code')}'."
                ) from exc
            raise

    return obtener_dispositivo(device_id)


def dar_de_baja(
    device_id: int, *, usuario_id: str, motivo: str | None = None
) -> dict[str, Any]:
    """
    Borrado LOGICO de un componente.

    UN EQUIPO NO SE BORRA: SE MARCA
    -------------------------------
    El `DELETE` fisico rompe el historial de movimientos (FK) y deja el
    inventario sin trazabilidad: no se puede auditar que paso con el equipo que
    ocupaba ese lugar. `deleted_at` lo saca de todas las consultas —`listar`,
    `resolver`, `obtener` filtran por `deleted_at IS NULL`— y conserva el
    historial para el reporte.

    El estado pasa a `PERMANENTLY_DISPOSED` en el mismo paso, porque un
    componente "borrado" que sigue contando como `IN_WAREHOUSE` en los KPIs es
    peor que no borrarlo: hay dos verdades sobre el mismo equipo.

    `usuario_id` es obligatorio porque `disposal_certificates.authorized_by` es
    NOT NULL: una baja sin responsable no es un dato, es un hueco en la cadena de
    custodia.
    """
    engine = get_ai_engine()

    with engine.begin() as conexion:
        actual = _existe_y_activo(conexion, device_id)
        if not actual:
            raise DispositivoNoEncontrado(f"No existe un componente con id {device_id}.")

        conexion.execute(
            text(
                """
                UPDATE devices
                SET deleted_at = NOW(),
                    current_status = 'PERMANENTLY_DISPOSED',
                    is_warehouse = FALSE
                WHERE id = :id
                """
            ),
            {"id": device_id},
        )

        # El certificado de baja. La tabla es la que sostiene la trazabilidad
        # legal: dice quien dio de baja, cuando y por que. `resolver_codigo` ya
        # lee de acá para explicarle al operador por que una etiqueta no resuelve.
        #
        # `device_id` es UNIQUE en esta tabla, asi que un componente re-dado de
        # baja (baja, reactivacion manual y baja de nuevo) chocaria con la
        # primera fila. El `ON CONFLICT` actualiza la baja vigente en lugar de
        # reventar contra un registro historico que ya no describe la situacion.
        conexion.execute(
            text(
                """
                INSERT INTO disposal_certificates
                    (device_id, disposal_date, disposal_reason, authorized_by)
                VALUES (:id, NOW(), :motivo, :autorizado_por)
                ON CONFLICT (device_id) DO UPDATE
                SET disposal_date = NOW(),
                    disposal_reason = EXCLUDED.disposal_reason,
                    authorized_by = EXCLUDED.authorized_by
                """
            ),
            {
                "id": device_id,
                "motivo": (motivo or "Baja desde el panel de inventario")[:500],
                "autorizado_por": usuario_id,
            },
        )

    return {"id": device_id, "eliminado": True, "motivo": motivo}
