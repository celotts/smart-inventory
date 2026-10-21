"""
Registro de movimientos por escaneo.

QUE ES UN ESCANEO, EN LA VIDA REAL
----------------------------------
Un operador escanea una etiqueta con el telefono, sin mirar la pantalla, y sigue
caminando. En ese momento ocurren tres cosas que el sistema tiene que resolver:

  1. IDEMPOTENCIA. La peticion sale dos veces: una vez porque la senal del
     deposito es mala y el cliente reintenta, otra porque el operador aprieta el
     boton de nuevo sin ver la respuesta. Sin proteccion quedan dos movimientos
     y el conteo del dia da el doble.

  2. GEO-VALIDACION. Dice donde esta el componente ahora. El GPS del telefono
     lo dice el cliente, o sea que es un dato que puede estar equivocado por un
     tunel, por la deriva de un sensor viejo o porque alguien lo manipulo a
     proposito. Por eso se RECALCULA en el servidor contra las coordenadas de la
     sede: no se confía en el numero que manda el telefono.

  3. ESTADO. El escaneo mueve el componente de deposito a instalado, o de
     instalado a deposito, o lo manda al taller. El estado del componente y el
     registro del movimiento tienen que cambiar JUNTOS o ninguno de los dos: si
     el movimiento se escribe y el estado no, el historial dice una cosa y la
     pantalla dice otra.

LAS TRES EN UNA TRANSACCION
--------------------------
Por eso todo va en `engine.begin()`: si el estado no se puede mover (el trigger
de la maquina de estados lo rechaza), el movimiento tampoco se escribe. Un
movimiento que existe sin cambio de estado es un componente que "esta en el
taller" con la ultima visita registrada en el deposito.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.geo import CoordenadaInvalida, dentro_del_radio, normalizar_coordenada
from src.core import audit
from src.modules.ai.executor import get_ai_engine
from src.modules.devices import service as devices_service

logger = logging.getLogger(__name__)

# Que tipo de escaneo produce que estado. Es la MAQUINA DE ESTADOS, escrita
# del lado del negocio.
#
# Por que esta tabla y no derivarla: el trigger de Postgres valida que la
# transicion sea legal, pero no sabe que tipo de movimiento la origina. Si
# "instalar" y "mandar al taller" dieran el mismo estado, la base no podria
# distinguirlos. Acá queda escrito cual es cual, y el trigger es la segunda
# linea de defensa.
ESTADO_POR_MOVIMIENTO: dict[str, str] = {
    "WAREHOUSE_ENTRY": "IN_WAREHOUSE",
    "WAREHOUSE_REENTRY": "IN_WAREHOUSE",
    "DISPOSAL": "PERMANENTLY_DISPOSED",
    "OUT_TO_CLIENT": "IN_TRANSIT",
    "INSTALLATION": "INSTALLED",
    "REPLACEMENT": "IN_TRANSIT",
    "WITHDRAWAL_TO_WORKSHOP": "IN_REPAIR",
}

# Los movimientos que exigen saber que equipo se reemplazo y por que. Es el
# CHECK `ck_movement_replacement` del esquema, declarado aca para poder dar el
# error antes de que lo de Postgres.
REQUIERE_REEMPLAZO = {"REPLACEMENT"}


class ErrorDeEscaneo(ValueError):
    """La peticion de escaneo no es valida. 400."""


class TransicionInvalida(ValueError):
    """
    El escaneo pide un cambio de estado que el inventario no permite. 409.

    POR QUE ES UNA EXCEPCION PROPIA Y NO UN 500
    ------------------------------------------
    La maquina de estados vive en un trigger de Postgres, y cuando rechaza un
    escaneo levanta una excepcion de la base. Sin capturarla, esa excepcion
    llegaba al handler global y salia un 500 con "Error interno": el operador ve
    un fallo del sistema donde en realidad le estan diciendo que esta intentando
    algo imposible.

    El mensaje del trigger ya esta escrito para un humano ("no se puede pasar de
    instalado a deposito sin pasar por transito"), asi que se conserva tal cual:
    traducirlo de nuevo seria una segunda version que puede quedar vieja.
    """


class DispositivoNoEncontrado(LookupError):
    """El codigo no resuelve a ningun componente. 404."""


class CodigoDeProducto(LookupError):
    """
    El codigo es la etiqueta de un PRODUCTO del maestro, no la de un equipo. 409.

    POR QUE NO ES UN 404
    --------------------
    El codigo EXISTE: es la etiqueta de la caja, y el operador leyo bien. Un
    404 le diria "este codigo no esta en el inventario", que es falso, y lo
    mandaria a buscar un error donde no hay. Lo que hay es un pedido que no
    se puede resolver como esta: un movimiento de inventario se registra
    sobre un EQUIPO, y la etiqueta de la caja no dice cual.

    POR QUE 409 Y NO 400
    --------------------
    El pedido esta bien formado: el codigo es legitimo, el tipo de movimiento
    es valido. Lo que no se puede es aplicarlo sin elegir una unidad. Es un
    conflicto entre lo que pidio el operador y lo que el dato permite.

    El producto y sus unidades viajan en la excepcion: el frontend los
    muestra para que el operador elija la unidad y no tenga que buscar el
    serial a mano.
    """

    def __init__(self, producto: dict[str, Any]) -> None:
        self.producto = producto
        super().__init__(
            f"El codigo es la etiqueta del producto {producto.get('brand', '')} "
            f"{producto.get('model', '')} (SKU {producto.get('sku', '')}), no la de un equipo. "
            "Elegi la unidad a la que corresponde el movimiento."
        )


def _validar_uuid(valor: str, nombre: str) -> str:
    """Valida un UUID de cliente antes de mandarlo a Postgres."""
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ErrorDeEscaneo(f"El {nombre} no es un UUID valido.") from exc


def mover_por_producto(
    *,
    model_id: int,
    cantidad: int,
    tipo_movimiento: str,
    user_id: str,
    ubicacion_id: int | None = None,
    ubicacion_origen_id: int | None = None,
    observations: str | None = None,
) -> dict[str, Any]:
    """
    Mueve N unidades de un producto, eligiendo los equipos automaticamente.

    QUE EQUIPOS SE ELIGEN, Y POR QUE EN ESE ORDEN
    ---------------------------------------------
    Se toma la lista de equipos del modelo en el estado de ORIGEN que el
    movimiento necesita, y se mueven los `cantidad` primeros. El orden no es
    arbitrario: se ordena por el movimiento mas viejo primero (`COALESCE` del
    ultimo `movement_date`, y `id` como desempate), o sea FIFO. Un deposito que
    rota por FIFO no deja equipos envejeciendo al fondo; si se eligiera al azar,
    los ultimos en entrar serian los primeros en salir y habria equipos de tres
    anos nunca tocados.

    POR QUE REUSA `escanear` Y NO ESCRIBE SQL PROPIO
    ------------------------------------------------
    Porque la maquina de estados, la geo-validacion, la idempotencia y el
    registro del historial ya viven ahi, y reescribirlas aca es la forma segura
    de que las dos versiones se desincronicen. Este endpoint es una CAPA DE
    SELECCION: elige los equipos y le pasa cada uno a la funcion que ya sabe
    moverlos. Si el trigger rechaza un equipo (esta en taller, no puede saltar de
    deposito a instalado), ese equipo falla y los demas siguen.
    """
    if tipo_movimiento not in ESTADO_POR_MOVIMIENTO:
        raise ErrorDeEscaneo(
            f"Tipo de movimiento desconocido: {tipo_movimiento!r}. "
            f"Validos: {', '.join(sorted(ESTADO_POR_MOVIMIENTO))}."
        )

    user_id = _validar_uuid(user_id, "user_id")

    # El estado del que hay que sacar los equipos lo define el movimiento. Un
    # "salida a cliente" saca de deposito; una "instalacion" saca de transito;
    # una "devolucion" (reingreso) saca de instalado o transito.
    #
    # `ORIGEN_POR_MOVIMIENTO` es la inversa de `ESTADO_POR_MOVIMIENTO`: dice
    # desde donde tiene sentido sacar cada movimiento. Sin esto, se elegirian
    # equipos en cualquier estado y la mayoria fallaria en el trigger.
    ORIGEN_POR_MOVIMIENTO: dict[str, tuple[str, ...]] = {
        "WAREHOUSE_ENTRY": ("IN_TRANSIT", "IN_WAREHOUSE"),
        "WAREHOUSE_REENTRY": ("INSTALLED", "IN_TRANSIT", "IN_REPAIR"),
        "OUT_TO_CLIENT": ("IN_WAREHOUSE",),
        "INSTALLATION": ("IN_TRANSIT",),
        "WITHDRAWAL_TO_WORKSHOP": ("IN_WAREHOUSE", "INSTALLED", "IN_TRANSIT"),
        "DISPOSAL": ("IN_WAREHOUSE", "IN_TRANSIT", "INSTALLED", "IN_REPAIR"),
        "REPLACEMENT": ("IN_WAREHOUSE",),
    }

    estados_origen = ORIGEN_POR_MOVIMIENTO.get(tipo_movimiento)
    if not estados_origen:
        raise ErrorDeEscaneo(
            f"El movimiento {tipo_movimiento} no se puede aplicar por producto: "
            "solo sirve para un equipo puntual."
        )

    engine = get_ai_engine()

    with engine.connect() as conexion:
        # Se arman los placeholders del IN a mano: SQLAlchemy no expande una
        # tupla en un `IN` sin `bindparam(expanding=True)`, y con `text()` el
        # camino mas simple y explicito es generar los nombres.
        nombres_estado = [f":e{i}" for i in range(len(estados_origen))]
        parametros: dict[str, Any] = {
            "model_id": model_id,
            "limite": cantidad,
        }
        for i, estado in enumerate(estados_origen):
            parametros[f"e{i}"] = estado

        condiciones = [
            "d.model_id = :model_id",
            "d.deleted_at IS NULL",
            f"d.current_status IN ({', '.join(nombres_estado)})",
        ]

        # Si el operador dijo DE DONDE sacar, se respeta. Sin esto, una
        # transferencia de Norte a Sur podria tomar equipos de Centro, que no es
        # lo que se pidio.
        if ubicacion_origen_id:
            condiciones.append("d.current_location_id = :ubicacion_origen")
            parametros["ubicacion_origen"] = ubicacion_origen_id

        filas = conexion.execute(
            text(
                f"""
                SELECT d.id, d.serial_number
                FROM devices d
                LEFT JOIN LATERAL (
                    SELECT MAX(mh.movement_date) AS ultimo
                    FROM movement_history mh
                    WHERE mh.device_id = d.id
                ) h ON TRUE
                WHERE {' AND '.join(condiciones)}
                ORDER BY COALESCE(h.ultimo, d.created_at) ASC, d.id ASC
                LIMIT :limite
                """
            ),
            parametros,
        ).mappings().all()

    if not filas:
        ubicacion_texto = "en ninguna sede" if not ubicacion_origen_id else f"en la sede {ubicacion_origen_id}"
        raise ErrorDeEscaneo(
            f"No hay unidades de este producto en un estado que permita "
            f"'{tipo_movimiento}' {ubicacion_texto}. Revisa el stock disponible "
            "antes de mover."
        )

    # Cada equipo se mueve por separado, con su propio `client_scan_id`
    # deterministico. Asi, si el operador reintenta el MISMO lote, cada equipo
    # devuelve replay en vez de duplicar el movimiento.
    lote_id = uuid.uuid4()
    resultados: list[dict[str, Any]] = []

    for indice, fila in enumerate(filas):
        client_scan_id = str(uuid.uuid5(lote_id, str(indice)))
        try:
            resultado = escanear(
                codigo=fila["serial_number"],
                tipo_movimiento=tipo_movimiento,
                client_scan_id=client_scan_id,
                user_id=user_id,
                ubicacion_id=ubicacion_id,
                observations=observations,
            )
            resultados.append(
                {
                    "device_id": fila["id"],
                    "serial_number": fila["serial_number"],
                    "ok": True,
                    "movement_id": resultado.get("movement_id"),
                }
            )
        except (ErrorDeEscaneo, DispositivoNoEncontrado, TransicionInvalida) as exc:
            resultados.append(
                {
                    "device_id": fila["id"],
                    "serial_number": fila["serial_number"],
                    "ok": False,
                    "error": str(exc),
                }
            )

    movidas = sum(1 for r in resultados if r["ok"])
    fallidas = len(resultados) - movidas

    if not filas:
        mensaje = "No habia unidades para mover."
    elif fallidas == 0:
        mensaje = f"Se movieron {movidas} de {len(filas)} unidades."
    elif movidas == 0:
        mensaje = f"No se pudo mover ninguna de las {len(filas)} unidades."
    else:
        mensaje = (
            f"Se movieron {movidas} de {len(filas)} unidades. "
            f"{fallidas} fallaron: revisa el detalle."
        )

    return {
        "movidas": movidas,
        "fallidas": fallidas,
        "total": len(filas),
        "resultados": resultados,
        "mensaje": mensaje,
    }


def escanear(
    *,
    codigo: str,
    tipo_movimiento: str,
    client_scan_id: str,
    user_id: str,
    ubicacion_id: int | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    replaced_device_id: int | None = None,
    replacement_reason: str | None = None,
    observations: str | None = None,
) -> dict[str, Any]:
    """
    Registra un escaneo. Idempotente por `client_scan_id`.

    Devuelve siempre el mismo formato. `replay=True` indica que el `client_scan_id`
    ya se habia procesado y que lo que se devuelve es el movimiento original: el
    cliente puede mostrar "ya registrado" sin tener que distinguir el caso.
    """
    client_scan_id = _validar_uuid(client_scan_id, "client_scan_id")
    user_id = _validar_uuid(user_id, "user_id")

    if tipo_movimiento not in ESTADO_POR_MOVIMIENTO:
        raise ErrorDeEscaneo(
            f"Tipo de movimiento desconocido: {tipo_movimiento!r}. "
            f"Validos: {', '.join(sorted(ESTADO_POR_MOVIMIENTO))}."
        )

    if tipo_movimiento in REQUIERE_REEMPLAZO and not replaced_device_id:
        raise ErrorDeEscaneo(
            "Un movimiento de reemplazo tiene que decir QUE componente se "
            "reemplaza (replaced_device_id) y POR QUE (replacement_reason)."
        )
    if tipo_movimiento not in REQUIERE_REEMPLAZO and (replaced_device_id or replacement_reason):
        raise ErrorDeEscaneo(
            f"Un movimiento de tipo {tipo_movimiento} no lleva equipo reemplazado "
            "ni motivo: solo REPLACEMENT los lleva."
        )

    # --- 0. Las coordenadas se validan ANTES de ir a la base -------------
    #
    # Solo la parte SINTACTICA (que sean numeros, que esten en rango). La
    # comparacion contra la sede necesita leer `locations`, y esa va mas abajo.
    #
    # Va primero por una razon concreta: una latitud de 400 tiene que
    # rechazarse antes de gastar una consulta en resolver el codigo. Si el
    # chequeo va al final, el cliente recibe "no existe el componente" —o un
    # 404 de una etiqueta que si existe— cuando lo que esta mal es el GPS. Es
    # el error que manda al operador a revisar la etiqueta en vez del celular.
    if latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            raise ErrorDeEscaneo(
                "Falta una de las dos coordenadas. Si mandas la ubicacion del "
                "escaneo, tiene que ir latitud Y longitud."
            )
        # `normalizar_coordenada` es la que rechaza NaN, infinito y los rangos.
        normalizar_coordenada(latitude, longitude)

    # --- 1. Resolucion del codigo ---------------------------------------
    resolucion = devices_service.resolver_codigo(codigo)

    # La etiqueta de la CAJA (el producto del maestro) no identifica a un
    # equipo. El codigo existe, asi que no es un 404: es un 409 con el
    # producto y sus unidades, para que el operador elija con cual registrar
    # el movimiento.
    producto = resolucion.get("producto")
    if producto:
        raise CodigoDeProducto(producto)

    if not resolucion["encontrado"]:
        raise DispositivoNoEncontrado(
            resolucion["motivo"] or "El codigo no corresponde a ningun componente."
        )
    dispositivo = resolucion["dispositivo"]
    device_id = dispositivo["id"]

    # --- 2. Idempotencia: si ya se proceso, se devuelve lo anterior --------
    # Va ANTES de toda validacion geografica a proposito. Un reintento tiene que
    # devolver SIEMPRE el mismo resultado, incluso si en el medio la senal
    # empeoro: si el reintento calculara una distancia distinta y rejectara, el
    # cliente veria un error en algo que ya se guardo.
    engine = get_ai_engine()
    with engine.connect() as conexion:
        previo = conexion.execute(
            text(
                """
                SELECT sr.movement_id, mt.name AS movement_type, mh.movement_date
                FROM scan_receipts sr
                JOIN movement_history mh ON mh.id = sr.movement_id
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                WHERE sr.client_scan_id = :cid
                """
            ),
            {"cid": client_scan_id},
        ).mappings().first()

    if previo:
        return {
            "replay": True,
            "movement_id": previo["movement_id"],
            "tipo_movimiento": previo["movement_type"],
            "movimiento_date": previo["movement_date"],
            "dispositivo": dispositivo,
            "geo": {"dentro_del_radio": None, "distancia_metros": None},
            "mensaje": "Este escaneo ya se habia registrado.",
        }

    # --- 3. Geo-validacion ------------------------------------------------
    geo = _validar_geo(ubicacion_id, latitude, longitude)

    # --- 4. Escritura: movimiento + estado, en UNA transaccion ------------
    estado_destino = ESTADO_POR_MOVIMIENTO[tipo_movimiento]
    es_deposito = estado_destino == "IN_WAREHOUSE"
    origen = dispositivo["ubicacion_id"]

    try:
        with engine.begin() as conexion:
            movimiento_id = conexion.execute(
                text(
                    """
                    INSERT INTO movement_history (
                        device_id, movement_type_id,
                        origin_location_id, destination_location_id,
                        replaced_device_id, replacement_reason,
                        user_id, scan_latitude, scan_longitude,
                        is_valid_location, observations
                    )
                    VALUES (
                        :device_id,
                        (SELECT id FROM movement_types WHERE name = :tipo),
                        :origen, :destino,
                        :reemplazado, :motivo,
                        :user_id, :lat, :lng,
                        :dentro, :observaciones
                    )
                    RETURNING id
                    """
                ),
                {
                    "device_id": device_id,
                    "tipo": tipo_movimiento,
                    "origen": origen,
                    "destino": ubicacion_id,
                    "reemplazado": replaced_device_id,
                    "motivo": replacement_reason,
                    "user_id": user_id,
                    "lat": latitude,
                    "lng": longitude,
                    "dentro": geo["dentro_del_radio"],
                    "observaciones": observations,
                },
            ).scalar_one()

            # El estado del componente cambia en la MISMA transaccion que el
            # movimiento. Si el trigger de la maquina de estados lo rechaza, el
            # INSERT de arriba tambien se deshace.
            #
            # `current_location_id` solo se toca si el movimiento dice adonde va.
            # Un escaneo sin destino (una correccion, una revision) no debe
            # borrar la ubicacion conocida del componente.
            conexion.execute(
                text(
                    """
                    UPDATE devices
                    SET current_status = :estado,
                        is_warehouse = :es_deposito,
                        current_location_id = COALESCE(:destino, current_location_id)
                    WHERE id = :id
                    """
                ),
                {
                    "estado": estado_destino,
                    "es_deposito": es_deposito,
                    "destino": ubicacion_id,
                    "id": device_id,
                },
            )

            # El recibo de idempotencia va ultimo: es lo que hace que el
            # reintento funcione, y va en la misma transaccion para que no
            # exista el caso "movimiento escrito, recibo no" — que deja el
            # cliente sin forma de reconocer el reintento.
            conexion.execute(
                text(
                    """
                    INSERT INTO scan_receipts (client_scan_id, device_id, movement_id, user_id)
                    VALUES (:cid, :device_id, :movement_id, :user_id)
                    """
                ),
                {
                    "cid": client_scan_id,
                    "device_id": device_id,
                    "movement_id": movimiento_id,
                    "user_id": user_id,
                },
            )

    except IntegrityError as exc:
        # Dos reintentos que llegan SIMULTANEOS: los dos pasan el SELECT de
        # idempotencia (ninguno lo escribio todavia) y los dos intentan
        # insertar. El UNIQUE de `client_scan_id` resuelve: uno gana, el otro
        # recibe el error. Se traduce a un replay en vez de un 500.
        #
        # Sin este manejo, un doble toque en el boton de escanear produce un
        # 500 y el operador, que no sabe que ya se guardo, aprieta otra vez.
        if "scan_receipts" in str(getattr(exc, "orig", exc)):
            logger.info("Escaneo concurrente con el mismo client_scan_id: replay")
            return {
                "replay": True,
                "movement_id": None,
                "tipo_movimiento": tipo_movimiento,
                "movimiento_date": None,
                "dispositivo": dispositivo,
                "geo": geo,
                "mensaje": "Este escaneo ya se estaba registrando.",
            }

        # `IntegrityError` es subclase de `DBAPIError`, asi que el chequeo del
        # trigger tiene que estar ACA y no solo en el `except DBAPIError` de
        # abajo: si no, esta rama hace `raise` y el mensaje del trigger —que
        # explica que hacer— se pierde en un 500.
        original = getattr(exc, "orig", exc)
        if getattr(original, "pgcode", None) == "23514" or "23514" in str(original):
            raise TransicionInvalida(_mensaje_de_regla(original)) from exc
        raise
    except DBAPIError as exc:
        # El trigger de la maquina de estados rechaza el cambio. Es una REGLA
        # de negocio, no un fallo: sale 409 con el motivo, nunca un 500.
        #
        # 23514 es `check_violation`, que es el SQLSTATE que usan los CHECK y
        # los `RAISE ... USING ERRCODE = 'check_violation'` del trigger.
        original = getattr(exc, "orig", exc)
        if getattr(original, "pgcode", None) == "23514" or "23514" in str(original):
            raise TransicionInvalida(_mensaje_de_regla(original)) from exc
        raise

    # --- 5. Bitacora formal ----------------------------------------------
    #
    # El movimiento YA esta confirmado en `movement_history`, que es la fuente
    # de la trazabilidad del inventario. Esta linea agrega el evento a
    # `audit_logs` para que la pantalla de auditoria pueda mostrar los
    # movimientos y los eventos de cuentas en la MISMA lista.
    #
    # Va aca y no dentro de la transaccion de escritura por dos razones:
    # `registrar` abre su propia conexion (no se puede anidar), y su contrato es
    # no propagar la excepcion — si la bitacora falla, el movimiento ya
    # registrado no se cae.
    audit.registrar(
        user_id=user_id,
        action="MOVIMIENTO",
        resource="movement_history",
        metadata={
            "accion": tipo_movimiento,
            "device_id": device_id,
            "serial_number": dispositivo.get("serial_number"),
            "movement_id": movimiento_id,
            "origen": origen,
            "destino": ubicacion_id,
        },
    )

    # Obtener historial rico del dispositivo
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Calling _obtener_historial_rico for device_id={device_id}")
    try:
        historial_rico = _obtener_historial_rico(device_id)
        logger.info(f"_obtener_historial_rico returned keys: {list(historial_rico.keys()) if historial_rico else 'None'}")
    except Exception as e:
        logger.exception(f"Error in _obtener_historial_rico: {e}")
        historial_rico = {
            "timeline": [],
            "periodos_instalacion": [],
            "reparaciones": [],
            "dias_por_ubicacion": {},
            "dias_instalado_actual": None,
            "total_movimientos": 0,
            "total_reparaciones": 0,
        }

    return {
        "replay": False,
        "movement_id": movimiento_id,
        "tipo_movimiento": tipo_movimiento,
        "movimiento_date": datetime.now(timezone.utc),
        "dispositivo": dispositivo,
        "historial": historial_rico,
        "geo": geo,
        "mensaje": _mensaje_geo(geo),
    }


def _obtener_historial_rico(device_id: int) -> dict[str, Any]:
    """
    Obtiene el historial completo y enriquecido de un dispositivo:
    - Timeline de movimientos (últimos 20)
    - Periodos de instalación por sede (con días acumulados)
    - Historial de reparaciones
    - Resumen de estado actual
    """
    engine = get_ai_engine()
    with engine.connect() as conexion:
        # 1. Timeline de movimientos (últimos 20)
        movimientos = conexion.execute(
            text(
                """
                SELECT
                    mh.id,
                    mt.name AS movement_type,
                    mh.movement_date,
                    mh.is_valid_location,
                    lo.store_name AS origen,
                    ld.store_name AS destino,
                    u.full_name AS registrado_por,
                    mh.observations
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                LEFT JOIN locations lo ON lo.id = mh.origin_location_id
                LEFT JOIN locations ld ON ld.id = mh.destination_location_id
                LEFT JOIN users u ON u.id = mh.user_id
                WHERE mh.device_id = :device_id
                ORDER BY mh.movement_date DESC, mh.id DESC
                LIMIT 20
                """
            ),
            {"device_id": device_id},
        ).mappings().all()

        # 2. Periodos de instalación (días por sede)
        periodos = conexion.execute(
            text(
                """
                SELECT
                    ip.id,
                    ip.started_at,
                    ip.ended_at,
                    ip.days_installed,
                    l.store_name AS ubicacion_nombre,
                    l.id AS ubicacion_id
                FROM installation_periods ip
                JOIN locations l ON l.id = ip.location_id
                WHERE ip.device_id = :device_id
                ORDER BY ip.started_at DESC
                """
            ),
            {"device_id": device_id},
        ).mappings().all()

        # 3. Historial de reparaciones
        reparaciones = conexion.execute(
            text(
                """
                SELECT
                    r.id,
                    r.status,
                    r.entry_date,
                    r.exit_date,
                    r.initial_diagnosis,
                    r.work_performed,
                    r.estimated_cost,
                    u.full_name AS tecnico_nombre
                FROM repairs r
                LEFT JOIN users u ON u.id = r.technician_id
                WHERE r.device_id = :device_id
                ORDER BY r.entry_date DESC
                """
            ),
            {"device_id": device_id},
        ).mappings().all()

        # 4. Calcular días instalados actual si está instalado
        dispositivo_actual = None
        with get_ai_engine().connect() as conn2:
            disp = conn2.execute(
                text(
                    """
                    SELECT current_status, installation_date, current_location_id
                    FROM devices WHERE id = :id
                    """
                ),
                {"id": device_id},
            ).mappings().first()
            if disp:
                dispositivo_actual = dict(disp)

        # Calcular días instalados actual si aplica
        dias_instalado_actual = None
        if dispositivo_actual and dispositivo_actual["current_status"] == "INSTALLED" and dispositivo_actual["installation_date"]:
            from datetime import datetime, timezone
            instalacion = dispositivo_actual["installation_date"]
            if instalacion.tzinfo is None:
                instalacion = instalacion.replace(tzinfo=timezone.utc)
            dias_instalado_actual = (datetime.now(timezone.utc) - instalacion).days

        # Calcular totales de días por ubicación
        dias_por_ubicacion = {}
        for p in periodos:
            loc = p["ubicacion_nombre"]
            dias = p["days_installed"] or 0
            if p["ended_at"] is None and p["started_at"]:
                # Periodo abierto - calcular días hasta hoy
                from datetime import datetime, timezone
                inicio = p["started_at"]
                if inicio.tzinfo is None:
                    inicio = inicio.replace(tzinfo=timezone.utc)
                dias += (datetime.now(timezone.utc) - inicio).days
            dias_por_ubicacion[loc] = dias_por_ubicacion.get(loc, 0) + dias

        return {
            "timeline": [dict(m) for m in movimientos],
            "periodos_instalacion": [dict(p) for p in periodos],
            "reparaciones": [dict(r) for r in reparaciones],
            "dias_por_ubicacion": dias_por_ubicacion,
            "dias_instalado_actual": dias_instalado_actual,
            "total_movimientos": len(movimientos),
            "total_reparaciones": len(reparaciones),
        }


def _mensaje_de_regla(original: Exception) -> str:
    """
    Saca el mensaje del trigger de la excepcion de Postgres.

    `psycopg2` pone el texto de `RAISE EXCEPTION` en `diag.message_primary`, que
    es donde queda el mensaje que escribio el trigger, ya en castellano y ya
    explicado. Si no esta (una excepcion de otro tipo), se cae al `str`, que
    trae el SQLSTATEsquema completo y el mensaje concatenado.
    """
    mensaje = getattr(getattr(original, "diag", None), "message_primary", None)
    return mensaje or str(original)


def _validar_geo(
    ubicacion_id: int | None, latitude: float | None, longitude: float | None
) -> dict[str, Any]:
    """
    Calcula si el escaneo ocurrio dentro del radio de la sede.

    POR QUE SE RECALCULA Y NO SE CONFIA EN EL CLIENTE
    ------------------------------------------------
    Las coordenadas llegan del GPS del telefono, que es un dato del cliente: un
    tunel, un sensor con deriva o alguien que quiere registrar un escaneo desde
    la casa. La distancia se recalcula contra `locations.latitude/longitude`, y
    lo que se guarda (`is_valid_location`) es el veredicto del servidor.

    LO QUE SE HACE CUANDO NO HAY COORDENADAS
    -----------------------------------------
    `dentro_del_radio=None` y NO `False`. La diferencia es la que separa "no
    estabas ahi" de "no se puede saber": un `False` sin coordenadas convierte
    cada escaneo de un movil sin GPS (o con permiso denegado) en una
    inspeccion, y el operador deja de mirar esa columna porque siempre dice lo
    mismo.
    """
    if latitude is None and longitude is None:
        return {"dentro_del_radio": None, "distancia_metros": None, "radio_metros": None}

    if latitude is None or longitude is None:
        raise ErrorDeEscaneo(
            "Falta una de las dos coordenadas. Si mandas la ubicacion del "
            "escaneo, tiene que ir latitud Y longitud."
        )

    # Se normaliza aca para que un NaN o una latitud de 400 salten con el error
    # de geo, no con uno de Postgres tres capas mas abajo.
    lat, lng = normalizar_coordenada(latitude, longitude)

    if ubicacion_id is None:
        return {"dentro_del_radio": None, "distancia_metros": None, "radio_metros": None}

    engine = get_ai_engine()
    with engine.connect() as conexion:
        sede = conexion.execute(
            text(
                """
                SELECT latitude, longitude, tolerance_radius_meters, store_name
                FROM locations
                WHERE id = :id AND is_active AND deleted_at IS NULL
                """
            ),
            {"id": ubicacion_id},
        ).mappings().first()

    if sede is None:
        raise ErrorDeEscaneo(
            f"La ubicacion {ubicacion_id} no existe o esta dada de baja."
        )

    if sede["latitude"] is None or sede["longitude"] is None:
        # La sede no tiene coordenadas cargadas. No se puede verificar nada, y
        # decir "dentro" seria afirmar algo que no se sabe.
        return {
            "dentro_del_radio": None,
            "distancia_metros": None,
            "radio_metros": sede["tolerance_radius_meters"],
            "sede_sin_coordenadas": sede["store_name"],
        }

    dentro, metros = dentro_del_radio(
        lat, lng, sede["latitude"], sede["longitude"], sede["tolerance_radius_meters"]
    )

    return {
        "dentro_del_radio": dentro,
        "distancia_metros": metros,
        "radio_metros": sede["tolerance_radius_meters"],
        "sede": sede["store_name"],
    }


def _mensaje_geo(geo: dict[str, Any]) -> str:
    """El texto que ve el operador, distinto segun que se haya podido saber."""
    if geo.get("dentro_del_radio") is None:
        if geo.get("sede_sin_coordenadas"):
            return (
                f"La sede '{geo['sede_sin_coordenadas']}' no tiene coordenadas "
                "cargadas, asi que no se pudo validar donde estas. Cargalas en la sede."
            )
        return "Escaneo registrado. Sin coordenadas no se pudo validar la ubicacion."

    if geo["dentro_del_radio"]:
        return f"Escaneo registrado en sede ({geo['distancia_metros']} m del centro)."

    return (
        f"ATENCION: escaneo registrado a {geo['distancia_metros']} m de la sede, "
        f"fuera del radio de {geo['radio_metros']} m. Queda marcado para revisar."
    )


def historial_dispositivo(device_id: int, limite: int = 50) -> list[dict[str, Any]]:
    """
    Movimientos de un componente, del mas nuevo al mas viejo.

    ES EL "DONDE ESTUVE". Es la respuesta a la pregunta que el inventario no
    puede dejar de dar: si un equipo desaparece, hay que poder decir por donde
    paso.
    """
    with get_ai_engine().connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT
                    mh.id,
                    mt.name AS movement_type,
                    mh.movement_date,
                    mh.scan_latitude,
                    mh.scan_longitude,
                    mh.is_valid_location,
                    mh.observations,
                    mh.replacement_reason,
                    lo.store_name AS origen,
                    ld.store_name AS destino,
                    u.full_name    AS registrado_por,
                    d.serial_number AS equipo_reemplazado
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                LEFT JOIN locations lo ON lo.id = mh.origin_location_id
                LEFT JOIN locations ld ON ld.id = mh.destination_location_id
                LEFT JOIN users u      ON u.id = mh.user_id
                LEFT JOIN devices d    ON d.id = mh.replaced_device_id
                WHERE mh.device_id = :id
                ORDER BY mh.movement_date DESC, mh.id DESC
                LIMIT :limite
                """
            ),
            {"id": device_id, "limite": limite},
        ).mappings().all()

    return [dict(f) for f in filas]


def listar_movimientos(
    *,
    device_id: int | None = None,
    usuario_id: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    busqueda: str | None = None,
    tipo_movimiento: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Listado paginado de movimientos, para la pantalla de actividad.

    EL BUSCADOR
    -----------
    Sin busqueda, el historial es una lista infinita: con 5000 movimientos,
    el operador ve los ultimos 50 y no llega a los de ayer. Con el buscador
    (serial, SKU, modelo o ubicacion) encuentra el movimiento que necesita,
    y con el rango de fechas acota el periodo.

    La busqueda es por texto con `ILIKE` y no con el indice de texto completo
    de `devices`: el historial se busca por un fragmento de serial ("SN-01")
    que no es una palabra del diccionario, y el `ILIKE` con el prefijo
    encuentra.
    """
    condiciones: list[str] = ["1=1"]
    parametros: dict[str, Any] = {"limite": limite, "offset": offset}

    if device_id:
        condiciones.append("mh.device_id = :device_id")
        parametros["device_id"] = device_id
    if usuario_id:
        condiciones.append("mh.user_id = :usuario_id")
        parametros["usuario_id"] = usuario_id
    if desde:
        condiciones.append("mh.movement_date >= :desde")
        parametros["desde"] = desde
    if hasta:
        condiciones.append("mh.movement_date <= :hasta")
        parametros["hasta"] = hasta
    if tipo_movimiento:
        condiciones.append("mt.code = :tipo_movimiento")
        parametros["tipo_movimiento"] = tipo_movimiento
    if busqueda:
        # El `%` y el `_` del texto se escapan: si el operador escribe "50%",
        # no es un comodin, es un 50 y un porcentaje.
        texto = busqueda.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        condiciones.append(
            "(d.serial_number ILIKE :busqueda "
            " OR d.qr_code ILIKE :busqueda "
            " OR mo.sku ILIKE :busqueda "
            " OR mo.model ILIKE :busqueda "
            " OR mo.brand ILIKE :busqueda "
            " OR lo.store_name ILIKE :busqueda "
            " OR ld.store_name ILIKE :busqueda)"
        )
        parametros["busqueda"] = f"%{texto}%"

    where = " AND ".join(condiciones)

    with get_ai_engine().connect() as conexion:
        filas = conexion.execute(
            text(
                f"""
                SELECT
                    mh.id, mt.name AS movement_type, mh.movement_date,
                    mh.is_valid_location,
                    d.serial_number, d.qr_code,
                    mo.sku, mo.brand, mo.model,
                    lo.store_name AS origen, ld.store_name AS destino,
                    u.full_name AS registrado_por
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                JOIN devices d ON d.id = mh.device_id
                LEFT JOIN models mo ON mo.id = d.model_id
                LEFT JOIN locations lo ON lo.id = mh.origin_location_id
                LEFT JOIN locations ld ON ld.id = mh.destination_location_id
                LEFT JOIN users u ON u.id = mh.user_id
                WHERE {where}
                ORDER BY mh.movement_date DESC, mh.id DESC
                LIMIT :limite OFFSET :offset
                """
            ),
            parametros,
        ).mappings().all()

        total = conexion.execute(
            text(
                f"""
                SELECT COUNT(*) AS n
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                JOIN devices d ON d.id = mh.device_id
                LEFT JOIN models mo ON mo.id = d.model_id
                LEFT JOIN locations lo ON lo.id = mh.origin_location_id
                LEFT JOIN locations ld ON ld.id = mh.destination_location_id
                WHERE {where}
                """
            ),
            parametros,
        ).scalar_one()

    return {
        "items": [dict(f) for f in filas],
        "total": int(total or 0),
        "limit": limite,
        "offset": offset,
    }
