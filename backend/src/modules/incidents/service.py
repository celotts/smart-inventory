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
