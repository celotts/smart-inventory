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

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
    solo_mias: bool = False,
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


def obtener_incidencia(incidencia_id: UUID) -> dict[str, Any]:
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

        eventos = conexion.execute(
            text(
                """
                SELECT id, event_type, from_status, to_status, actor_id, note, payload, created_at
                FROM incident_events
                WHERE incident_id = :id
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"id": str(incidencia_id)},
        ).mappings().all()

    datos = _a_resumen(dict(fila))
    datos["eventos"] = [dict(e) for e in eventos]
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

            # El evento de alta. Lo escribe la API y no el trigger: el trigger
            # de SLA calcula plazos y codigo, que es otra cosa.
            conexion.execute(
                text(
                    """
                    INSERT INTO incident_events (incident_id, event_type, to_status, actor_id, note)
                    VALUES (:id, 'CREATED', 'OPEN', :actor, :nota)
                    """
                ),
                {
                    "id": str(incidencia_id),
                    "actor": str(user_id),
                    "nota": datos.get("description"),
                },
            )
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
                    "payload": {"severidad": campos["severity"]},
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
                    "payload": {"responsable": campos["assigned_to"]},
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


def metricas() -> dict[str, Any]:
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
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS creadas_30d
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

    datos = dict(resumen)
    if datos.get("mttr_horas") is not None:
        datos["mttr_horas"] = float(datos["mttr_horas"])
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
