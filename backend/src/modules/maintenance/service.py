"""
Servicio de Mantenimiento.
Operaciones sobre tabla `maintenance` extendida + planes.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import get_db_session


def listar_pendientes(estado: str = "ALL", tecnico_id: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
    """Vista v_devices_maintenance_due con filtros."""
    db = get_db_session()
    try:
        query = """
            SELECT
                m.id AS maintenance_id,
                d.id AS device_id,
                d.serial_number,
                d.qr_code,
                mo.model AS model_name,
                mo.sku,
                mp.name AS plan_name,
                mp.type AS plan_type,
                m.scheduled_for,
                m.due_date,
                m.status,
                l.store_name AS location_name,
                l.client_id,
                CASE
                    WHEN m.due_date < CURRENT_DATE THEN CURRENT_DATE - m.due_date
                    ELSE 0
                END AS days_overdue,
                m.result,
                m.next_scheduled_for
            FROM maintenance m
            JOIN devices d ON d.id = m.device_id
            JOIN models mo ON mo.id = d.model_id
            JOIN maintenance_plans mp ON mp.id = m.plan_id
            JOIN locations l ON l.id = d.current_location_id
            WHERE m.status IN ('PENDING', 'DUE_SOON', 'OVERDUE')
        """
        params = {"limite": limite}
        if estado != "ALL":
            query += " AND m.status = :estado"
            params["estado"] = estado
        if tecnico_id:
            query += " AND m.tecnico_id = :tecnico_id"
            params["tecnico_id"] = UUID(tecnico_id)
        query += " ORDER BY m.due_date ASC LIMIT :limite"

        result = db.execute(text(query), params)
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def listar_planes(model_id: int | None = None) -> list[dict[str, Any]]:
    db = get_db_session()
    try:
        query = """
            SELECT
                mp.id, mp.model_id, mp.name, mp.description, mp.type,
                mp.interval_months, mp.interval_basis, mp.lead_time_days,
                mp.checklist, mp.estimated_duration_minutes,
                mp.requires_specialist, mp.is_active
            FROM maintenance_plans mp
            WHERE mp.is_active
        """
        params = {}
        if model_id:
            query += " AND mp.model_id = :model_id"
            params["model_id"] = model_id
        query += " ORDER BY mp.name"

        result = db.execute(text(query), params)
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def completar_mantenimiento(
    maintenance_id: str,
    result: str,
    observations: str | None = None,
    checklist_results: list[dict] | None = None,
    labor_cost: float | None = None,
    parts_cost: float | None = None,
    next_scheduled_for: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Marca maintenance como COMPLETED con resultados."""
    db = get_db_session()
    try:
        maintenance_uuid = UUID(maintenance_id)
        user_uuid = UUID(user_id) if user_id else None

        # Validar result
        valid_results = {'OK', 'NEEDS_REPAIR', 'NEEDS_REPLACEMENT', 'OBSERVATION'}
        if result not in valid_results:
            raise ValueError(f"result inválido: {result}. Válidos: {valid_results}")

        # Actualizar
        query = """
            UPDATE maintenance SET
                status = 'COMPLETED',
                completed_at = NOW(),
                result = :result,
                observations = :observations,
                checklist_results = :checklist_results,
                labor_cost = :labor_cost,
                parts_cost = :parts_cost,
                total_cost = COALESCE(:labor_cost, 0) + COALESCE(:parts_cost, 0),
                next_scheduled_for = :next_scheduled_for,
                tecnico_id = :tecnico_id
            WHERE id = :mid
            RETURNING id, device_id, plan_id, status, completed_at
        """
        params = {
            "mid": maintenance_uuid,
            "result": result,
            "observations": observations,
            "checklist_results": checklist_results,
            "labor_cost": labor_cost,
            "parts_cost": parts_cost,
            "next_scheduled_for": next_scheduled_for,
            "tecnico_id": user_uuid,
        }
        res = db.execute(text(query), params)
        row = res.mappings().first()
        db.commit()

        if not row:
            raise ValueError(f"Mantenimiento {maintenance_id} no encontrado")

        return dict(row)
    finally:
        db.close()


def generar_mantenimientos() -> list[dict[str, Any]]:
    """Ejecuta función PL/pgSQL generate_pending_maintenances()."""
    db = get_db_session()
    try:
        result = db.execute(text("SELECT * FROM generate_pending_maintenances()"))
        rows = result.mappings().all()
        db.commit()
        return [dict(r) for r in rows]
    finally:
        db.close()