"""
Servicio de incidencias.

QUE ES UNA INCIDENCIA
---------------------
El reporte de un problema: "este equipo no prende", "el cliente no tiene
internet". Es la unidad de trabajo del dia a dia, y tiene tres cosas que la
diferencian de un ticket generico:

  1. UN SLA POR SEVERIDAD, con los plazos calculados y guardados.
  2. UNA MAQUINA DE ESTADOS validada por la base.
  3. UN HISTORIAL INMUTABLE que dice que paso, cuando y quien lo hizo.

EL ACTOR DE CADA EVENTO
-----------------------
La linea de tiempo no puede confiar en el `actor_id` que manda el cliente: un
historial de soporte es una prueba, y una prueba no la escribe el interesado.
Por eso el service setea `app.actor_id` en el CONTEXTO de la transaccion
(`SET LOCAL`), que lo lee el trigger de la base. El cliente no puede
inventarse un actor.

LA IDEMPOTENCIA
---------------
La app es una PWA con cobertura mala: el mismo alta puede llegar dos veces.
`client_ref` es UNIQUE, y un alta repetida devuelve LA MISMA incidencia con
`200` en vez de crear un caso duplicado. Es el mismo patron que el
`client_scan_id` del escaner.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.modules.ai.executor import get_ai_engine


class IncidenciaInvalida(ValueError):
    """Regla de negocio de incidencias. 409."""


class IncidenciaNoEncontrada(LookupError):
    """La incidencia no existe. 404."""


class ConflictoDeVersion(LookupError):
    """
    Alguien edito la incidencia antes que este cliente. 409.

    El bloqueo optimista: el cliente manda la `version` que leyo, y si la base
    tiene otra, otro operador guardo en el medio. Pisar su trabajo sin
    avisar es peor que un error.
    """


# Los estados en los que la incidencia sigue viva.
ESTADOS_ACTIVOS = ("OPEN", "IN_PROGRESS", "WAITING")
ESTADOS_CERRADOS = ("RESOLVED", "CLOSED", "CANCELLED")


def _a_resumen(fila: dict[str, Any]) -> dict[str, Any]:
    """Normaliza los tipos que JSON no maneja (NUMERIC y EXTRACT son Decimal)."""
    datos = dict(fila)
    for clave in ("cost", "minutos_para_resolver", "minutos_para_responder"):
        if datos.get(clave) is not None and not isinstance(datos[clave], (int, float)):
            datos[clave] = float(datos[clave])
    return datos


def listar_incidencias(
    *,
    estado: str | None = None,
    severidad: str | None = None,
    categoria: str | None = None,
    asignado_a: str | None = None,
    device_id: int | None = None,
    model_id: int | None = None,
    location_id: int | None = None,
    solo_vencidas: bool = False,
    solo_abiertas: bool = False,
    solo_sin_asignar: bool = False,
    fuera_de_sla: bool = False,
    solo_mias: bool = False,
    sin_atender: bool = False,
    mi_usuario: str | None = None,
    busqueda: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Listado paginado, con filtros combinables.

    La busqueda es de TEXTO COMPLETO (Postgres FTS en español), no un ILIKE:
    "notebook dell" tiene que encontrar "Dell Latitude 5520", cosa que un
    `ILIKE('%notebook dell%')` no puede hacer nunca.
    """
    condiciones = ["i.deleted_at IS NULL"]
    parametros: dict[str, Any] = {"limite": limite, "offset": offset}

    if estado:
        condiciones.append("i.status = :estado")
        parametros["estado"] = estado
    if severidad:
        condiciones.append("i.severity = :severidad")
        parametros["severidad"] = severidad
    if categoria:
        condiciones.append("i.category = :categoria")
        parametros["categoria"] = categoria
    if asignado_a:
        condiciones.append("i.assigned_to = :asignado_a")
        parametros["asignado_a"] = asignado_a
    if device_id:
        condiciones.append("i.device_id = :device_id")
        parametros["device_id"] = device_id
    if model_id:
        condiciones.append("i.model_id = :model_id")
        parametros["model_id"] = model_id
    if location_id:
        condiciones.append("i.location_id = :location_id")
        parametros["location_id"] = location_id
    if solo_vencidas:
        # Vencida = sin resolver y con el plazo de resolucion pasado.
        condiciones.append("i.resolved_at IS NULL AND i.resolution_due_at < NOW()")
    if solo_abiertas:
        # Las que siguen vivas: las tres del ciclo de trabajo. Es el filtro que
        # usa el tablero del Helpdesk para mostrar "los casos que vencen".
        condiciones.append("i.status IN ('OPEN', 'IN_PROGRESS', 'WAITING')")
    if solo_sin_asignar:
        # Nadie las todavia: son las que se van a perder, porque un caso sin
        # responsable no lo mira nadie.
        condiciones.append("i.assigned_to IS NULL AND i.status IN ('OPEN', 'IN_PROGRESS', 'WAITING')")
    if fuera_de_sla:
        # Se resolvieron despues del plazo: el SLA incumplido, que es lo que se
        # mide despues para evaluar al taller.
        condiciones.append("i.resolved_at IS NOT NULL AND i.resolved_at > i.resolution_due_at")
    if sin_atender:
        # EL BACKLOG: los casos que NADIE TOMO. No es lo mismo que "sin
        # asignar": un caso asignado a alguien que lo tiene abandonado sigue
        # sin atendido, y es el que de verdad se pierde. El backlog mira la
        # PRIMERA RESPUESTA, no la asignacion.
        condiciones.append(
            "i.first_response_at IS NULL AND i.status IN ('OPEN', 'IN_PROGRESS', 'WAITING')"
        )
    if solo_mias:
        # "MIS CASOS": los que tiene asignados el usuario, sin importar el
        # estado. Sin el id del usuario no se puede filtrar, y un filtro que
        # se ignora en silencio es peor que no tenerlo.
        if not mi_usuario:
            raise IncidenciaInvalida("No se puede filtrar por mis casos sin saber quien pregunta.")
        condiciones.append("i.assigned_to = :mi_usuario")
        parametros["mi_usuario"] = mi_usuario
    if busqueda:
        condiciones.append(
            "(i.title ILIKE :busqueda OR i.description ILIKE :busqueda OR i.code ILIKE :busqueda "
            "OR to_tsvector('spanish', coalesce(i.title, '') || ' ' || coalesce(i.description, '')) "
            "@@ plainto_tsquery('spanish', :busqueda))"
        )
        parametros["busqueda"] = f"%{_escapar_like(busqueda)}%"

    where = " AND ".join(condiciones)

    engine = get_ai_engine()
    with engine.connect() as conexion:
        filas = conexion.execute(
            text(
                f"""
                SELECT
                    i.id, i.code, i.title, i.severity, i.status, i.category,
                    i.device_id, i.model_id, i.location_id,
                    i.reported_by, i.assigned_to,
                    i.created_at, i.updated_at,
                    i.response_due_at, i.resolution_due_at,
                    i.first_response_at, i.resolved_at, i.closed_at,
                    i.version,
                    s.minutos_para_resolver, s.vencido, s.fuera_de_sla
                FROM incidents i
                LEFT JOIN v_incident_sla s ON s.id = i.id
                WHERE {where}
                ORDER BY
                    -- Las vencidas primero, y dentro de cada grupo, las mas
                    -- graves y las mas antiguas: ese es el orden en el que un
                    -- operador tiene que atacarlas.
                    (i.resolved_at IS NULL AND i.resolution_due_at < NOW()) DESC,
                    i.severity ASC,
                    i.created_at ASC
                LIMIT :limite OFFSET :offset
                """
            ),
            parametros,
        ).mappings().all()

        total = conexion.execute(
            text(f"SELECT COUNT(*) AS n FROM incidents i WHERE {where}"),
            parametros,
        ).scalar_one()

    return {
        "items": [_a_resumen(dict(f)) for f in filas],
        "total": int(total or 0),
        "limite": limite,
        "offset": offset,
    }


def obtener_incidencia(
    incidencia_id: UUID, *, limite_eventos: int = 100
) -> dict[str, Any]:
    """La incidencia con su linea de tiempo completa."""
    engine = get_ai_engine()
    with engine.connect() as conexion:
        fila = conexion.execute(
            text(
                """
                SELECT
                    i.*, s.minutos_para_resolver, s.vencido, s.fuera_de_sla
                FROM incidents i
                LEFT JOIN v_incident_sla s ON s.id = i.id
                WHERE i.id = :id AND i.deleted_at IS NULL
                """
            ),
            {"id": str(incidencia_id)},
        ).mappings().first()

        if not fila:
            raise IncidenciaNoEncontrada("No existe esa incidencia.")

        # LA LINEA DE TIEMPO, ACOTADA
        #
        # Un caso abierto durante años tiene miles de eventos, y la pantalla
        # los pinta TODOS: el modal se vuelve una lista interminable y el
        # operador tiene que scrollear hasta el principio para ver el
        # ultimo cambio (que es el que importa).
        #
        # Se traen los ULTIMOS y se dice cuantos hay en total: el que quiere
        # el historial completo lo pide por la API.
        eventos = conexion.execute(
            text(
                """
                SELECT id, event_type, from_status, to_status, actor_id, note, payload, created_at
                FROM incident_events
                WHERE incident_id = :id
                ORDER BY created_at DESC, id DESC
                LIMIT :limite
                """
            ),
            {"id": str(incidencia_id), "limite": limite_eventos},
        ).mappings().all()

        total_eventos = conexion.execute(
            text(
                """
                SELECT COUNT(*) FROM incident_events WHERE incident_id = :id
                """
            ),
            {"id": str(incidencia_id)},
        ).scalar_one()

    datos = _a_resumen(dict(fila))
    # Del mas nuevo al mas viejo: lo que paso ultimo es lo que se mira
    # primero. La linea de tiempo del modal ya la muestra asi.
    datos["eventos"] = [dict(e) for e in eventos]
    datos["total_eventos"] = int(total_eventos or 0)
    datos["eventos_omitidos"] = max(0, int(total_eventos or 0) - len(eventos))
    return datos


def crear_incidencia(datos: dict[str, Any], *, user_id: UUID, client_ref: str | None = None) -> dict[str, Any]:
    """
    Alta de una incidencia.

    IDEMPOTENTE
    ------------
    Si viene `client_ref` y ya existe una incidencia con ese ref, se devuelve
    LA MISMA. La app reintenta el alta cuando vuelve la red, y sin esto un
    corte de conexion a mitad del envio deja dos casos iguales.
    """
    engine = get_ai_engine()

    if client_ref:
        with engine.connect() as conexion:
            previa = conexion.execute(
                text(
                    """
                    SELECT id FROM incidents
                    WHERE client_ref = :client_ref AND deleted_at IS NULL
                    """
                ),
                {"client_ref": client_ref},
            ).mappings().first()
        if previa:
            return obtener_incidencia(UUID(str(previa["id"])))

    try:
        with engine.begin() as conexion:
            # El actor, para el evento de alta.
            conexion.execute(
                text("SELECT set_config('app.actor_id', :actor, true)"),
                {"actor": str(user_id)},
            )

            incidencia_id = conexion.execute(
                text(
                    """
                    INSERT INTO incidents (
                        title, description, severity, category,
                        device_id, model_id, location_id,
                        reported_by, assigned_to,
                        client_ref, cost, downtime_minutes
                    )
                    VALUES (
                        :title, :description, :severity, :category,
                        :device_id, :model_id, :location_id,
                        :reported_by, :assigned_to,
                        :client_ref, :cost, :downtime_minutes
                    )
                    RETURNING id
                    """
                ),
                {
                    "title": datos["title"],
                    "description": datos.get("description"),
                    "severity": datos.get("severity") or "S3",
                    "category": datos.get("category") or "OTHER",
                    "device_id": datos.get("device_id"),
                    "model_id": datos.get("model_id"),
                    "location_id": datos.get("location_id"),
                    "reported_by": str(user_id),
                    "assigned_to": _como_uuid(datos.get("assigned_to")),
                    "client_ref": client_ref,
                    "cost": datos.get("cost"),
                    "downtime_minutes": datos.get("downtime_minutes"),
                },
            ).scalar_one()

            # El evento de alta lo escribe el trigger `incident_registra_creacion`:
            # asi queda en el historial aunque la fila se cree por otro camino
            # (un script, una migracion, un `psql`).
    except IntegrityError as exc:
        detalle = str(getattr(exc, "orig", exc))
        if "client_ref" in detalle:
            # Dos altas simultaneas con el mismo ref: se devuelve la que
            # quedo, en vez de un error.
            with engine.connect() as conexion:
                fila = conexion.execute(
                    text("SELECT id FROM incidents WHERE client_ref = :ref"),
                    {"ref": client_ref},
                ).mappings().first()
            if fila:
                return obtener_incidencia(UUID(str(fila["id"])))
        if "fk_incidents" in detalle or "violates foreign key" in detalle:
            raise IncidenciaInvalida(
                "El equipo, el producto o la ubicacion no existen."
            ) from exc
        raise

    return obtener_incidencia(UUID(str(incidencia_id)))


def actualizar_incidencia(
    incidencia_id: UUID,
    datos: dict[str, Any],
    *,
    user_id: UUID,
) -> dict[str, Any]:
    """
    Edicion de una incidencia, con bloqueo optimista.

    `version` es la version que el cliente leyo. Si la base tiene otra, otro
    operador guardo en el medio: se responde 409 con un conflicto explicito, en
    vez de pisar su trabajo.
    """
    engine = get_ai_engine()
    campos: dict[str, Any] = {}
    mapeo = {
        "title": "title",
        "description": "description",
        "severity": "severity",
        "category": "category",
        "assigned_to": "assigned_to",
        "location_id": "location_id",
        "cost": "cost",
        "downtime_minutes": "downtime_minutes",
        "root_cause": "root_cause",
        "resolution": "resolution",
    }
    for clave, columna in mapeo.items():
        if clave in datos and datos[clave] is not None:
            campos[columna] = (
                str(_como_uuid(datos[clave])) if clave == "assigned_to" else datos[clave]
            )

    if not campos:
        return obtener_incidencia(incidencia_id)

    engine = get_ai_engine()
    with engine.begin() as conexion:
        conexion.execute(
            text("SELECT set_config('app.actor_id', :actor, true)"),
            {"actor": str(user_id)},
        )

        asignaciones = ", ".join(f"{columna} = :{columna}" for columna in campos)
        params = {**campos, "id": str(incidencia_id), "version": datos["version"]}
        params["actor"] = str(user_id)

        resultado = conexion.execute(
            text(
                f"""
                UPDATE incidents
                SET {asignaciones}, version = version + 1
                WHERE id = :id AND version = :version AND deleted_at IS NULL
                """
            ),
            params,
        )

        if resultado.rowcount == 0:
            # O no existe, o la version no es la que el cliente leyo.
            existe = conexion.execute(
                text("SELECT version FROM incidents WHERE id = :id AND deleted_at IS NULL"),
                {"id": str(incidencia_id)},
            ).mappings().first()
            if not existe:
                raise IncidenciaNoEncontrada("No existe esa incidencia.")
            raise ConflictoDeVersion(
                "Alguien modifico esta incidencia mientras la editabas. "
                "Recargala para ver los cambios."
            )

        # Los cambios que quedan en el historial.
        if "severity" in campos:
            conexion.execute(
                text(
                    """
                    INSERT INTO incident_events (incident_id, event_type, actor_id, payload)
                    VALUES (:id, 'SEVERITY_CHANGED', :actor, :payload)
                    """
                ),
                {
                    "id": str(incidencia_id),
                    "actor": str(user_id),
                    "payload": json.dumps({"severidad": campos["severity"]}),
                },
            )
        if "assigned_to" in campos:
            conexion.execute(
                text(
                    """
                    INSERT INTO incident_events (incident_id, event_type, actor_id, payload)
                    VALUES (:id, 'ASSIGNED', :actor, :payload)
                    """
                ),
                {
                    "id": str(incidencia_id),
                    "actor": str(user_id),
                    "payload": json.dumps({"responsable": campos["assigned_to"]}),
                },
            )

    return obtener_incidencia(incidencia_id)


def cambiar_estado(
    incidencia_id: UUID,
    nuevo_estado: str,
    *,
    user_id: UUID,
    nota: str | None = None,
) -> dict[str, Any]:
    """
    Cambia el estado de una incidencia.

    La TRANSICION la valida la base (trigger): si el par actual->nuevo no esta
    permitido, el UPDATE falla con el error de la base, que se traduce a un
    409 con el motivo. La API no reimplementa la maquina de estados: dos
    implementaciones se desincronizan, y la que queda sin actualizado es la
    que deja pasar lo imposible.
    """
    engine = get_ai_engine()
    with engine.begin() as conexion:
        conexion.execute(
            text("SELECT set_config('app.actor_id', :actor, true)"),
            {"actor": str(user_id)},
        )
        conexion.execute(
            text("SELECT set_config('app.actor_note', :nota, true)"),
            {"nota": nota or ""},
        )

        try:
            resultado = conexion.execute(
                text(
                    """
                    UPDATE incidents
                    SET status = :estado, version = version + 1
                    WHERE id = :id AND deleted_at IS NULL
                    """
                ),
                {"estado": nuevo_estado, "id": str(incidencia_id)},
            )
        except Exception as exc:  # noqa: BLE001 - se re-traslata abajo
            mensaje = str(getattr(exc, "orig", exc))
            if "Transicion de incidencia no valida" in mensaje:
                raise IncidenciaInvalida(
                    "Ese cambio de estado no esta permitido. "
                    "Revisá el estado actual de la incidencia."
                ) from exc
            raise

        if resultado.rowcount == 0:
            raise IncidenciaNoEncontrada("No existe esa incidencia.")

    return obtener_incidencia(incidencia_id)


def comentar(incidencia_id: UUID, nota: str, *, user_id: UUID) -> dict[str, Any]:
    """Agrega una nota a la linea de tiempo."""
    engine = get_ai_engine()
    with engine.begin() as conexion:
        existe = conexion.execute(
            text("SELECT 1 FROM incidents WHERE id = :id AND deleted_at IS NULL"),
            {"id": str(incidencia_id)},
        ).mappings().first()
        if not existe:
            raise IncidenciaNoEncontrada("No existe esa incidencia.")

        conexion.execute(
            text(
                """
                INSERT INTO incident_events (incident_id, event_type, actor_id, note)
                VALUES (:id, 'COMMENT', :actor, :nota)
                """
            ),
            {"id": str(incidencia_id), "actor": str(user_id), "nota": nota},
        )

    return obtener_incidencia(incidencia_id)


# ===========================================================================
# EL FLUJO DEL CASO: BANDEJA, TOMA Y CIERRE
# ===========================================================================
#
# QUE ES UNA BANDEJA
# ------------------
# Los casos reportados que NADIE TOMO. No es un filtro cosmetico: es el
# trabajo sin dueño, que es lo unico que se pierde. La bandeja se mira al
# empezar el turno y se vacia con un clic ("Tomar el caso").
#
# POR QUE "TOMAR" Y NO "ASIGNAR"
# ------------------------------
# Asignar es una decision del supervisor: "este caso es de Juan". Tomar es
# una decision del tecnico: "este caso es mio". Con las dos, el supervisor
# puede repartir el trabajo Y el tecnico puede trabajar sin pedir permiso.
#
# EL AVISO AL QUE REPORTO
# -----------------------
# El que reporto el caso quiere saber que paso con el. Si nadie le dice
# nada, deja de reportar (o reporta por telefono). Por eso, cuando el caso
# se toma y cuando se resuelve, el reportador recibe un aviso en su campana.


# Los destinos posibles del EQUIPO al cerrar el caso. Son los del inventario:
# el caso no inventa estados, usa los del stock.
DESTINOS_EQUIPO = ("NINGUNO", "TALLER", "REPARADO", "DANO_TOTAL")


def _notificar_reportador(
    conexion,
    *,
    incidencia: dict[str, Any],
    user_id: UUID,
    tipo: str,
    titulo: str,
    mensaje: str,
    clave: str,
) -> int:
    """
    Avisa al que reporto el caso.

    Si el que reporto es el mismo que esta operando, no se avisa: uno no se
    notifica a si mismo.
    """
    reportador = incidencia.get("reported_by")
    if not reportador or str(reportador) == str(user_id):
        return 0

    return _crear_notificaciones(
        conexion,
        incidencia_id=incidencia["id"],
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje,
        destinatarios=[UUID(str(reportador))],
        clave=clave,
    )


def tomar_caso(incidencia_id: UUID, *, user_id: UUID) -> dict[str, Any]:
    """
    Toma un caso de la bandeja: queda asignado a quien lo toma.

    QUE HACE
    --------
    1. Lo asigna al usuario.
    2. Arranca el reloj de respuesta (`first_response_at`), si no habia
       arrancado: el SLA se mide desde la PRIMERA RESPUESTA, y el momento de
       tomar el caso es la respuesta.
    3. Lo pasa a "en curso" (de la bandeja no se queda parado en "abierta").
    4. Avisa al que lo reporto.

    LA CONCURRENCIA
    ---------------
    Dos tecnicos pueden tocar el mismo caso en el mismo segundo. El `FOR
    UPDATE` serializa: el segundo ve que ya esta asignado y recibe un 409
    con un mensaje claro, en vez de stealearle el caso.
    """
    engine = get_ai_engine()
    with engine.begin() as conexion:
        conexion.execute(
            text("SELECT set_config('app.actor_id', :actor, true)"),
            {"actor": str(user_id)},
        )

        fila = conexion.execute(
            text(
                """
                SELECT id, code, title, severity, status, assigned_to, reported_by
                FROM incidents
                WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
                """
            ),
            {"id": str(incidencia_id)},
        ).mappings().first()

        if not fila:
            raise IncidenciaNoEncontrada("No existe esa incidencia.")

        caso = dict(fila)

        if caso["status"] in ("RESOLVED", "CLOSED", "CANCELLED"):
            raise IncidenciaInvalida("Ese caso ya esta cerrado: no se puede tomar.")

        if caso["assigned_to"] and str(caso["assigned_to"]) != str(user_id):
            raise IncidenciaInvalida(
                "Otro tecnico ya tomo este caso. Recargá la bandeja para ver el estado real."
            )

        conexion.execute(
            text(
                """
                UPDATE incidents
                SET assigned_to = :usuario,
                    first_response_at = COALESCE(first_response_at, NOW()),
                    status = CASE WHEN status = 'OPEN' THEN 'IN_PROGRESS' ELSE status END,
                    version = version + 1
                WHERE id = :id
                """
            ),
            {"usuario": str(user_id), "id": str(incidencia_id)},
        )

        conexion.execute(
            text(
                """
                INSERT INTO incident_events (incident_id, event_type, actor_id, note, payload)
                VALUES (:id, 'CLAIMED', :actor, :nota, :payload)
                """
            ),
            {
                "id": str(incidencia_id),
                "actor": str(user_id),
                "nota": "El caso se tomo de la bandeja.",
                "payload": json.dumps({"responsable": str(user_id)}),
            },
        )

        _notificar_reportador(
            conexion,
            incidencia=caso,
            user_id=user_id,
            tipo="CASE_CLAIMED",
            titulo=f"{caso['code']} fue tomado por un tecnico",
            mensaje=(
                f"'{caso['title']}' ya esta en manos del taller. "
                "Te avisamos cuando se resuelva."
            ),
            clave=f"CASE_CLAIMED:{caso['id']}",
        )

    return obtener_incidencia(incidencia_id)


def _validar_destino_equipo(device_id: int, destino: str) -> str:
    """
    Revisa que el destino sea posible para el estado ACTUAL del equipo.

    Devuelve el estado actual del equipo (o None si el equipo no existe).
    """
    from src.modules.devices import service as devices_service

    try:
        equipo = devices_service.obtener_dispositivo(device_id)
    except devices_service.DispositivoNoEncontrado as exc:
        raise IncidenciaInvalida(
            "El equipo del caso no existe: no se puede aplicarle el destino."
        ) from exc

    estado_actual = equipo.get("current_status")

    if destino == "TALLER" and estado_actual not in ("IN_WAREHOUSE", "IN_TRANSIT", "INSTALLED", "IN_REPAIR"):
        raise IncidenciaInvalida(
            f"El equipo esta en {estado_actual}: no se puede enviar al taller desde ahi."
        )

    if destino == "REPARADO" and estado_actual not in ("IN_REPAIR", "IN_TRANSIT"):
        raise IncidenciaInvalida(
            "Solo un equipo en taller o en transito puede volver al deposito. "
            "Un equipo instalado en el cliente vuelve por escaneo, no por el caso."
        )

    return estado_actual


def _aplicar_destino_equipo(
    device_id: int,
    destino: str,
    *,
    user_id: UUID,
    codigo: str,
    motivo: str,
    incidencia_id: UUID | None = None,
) -> str:
    """
    Mueve el equipo del caso segun como termino.

    QUE SIGNIFICA CADA DESTINO
    --------------------------
    - TALLER: el equipo esta roto y va al taller. Sale del servicio: pasa a
      `IN_REPAIR` y deja de contar como disponible.
    - REPARADO: se arreglo y vuelve al deposito. Pasa a `IN_WAREHOUSE` y
      vuelve a contar como stock.
    - DANO_TOTAL: no se arregla. El equipo se desincorpora del stock (baja
      logica con certificado, como cualquier baja) y desaparece de los KPIs
      de stock.

    POR QUE NO SE TOCA EL INVENTARIO "A MANO"
    ----------------------------------------
    El estado del equipo se cambia con el service de dispositivos, que es el
    que valida la transicion contra la matriz de estados y el que deja el
    rastro. Si el Service Desk escribiera en la tabla `devices` por su
    cuenta, un caso podria dejar el equipo en un estado que el inventario no
    acepta, y los dos modulos contarian historias distintas.
    """
    from src.modules.devices import service as devices_service
    from src.modules.repairs import service as repairs_service

    if destino == "TALLER":
        # EL INGRESO AL TALLER
        #
        # No alcanza con cambiar el estado del equipo: la base exige que haya
        # una orden de trabajo ABIERTA para ponerlo en `IN_REPAIR` (un equipo
        # "en el taller" sin orden es un equipo que el taller no sabe que
        # tiene). La orden se abre ACÁ, ligada a este caso: asi se puede
        # responder "de que se estaba quejar este equipo" con un JOIN.
        orden = repairs_service.ingreso_taller(
            device_id,
            technician_id=user_id,
            diagnostico=motivo or f"{codigo}: ingreso al taller.",
            incident_id=incidencia_id,
        )
        return (
            "El equipo quedo en el taller (IN_REPAIR) "
            f"con la orden de trabajo #{orden.get('id')}."
        )

    if destino == "REPARADO":
        # Se cierra la orden de trabajo (si habia una) y el equipo vuelve al
        # deposito.
        repairs_service.cerrar_reparacion(
            device_id,
            technician_id=user_id,
            trabajo=motivo or "Reparado.",
            reparado=True,
        )
        devices_service.actualizar_dispositivo(
            device_id, {"current_status": "IN_WAREHOUSE"}
        )
        return "El equipo se reparo y volvio al deposito (IN_WAREHOUSE)."

    if destino == "DANO_TOTAL":
        # El taller deja la orden como "no reparable" y el equipo sale del
        # stock con su certificado de baja.
        orden = repairs_service.cerrar_reparacion(
            device_id,
            technician_id=user_id,
            trabajo=motivo or "Dano total: no reparable.",
            reparado=False,
        )

        if not orden:
            # El equipo NUNCA entro al taller (se reporto roto y ya no hadia
            # caso). Se registra igual la orden, cerrada como no reparable:
            # el certificado de baja tiene que apuntar a la reparacion que
            # demostro que no se podia arreglar. Un certificado que dice "se
            # dio de baja" sin decir por que no es una prueba.
            abierta = repairs_service.ingreso_taller(
                device_id,
                technician_id=user_id,
                diagnostico=motivo or "Dano total.",
                incident_id=incidencia_id,
            )
            orden = repairs_service.cerrar_reparacion(
                device_id,
                technician_id=user_id,
                trabajo=motivo or "Dano total: no reparable.",
                reparado=False,
            ) or abierta
        devices_service.dar_de_baja(
            device_id,
            usuario_id=str(user_id),
            motivo=motivo or f"{codigo}: dano total.",
            repair_id=(orden or {}).get("id"),
        )
        return "El equipo fue dado de baja por dano total y salio del stock."

    return "El equipo no cambio de estado."


def cerrar_caso(
    incidencia_id: UUID,
    datos: dict[str, Any],
    *,
    user_id: UUID,
) -> dict[str, Any]:
    """
    Resuelve un caso, y dice que paso con el EQUIPO.

    POR QUE EL CIERRE LLEVA EL DESTINO DEL EQUIPO
    ---------------------------------------------
    Este no es un help desk de software: es el control de los equipos danados.
    Un caso de "la notebook no prende" no se cierra con un texto: se cierra
    con el equipo en el taller, reparado, o dado de baja por dano total. Sin
    eso, el inventario no sabe que el equipo salio del stock y el KPI de
    "en deposito" miente.

    EL AVISO AL QUE REPORTO
    -----------------------
    Al resolver, el reportador recibe el aviso: por eso puede dejar de
    preguntar por telefono "ya esta?".
    """
    resolucion = (datos.get("resolution") or "").strip()
    if not resolucion:
        raise IncidenciaInvalida("Escribi como se resolvio el caso.")

    destino = (datos.get("destino_equipo") or "NINGUNO").upper()
    if destino not in DESTINOS_EQUIPO:
        raise IncidenciaInvalida(
            f"Destino de equipo invalido. Validos: {', '.join(DESTINOS_EQUIPO)}."
        )

    estado = (datos.get("estado") or "RESOLVED").upper()

    engine = get_ai_engine()
    with engine.begin() as conexion:
        conexion.execute(
            text("SELECT set_config('app.actor_id', :actor, true)"),
            {"actor": str(user_id)},
        )

        fila = conexion.execute(
            text(
                """
                SELECT id, code, title, status, device_id, reported_by, assigned_to
                FROM incidents
                WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
                """
            ),
            {"id": str(incidencia_id)},
        ).mappings().first()

        if not fila:
            raise IncidenciaNoEncontrada("No existe esa incidencia.")

        caso = dict(fila)

        if destino != "NINGUNO" and not caso["device_id"]:
            raise IncidenciaInvalida(
                "El caso no tiene ningun equipo asociado: no hay a que aplicarle el destino."
            )

        # EL DESTINO SE VALIDA ANTES DE RESOLVER EL CASO
        #
        # Si el destino es invalido para el estado actual del equipo (por
        # ejemplo, "reparado" de un equipo que esta INSTALLED en el cliente: no
        # puede volver solo al deposito), el caso NO se resuelve. Resolver
        # primero y fallar despues deja el caso cerrado y el equipo donde
        # estaba: dos verdades sobre lo mismo, que es justo lo que este
        # Service Desk existe para evitar.
        if destino != "NINGUNO":
            _validar_destino_equipo(caso["device_id"], destino)

        try:
            conexion.execute(
                text(
                    """
                    UPDATE incidents
                    SET status = :estado,
                        resolution = :resolucion,
                        version = version + 1
                    WHERE id = :id
                    """
                ),
                {"estado": estado, "resolucion": resolucion, "id": str(incidencia_id)},
            )
        except Exception as exc:  # noqa: BLE001 - se re-traslata abajo
            mensaje = str(getattr(exc, "orig", exc))
            if "Transicion de incidencia no valida" in mensaje:
                raise IncidenciaInvalida(
                    "Ese cambio de estado no esta permitido. "
                    "Revisá el estado actual de la incidencia."
                ) from exc
            raise

        conexion.execute(
            text(
                """
                INSERT INTO incident_events (incident_id, event_type, actor_id, note, payload)
                VALUES (:id, 'RESOLVED', :actor, :nota, :payload)
                """
            ),
            {
                "id": str(incidencia_id),
                "actor": str(user_id),
                "nota": resolucion,
                "payload": json.dumps({"destino_equipo": destino}),
            },
        )

        _notificar_reportador(
            conexion,
            incidencia=caso,
            user_id=user_id,
            tipo="CASE_RESOLVED",
            titulo=f"{caso['code']} fue resuelta",
            mensaje=(
                f"'{caso['title']}' quedo resuelta. {resolucion}"
            ),
            clave=f"CASE_RESOLVED:{caso['id']}",
        )

    # El destino del equipo se aplica FUERA de la transaccion del caso: el
    # service de dispositivos abre su propia conexion, y anidar dos
    # transacciones sobre las mismas filas es como se produce un deadlock.
    mensaje_equipo = "El equipo no cambio de estado."
    if destino != "NINGUNO" and caso["device_id"]:
        mensaje_equipo = _aplicar_destino_equipo(
            caso["device_id"],
            destino,
            user_id=user_id,
            codigo=caso["code"],
            motivo=resolucion,
            incidencia_id=incidencia_id,
        )
        engine = get_ai_engine()
        with engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    INSERT INTO incident_events (incident_id, event_type, actor_id, note, payload)
                    VALUES (:id, 'DEVICE_DISPOSITION', :actor, :nota, :payload)
                    """
                ),
                {
                    "id": str(incidencia_id),
                    "actor": str(user_id),
                    "nota": mensaje_equipo,
                    "payload": json.dumps(
                        {"destino_equipo": destino, "device_id": caso["device_id"]}
                    ),
                },
            )

    detalle = obtener_incidencia(incidencia_id)
    detalle["mensaje_equipo"] = mensaje_equipo
    return detalle


def metricas(user_id: UUID | None = None) -> dict[str, Any]:
    """
    El resumen para el tablero: cuantas hay abiertas, cuantas vencidas, y
    cuanto se demora el taller.
    """
    engine = get_ai_engine()
    with engine.connect() as conexion:
        resumen = conexion.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('OPEN', 'IN_PROGRESS', 'WAITING')) AS abiertas,
                    COUNT(*) FILTER (WHERE status = 'OPEN') AS sin_asignar,
                    COUNT(*) FILTER (WHERE vencido) AS vencidas,
                    COUNT(*) FILTER (WHERE fuera_de_sla) AS fuera_de_sla,
                    COUNT(*) FILTER (WHERE respuesta_tardia) AS respuesta_tardia,
                    COUNT(*) FILTER (WHERE status = 'RESOLVED') AS resueltas,
                    COUNT(*) FILTER (WHERE status = 'CLOSED') AS cerradas,
                    COUNT(*) FILTER (WHERE severity = 'S1' AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING')) AS criticas_abiertas,
                    ROUND(AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600.0)
                        FILTER (WHERE resolved_at IS NOT NULL)::NUMERIC, 1) AS mttr_horas,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS creadas_30d,

                    -- EL BACKLOG
                    --
                    -- "Sin atender" son los casos ABIERTOS que nadie respondio
                    -- todavia (primera respuesta vacia). No es lo mismo que
                    -- "sin asignar": un caso asignado a alguien que lo tiene
                    -- abandonado sigue sin atendido, y es el que de verdad se
                    -- pierde. El backlog mira la primera respuesta, no la
                    -- asignacion.
                    COUNT(*) FILTER (
                        WHERE first_response_at IS NULL
                          AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING')
                    ) AS sin_atender,

                    -- La antiguedad del caso sin atender MAS VIEJO: el numero
                    -- que dice quantas horas lleva esperando el taller.
                    ROUND(MAX(EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600.0)
                        FILTER (
                            WHERE first_response_at IS NULL
                              AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING')
                        )::NUMERIC, 1) AS sin_atender_antiguedad_h,

                    -- Los que estan esperando algo (repuesto, aprobacion).
                    COUNT(*) FILTER (WHERE status = 'WAITING') AS en_espera
                FROM v_incident_sla
                """
            )
        ).mappings().one()

        por_severidad = conexion.execute(
            text(
                """
                SELECT severity, COUNT(*) AS cantidad
                FROM v_incident_sla
                WHERE status IN ('OPEN', 'IN_PROGRESS', 'WAITING')
                GROUP BY severity
                ORDER BY severity
                """
            )
        ).mappings().all()

    # "MIS CASOS": los que tiene asignados el usuario y siguen abiertos. Es
    # el segundo tablero del tecnico: no solo lo que hay que tomar, sino lo
    # que ya esta en sus manos.
    mias = 0
    if user_id:
        with engine.connect() as conexion:
            mias = int(
                conexion.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM incidents
                        WHERE deleted_at IS NULL
                          AND assigned_to = :usuario
                          AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING')
                        """
                    ),
                    {"usuario": str(user_id)},
                ).scalar_one()
                or 0
            )

    datos = dict(resumen)
    if datos.get("mttr_horas") is not None:
        datos["mttr_horas"] = float(datos["mttr_horas"])
    if datos.get("sin_atender_antiguedad_h") is not None:
        datos["sin_atender_antiguedad_h"] = float(datos["sin_atender_antiguedad_h"])
    datos["mias"] = mias
    return {"resumen": datos, "por_severidad": [dict(f) for f in por_severidad]}


def _como_uuid(valor: Any) -> UUID | None:
    """Un id de usuario que llega como texto (o None)."""
    if valor is None or valor == "":
        return None
    try:
        return UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        return None


def _escapar_like(texto: str) -> str:
    r"""Escapa los comodines de LIKE para que se busquen como texto literal."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ===========================================================================
# SLA OPERABLE: recordatorios, escalamiento y avisos
# ===========================================================================

# Cuanto antes del plazo se avisa. Es el "en 30 minutos vence esto".
MINUTOS_AVISO_POR_DEFECTO = 30


def _destinatarios(incidencia: dict[str, Any]) -> list[UUID]:
    """
    A quien le llega un aviso de este caso.

    - Si el caso tiene responsable, el aviso es para el.
    - Si no, el aviso es para quien puede resolverlo (el taller y los
      administradores): un aviso que le llega a nadie no es un aviso.
    """
    from src.core.permissions import Permiso, permisos_de

    if incidencia.get("assigned_to"):
        return [UUID(str(incidencia["assigned_to"]))]

    engine = get_ai_engine()
    with engine.connect() as conexion:
        filas = conexion.execute(
            text("SELECT id, role_id FROM users WHERE is_active = TRUE")
        ).mappings().all()

        roles = {
            str(r["id"]): r["name"]
            for r in conexion.execute(text("SELECT id, name FROM roles")).mappings().all()
        }

    destinatarios: list[UUID] = []
    for fila in filas:
        rol = roles.get(str(fila["role_id"]), "")
        if Permiso.INCIDENCIAS_GESTIONAR.value in permisos_de(rol):
            destinatarios.append(UUID(str(fila["id"])))
    return destinatarios


def _crear_notificaciones(
    conexion,
    *,
    incidencia_id: UUID,
    tipo: str,
    titulo: str,
    mensaje: str,
    destinatarios: list[UUID],
    clave: str,
) -> int:
    """
    Crea los avisos, sin duplicar.

    El `dedup_key` es UNIQUE: si el aviso ya existe, el `ON CONFLICT DO
    NOTHING` no inserta nada. Asi el barrido puede correr cada minuto sin
    llenar la bandeja de copias del mismo aviso.
    """
    creados = 0
    for destinatario in destinatarios:
        resultado = conexion.execute(
            text(
                """
                INSERT INTO incident_notifications (
                    incident_id, user_id, type, title, message, dedup_key
                )
                VALUES (:incidencia, :usuario, :tipo, :titulo, :mensaje, :clave)
                ON CONFLICT (dedup_key) DO NOTHING
                """
            ),
            {
                "incidencia": str(incidencia_id),
                "usuario": str(destinatario),
                "tipo": tipo,
                "titulo": titulo,
                "mensaje": mensaje,
                "clave": clave,
            },
        )
        creados += resultado.rowcount or 0
    return creados


def barrer_sla(*, minutos_aviso: int = MINUTOS_AVISO_POR_DEFECTO) -> dict[str, Any]:
    """
    El barrido: revisa las incidencias y avisa y escala.

    QUE HACE
    --------
    1. AVISA de los casos que vencen en los proximos `minutos_aviso` y que
       todavia no tienen respuesta.
    2. AVISA de los casos cuyo plazo de RESOLUCION esta por vencer.
    3. ESCALA los casos que pasaron el plazo de respuesta sin que nadie los
       haya tomado: les asigna un responsable de escalamiento y avisa.

    POR QUE ES UNA FUNCION Y NO UN CRON
    ------------------------------------
    Es una funcion, y el backend la corre en segundo plano (ver `main.py`). Un
    cron externo necesitaria una imagen mas, un despliegue mas y un lugar mas
    donde se rompe. Ademas, llamar la funcion a mano desde la API sirve para
    probarla y para un operador que quiere forzar el barrido.

    LA IDEMPOTENCIA
    ---------------
    Cada aviso lleva un `dedup_key` UNIQUE (caso + tipo + plazo). El barrido
    puede correr cada minuto: un aviso ya dado no se duplica.
    """
    engine = get_ai_engine()
    ahora = datetime.now(timezone.utc)

    with engine.begin() as conexion:
        # Los casos sin respuesta, con el plazo cerca o vencido.
        candidatas = conexion.execute(
            text(
                """
                SELECT id, code, title, severity, status, assigned_to,
                       response_due_at, resolution_due_at
                FROM incidents
                WHERE deleted_at IS NULL
                  AND first_response_at IS NULL
                  AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING')
                  AND response_due_at <= :limite
                """
            ),
            {"limite": ahora + timedelta(minutes=minutos_aviso)},
        ).mappings().all()

        avisos = 0
        escalados = 0

        for fila in candidatas:
            incidencia = dict(fila)
            destinatarios = _destinatarios(incidencia)

            # 1. AVISO DE VENCIMIENTO PROXIMO.
            if ahora < incidencia["response_due_at"]:
                avisos += _crear_notificaciones(
                    conexion,
                    incidencia_id=incidencia["id"],
                    tipo="SLA_DUE_SOON",
                    titulo=f"{incidencia['code']} vence en {minutos_aviso} min",
                    mensaje=(
                        f"'{incidencia['title']}' ({incidencia['severity']}) "
                        "vence su plazo de respuesta y todavia no tiene respuesta."
                    ),
                    destinatarios=destinatarios,
                    clave=(
                        f"SLA_DUE_SOON:{incidencia['id']}:"
                        f"{incidencia['response_due_at'].isoformat()}"
                    ),
                )

            # 2. ESCALAMIENTO: el plazo de respuesta ya paso.
            if ahora >= incidencia["response_due_at"]:
                politica = conexion.execute(
                    text(
                        """
                        SELECT escalate_after_minutes, escalate_to_role, escalate_to_user, max_escalations
                        FROM incident_escalation_policies
                        WHERE severity = :severidad AND is_active = TRUE
                        """
                    ),
                    {"severidad": incidencia["severity"]},
                ).mappings().first()

                if politica:
                    limite = incidencia["response_due_at"] + timedelta(
                        minutes=politica["escalate_after_minutes"]
                    )
                    ya_escalado = conexion.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM incident_events
                            WHERE incident_id = :id AND event_type = 'ESCALATED'
                            """
                        ),
                        {"id": str(incidencia["id"])},
                    ).scalar_one()

                    if ahora >= limite and ya_escalado < politica["max_escalations"]:
                        objetivo = _destinatario_de_escalamiento(conexion, politica)
                        if objetivo:
                            # Se avisa al que tiene que resolver, y el caso
                            # queda asignado a el si estaba sin responsible.
                            avisos += _crear_notificaciones(
                                conexion,
                                incidencia_id=incidencia["id"],
                                tipo="SLA_ESCALATED",
                                titulo=f"{incidencia['code']} escalado: {incidencia['severity']}",
                                mensaje=(
                                    f"'{incidencia['title']}' paso su plazo de respuesta "
                                    f"({incidencia['severity']}) y se escala."
                                ),
                                destinatarios=[objetivo],
                                clave=(
                                    f"SLA_ESCALATED:{incidencia['id']}:"
                                    f"{ya_escalado + 1}"
                                ),
                            )

                            if not incidencia["assigned_to"]:
                                conexion.execute(
                                    text(
                                        """
                                        UPDATE incidents
                                        SET assigned_to = :usuario, version = version + 1
                                        WHERE id = :id
                                        """
                                    ),
                                    {"usuario": str(objetivo), "id": str(incidencia["id"])},
                                )

                            conexion.execute(
                                text(
                                    """
                                    INSERT INTO incident_events (
                                        incident_id, event_type, actor_id, note, payload
                                    )
                                    VALUES (:id, 'ESCALATED', :actor, :nota, :payload)
                                    """
                                ),
                                {
                                    "id": str(incidencia["id"]),
                                    "actor": str(objetivo),
                                    "nota": "Escalamiento automatico por SLA vencido.",
                                    "payload": json.dumps({"escalado_a": str(objetivo)}),
                                },
                            )
                            escalados += 1

        # 3. AVISO DE VENCIMIENTO DE LA RESOLUCION (con respuesta, pero sin
        #    resolver): el caso ya se respondio, pero se le acaba el tiempo.
        por_resolver = conexion.execute(
            text(
                """
                SELECT id, code, title, severity, status, assigned_to, resolution_due_at
                FROM incidents
                WHERE deleted_at IS NULL
                  AND resolved_at IS NULL
                  AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING')
                  AND resolution_due_at <= :limite
                """
            ),
            {"limite": ahora + timedelta(minutes=minutos_aviso)},
        ).mappings().all()

        for fila in por_resolver:
            incidencia = dict(fila)
            avisos += _crear_notificaciones(
                conexion,
                incidencia_id=incidencia["id"],
                tipo="SLA_RESOLUTION_DUE",
                titulo=f"{incidencia['code']} vence en {minutos_aviso} min",
                mensaje=(
                    f"'{incidencia['title']}' ({incidencia['severity']}) "
                    "vence su plazo de resolucion."
                ),
                destinatarios=_destinatarios(incidencia),
                clave=(
                    f"SLA_RESOLUTION_DUE:{incidencia['id']}:"
                    f"{incidencia['resolution_due_at'].isoformat()}"
                ),
            )

            # EL PLAZO YA PASO: el supervisor tiene que enterarse.
            #
            # El escalamiento por "nadie respondio" (arriba) cubre el caso de
            # un caso que nadie tomo. Este cubre el otro: alguien lo tomo, pero
            # no lo resuelve, y el plazo vencio. Sin este aviso, un caso
            # abandonado con un responsable asignado no escala nunca, y el
            # backlog crece sin que nadie lo note.
            if ahora >= incidencia["resolution_due_at"]:
                politica = conexion.execute(
                    text(
                        """
                        SELECT escalate_after_minutes, escalate_to_role, escalate_to_user
                        FROM incident_escalation_policies
                        WHERE severity = :severidad AND is_active = TRUE
                        """
                    ),
                    {"severidad": incidencia["severity"]},
                ).mappings().first()

                supervisor = (
                    _destinatario_de_escalamiento(conexion, politica) if politica else None
                )

                if supervisor:
                    ya_escalado = conexion.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM incident_events
                            WHERE incident_id = :id AND event_type = 'ESCALATED'
                            """
                        ),
                        {"id": str(incidencia["id"])},
                    ).scalar_one()

                    avisos += _crear_notificaciones(
                        conexion,
                        incidencia_id=incidencia["id"],
                        tipo="SLA_RESOLUTION_BREACHED",
                        titulo=f"{incidencia['code']} paso su plazo de resolucion",
                        mensaje=(
                            f"'{incidencia['title']}' ({incidencia['severity']}) "
                            "vencio su plazo de resolucion y sigue sin resolverse. "
                            "Requiere intervencion del supervisor."
                        ),
                        destinatarios=[supervisor],
                        clave=(
                            f"SLA_RESOLUTION_BREACHED:{incidencia['id']}:"
                            f"{incidencia['resolution_due_at'].isoformat()}"
                        ),
                    )

                    if ya_escalado == 0:
                        conexion.execute(
                            text(
                                """
                                INSERT INTO incident_events (
                                    incident_id, event_type, actor_id, note, payload
                                )
                                VALUES (:id, 'ESCALATED', :actor, :nota, :payload)
                                """
                            ),
                            {
                                "id": str(incidencia["id"]),
                                "actor": str(supervisor),
                                "nota": "Se paso el plazo de resolucion: se escala al supervisor.",
                                "payload": json.dumps({"escalado_a": str(supervisor)}),
                            },
                        )
                        escalados += 1

    return {
        "avisos_creados": avisos,
        "escalados": escalados,
        "revisados": len(candidatas),
    }


def _destinatario_de_escalamiento(conexion, politica) -> UUID | None:
    """A quien se escala el caso: el usuario fijo de la politica, o el del rol."""
    if politica.get("escalate_to_user"):
        return UUID(str(politica["escalate_to_user"]))

    rol = politica.get("escalate_to_role")
    if not rol:
        return None

    fila = conexion.execute(
        text(
            """
            SELECT u.id
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.name = :rol AND u.is_active = TRUE
            ORDER BY u.created_at
            LIMIT 1
            """
        ),
        {"rol": rol},
    ).mappings().first()

    return UUID(str(fila["id"])) if fila else None


def listar_notificaciones(
    *,
    user_id: UUID,
    solo_no_leidas: bool = False,
    limite: int = 30,
) -> list[dict[str, Any]]:
    """
    Los avisos del usuario: los propios y los generales (sin destinatario).

    Los generales son los que importan a todos los que pueden ver el caso
    (por ejemplo, "el deposito se quedo sin conexion": le interesa a todos).
    """
    engine = get_ai_engine()
    with engine.connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT n.id, n.incident_id, n.type, n.title, n.message,
                       n.read_at, n.created_at, i.code, i.severity, i.status
                FROM incident_notifications n
                JOIN incidents i ON i.id = n.incident_id
                WHERE i.deleted_at IS NULL
                  AND (n.user_id = :usuario OR n.user_id IS NULL)
                  AND (:solo_no_leidas = FALSE OR n.read_at IS NULL)
                ORDER BY n.created_at DESC
                LIMIT :limite
                """
            ),
            {
                "usuario": str(user_id),
                "solo_no_leidas": solo_no_leidas,
                "limite": limite,
            },
        ).mappings().all()

    return [dict(f) for f in filas]


def contar_notificaciones(*, user_id: UUID) -> int:
    """Cuantos avisos sin leer tiene el usuario."""
    engine = get_ai_engine()
    with engine.connect() as conexion:
        return int(
            conexion.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM incident_notifications n
                    JOIN incidents i ON i.id = n.incident_id
                    WHERE i.deleted_at IS NULL
                      AND n.read_at IS NULL
                      AND (n.user_id = :usuario OR n.user_id IS NULL)
                    """
                ),
                {"usuario": str(user_id)},
            ).scalar_one()
            or 0
        )


def marcar_notificacion_leida(notificacion_id: int, *, user_id: UUID) -> dict[str, Any]:
    """Marca un aviso como leido."""
    engine = get_ai_engine()
    with engine.begin() as conexion:
        resultado = conexion.execute(
            text(
                """
                UPDATE incident_notifications
                SET read_at = NOW()
                WHERE id = :id AND read_at IS NULL AND (user_id = :usuario OR user_id IS NULL)
                """
            ),
            {"id": notificacion_id, "usuario": str(user_id)},
        )
        if resultado.rowcount == 0:
            raise IncidenciaNoEncontrada("Ese aviso no existe o ya estaba leido.")

    return {"ok": True}


def obtener_calendario() -> dict[str, Any]:
    """La configuracion del calendario laboral."""
    engine = get_ai_engine()
    with engine.connect() as conexion:
        fila = conexion.execute(
            text(
                """
                SELECT timezone, day_start, day_end, workdays, holidays, updated_at
                FROM business_calendar_settings WHERE id = 1
                """
            )
        ).mappings().first()

    if not fila:
        raise IncidenciaInvalida("No hay configuracion de calendario laboral.")

    datos = dict(fila)
    # Los `TIME` y los `DATE[]` llegan como texto de Postgres: se normalizan
    # para que el frontend los pueda usar tal cual.
    datos["day_start"] = str(datos["day_start"])[:5]
    datos["day_end"] = str(datos["day_end"])[:5]
    datos["holidays"] = [str(h) for h in (datos.get("holidays") or [])]
    return datos


def actualizar_calendario(datos: dict[str, Any]) -> dict[str, Any]:
    """
    Cambia la configuracion del calendario laboral.

    LA ZONA HORARIA SE VALIDA
    -------------------------
    `AT TIME ZONE` con una zona invalida hace fallar la funcion del SLA en el
    momento del calculo, es decir, en el alta de una incidencia y no ahora.
    Por eso se valida ACA, contra la lista de zonas que el propio Postgres
    tiene.
    """
    timezone = (datos.get("timezone") or "America/Argentina/Buenos_Aires").strip()
    day_start = (datos.get("day_start") or "09:00").strip()
    day_end = (datos.get("day_end") or "18:00").strip()
    workdays = [int(d) for d in (datos.get("workdays") or [1, 2, 3, 4, 5])]
    # Los feriados van como `date`, no como texto: la columna es `DATE[]` y
    # psycopg2 no castea un `text[]` a un `date[]` solo.
    holidays = []
    for feriado in datos.get("holidays") or []:
        try:
            holidays.append(date.fromisoformat(str(feriado)[:10]))
        except ValueError as exc:
            raise IncidenciaInvalida(
                f"'{feriado}' no es una fecha valida (se espera AAAA-MM-DD)."
            ) from exc

    engine = get_ai_engine()
    with engine.begin() as conexion:
        existe = conexion.execute(
            text("SELECT 1 FROM pg_timezone_names WHERE name = :tz"), {"tz": timezone}
        ).first()
        if not existe:
            raise IncidenciaInvalida(
                f"'{timezone}' no es una zona horaria valida para Postgres."
            )

        try:
            conexion.execute(
                text(
                    """
                    UPDATE business_calendar_settings
                    SET timezone = :tz, day_start = :inicio, day_end = :fin,
                        workdays = :dias, holidays = :feriados, updated_at = NOW()
                    WHERE id = 1
                    """
                ),
                {
                    "tz": timezone,
                    "inicio": day_start,
                    "fin": day_end,
                    "dias": workdays,
                    "feriados": holidays,
                },
            )
        except SQLAlchemyError as exc:
            raise IncidenciaInvalida(f"No se pudo guardar el calendario: {exc}") from exc

    return obtener_calendario()
