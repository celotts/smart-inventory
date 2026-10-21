"""
Service de reparaciones (el taller).

QUE ES UNA Reparacion
---------------------
La EJECUCION técnica de un problema. La incidencia (Service Desk) es el
REPORTE; la reparacion es la ORDEN DE TRABAJO: quien la atiende, que le
diagnosticaron, que se hizo y cuanto costo.

POR QUE EXISTE UNA TABLA APARTE
-------------------------------
Porque el taller tiene su propio ciclo de vida, distinto del del caso:

  - La reparacion tiene un diagnostico, un trabajo hecho y un costo.
  - Se puede cerrar como REPAIRED o como UNREPAIRABLE (no se pudo arreglar).
  - El equipo NO puede pasar a `IN_REPAIR` sin una reparacion abierta: lo
    exige el trigger `validar_transicion_estado` de la base, porque un
    equipo "en el taller" sin orden de trabajo es un equipo que el taller no
    sabe que tiene.

LA REGLA DE ORO
---------------
Una orden de reparacion y una incidencia no son la misma cosa, pero siempre
estan unidas: la reparacion guarda el `incident_id` del caso que la origino.
 Asi se puede responder "de que se estaba quejar este equipo" con un solo
 `JOIN`, sin depender del historial de movimientos.

EL CICLO
--------
  1. `ingreso_taller`: el equipo entra al taller y se abre la orden.
  2. `cerrar_reparacion`: el taller termina. O queda REPAIRED (vuelve al
     stock) o UNREPAIRABLE (no se pudo: es el caso del daño total).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.modules.ai.executor import get_ai_engine

# Los estados de una reparación. Los mismos que el ENUM de la base.
REPARACIONES_REPAIRED = "REPAIRED"
REPARACIONES_UNREPAIRABLE = "UNREPAIRABLE"


class ReparaError(RuntimeError):
    """Error del taller. 409."""


def _reparacion_abierta(conexion, device_id: int) -> dict[str, Any] | None:
    """La orden de trabajo abierta del equipo, si la hay."""
    fila = conexion.execute(
        text(
            """
            SELECT id, status, technician_id, initial_diagnosis, incident_id
            FROM repairs
            WHERE device_id = :device_id
              AND status NOT IN ('REPAIRED', 'UNREPAIRABLE')
            ORDER BY entry_date DESC
            LIMIT 1
            """
        ),
        {"device_id": device_id},
    ).mappings().first()
    return dict(fila) if fila else None


def ingreso_taller(
    device_id: int,
    *,
    technician_id: UUID,
    diagnostico: str,
    incident_id: UUID | None = None,
    costo_estimado: float | None = None,
) -> dict[str, Any]:
    """
    Registra el ingreso al taller: abre la orden de trabajo y deja el equipo
    en `IN_REPAIR`.

    EL ORDEN IMPORTA
    ----------------
    Primero se abre la orden y después se mueve el equipo: el trigger de la
    base exige que exista la reparación abierta para poner el equipo en
    `IN_REPAIR`. Al reves, la base rechaza el cambió y el equipo queda
    "en el taller" sin orden, que es justo lo que el trigger quiere evitar.
    """
    from src.modules.devices import service as devices_service

    engine = get_ai_engine()
    with engine.begin() as conexion:
        existente = _reparacion_abierta(conexion, device_id)
        if existente:
            # Ya está en el taller: no se abre una segunda orden.
            return existente

        fila = conexion.execute(
            text(
                """
                INSERT INTO repairs (
                    device_id, technician_id, status, initial_diagnosis,
                    estimated_cost, entry_date, incident_id
                )
                VALUES (
                    :device_id, :tecnico, 'DIAGNOSIS', :diagnostico,
                    :costo, NOW(), :incidencia
                )
                RETURNING id, device_id, technician_id, status, initial_diagnosis,
                          estimated_cost, entry_date, incident_id
                """
            ),
            {
                "device_id": device_id,
                "tecnico": str(technician_id),
                "diagnostico": (diagnostico or "")[:4000],
                "costo": costo_estimado,
                "incidencia": str(incident_id) if incident_id else None,
            },
        ).mappings().first()

    # El equipo pasa al taller. Si esto falla, la orden queda abierta: el
    # técnico ve que hay un equipo con orden y no en el taller, y lo
    # resuelve. Es mejor eso que un equipo en el taller sin orden.
    devices_service.actualizar_dispositivo(device_id, {"current_status": "IN_REPAIR"})

    return dict(fila)


def cerrar_reparacion(
    device_id: int,
    *,
    technician_id: UUID,
    trabajo: str,
    reparado: bool,
    costo: float | None = None,
) -> dict[str, Any] | None:
    """
    Cierra la orden de trabajo del equipo.

    - `reparado=True`: queda `REPAIRED`. El equipo puede volver al depósito.
    - `reparado=False`: queda `UNREPAIRABLE`. El equipo no se pudo arreglar:
      es el caso del daño total, y el que lo resuelve da de baja el equipo.

    Si el equipo no tiene ninguna orden abierta, no hace nada y devuelve
    `None`: no es un error, es que el equipo nunca estuvo en el taller.
    """
    engine = get_ai_engine()
    with engine.begin() as conexion:
        abierta = _reparacion_abierta(conexion, device_id)
        if not abierta:
            return None

        estado = REPARACIONES_REPAIRED if reparado else REPARACIONES_UNREPAIRABLE

        fila = conexion.execute(
            text(
                """
                UPDATE repairs
                SET status = :estado,
                    work_performed = :trabajo,
                    exit_date = NOW(),
                    estimated_cost = COALESCE(:costo, estimated_cost)
                WHERE id = :id
                RETURNING id, device_id, technician_id, status, initial_diagnosis,
                          work_performed, estimated_cost, entry_date, exit_date,
                          incident_id
                """
            ),
            {
                "estado": estado,
                "trabajo": (trabajo or "")[:4000],
                "costo": costo,
                "id": abierta["id"],
            },
        ).mappings().first()

    return dict(fila)


def listar_reparaciones(device_id: int) -> list[dict[str, Any]]:
    """Las ordenes de trabajo de un equipo, de la más reciente a la más vieja."""
    engine = get_ai_engine()
    with engine.connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT
                    r.id,
                    r.status,
                    r.initial_diagnosis,
                    r.work_performed,
                    r.estimated_cost,
                    r.entry_date,
                    r.exit_date,
                    r.incident_id,
                    u.full_name AS técnico_nombre
                FROM repairs r
                LEFT JOIN users u ON u.id = r.technician_id
                WHERE r.device_id = :device_id
                ORDER BY r.entry_date DESC
                """
            ),
            {"device_id": device_id},
        ).mappings().all()
    return [dict(f) for f in filas]
