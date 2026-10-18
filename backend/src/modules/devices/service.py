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


def resolver_codigo(codigo: str) -> dict[str, Any]:
    """
    Resuelve un codigo leido por el lector a un componente.

    La busqueda es sobre `qr_code` Y `serial_number` en la MISMA consulta: lo que
    el operador escanea puede ser cualquiera de los dos, y el caso mas comun en
    el campo es el equipo viejo con la etiqueta del proveedor y sin QR propio.

    Devuelve `encontrado=False` con un `motivo` en vez de levantar excepcion, y
    la razon esta en el docstring del schema: el operador necesita distinguir
    "no existe" de "esta dado de baja", y son dos instrucciones distintas.
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

    return {
        "encontrado": False,
        "codigo": codigo,
        "motivo": (
            "Ningun componente de este inventario tiene ese codigo. Puede ser una "
            "etiqueta de otro sistema, o el componente todavia no fue dado de alta."
        ),
    }


def obtener_dispositivo(device_id: int) -> dict[str, Any]:
    """Ficha completa. Lanza `DispositivoNoEncontrado` si no existe."""
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
                SELECT movement_type, movement_date
                FROM movement_history
                WHERE device_id = :id
                ORDER BY movement_date DESC
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
    busqueda: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Listado paginado.

    El `LIKE` del filtro va con `ILIKE` y con el comodin escapado. Un `busqueda`
    de `%` sin escapar devuelve la tabla entera, que no es un error de
    seguridad sino de recursos: el cliente pidio una pagina de 50 y se lleva la
    base.
    """
    condiciones = ["d.deleted_at IS NULL"]
    parametros: dict[str, Any] = {"limite": limite, "offset": offset}

    if estado:
        condiciones.append("d.current_status = :estado")
        parametros["estado"] = estado
    if ubicacion_id:
        condiciones.append("d.current_location_id = :ubicacion_id")
        parametros["ubicacion_id"] = ubicacion_id
    if categoria:
        condiciones.append("c.name = :categoria")
        parametros["categoria"] = categoria
    if busqueda:
        # Se escapan `%`, `_` y `\`. Sin esto, buscar "a_b" trae tambien
        # "aXb", y buscar "%" trae todo.
        condiciones.append("(d.serial_number ILIKE :busqueda OR d.qr_code ILIKE :busqueda)")
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
