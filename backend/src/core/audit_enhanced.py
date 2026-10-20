"""
Sistema de auditoría mejorado para trazabilidad completa.
Registra INSERT, UPDATE, DELETE con old/new values.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.modules.ai.executor import get_ai_engine

logger = logging.getLogger(__name__)

# Tablas que deben ser auditadas completamente
TABLAS_AUDITABLES = {
    "devices", "models", "users", "roles", "purchase_orders", 
    "purchase_order_lines", "clients", "vendors", "categories",
    "locations", "maintenance_plans", "repairs"
}

def registrar_auditoria_completa(
    *,
    user_id: str | None = None,
    action: str,  # INSERT, UPDATE, DELETE
    table_name: str,
    record_id: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """
    Registra operación con trazabilidad completa.
    NUNCA propaga excepciones para no afectar operación principal.
    """
    if action not in {"INSERT", "UPDATE", "DELETE"}:
        logger.warning(f"Acción inválida para auditoría: {action}")
        return
    
    if table_name not in TABLAS_AUDITABLES and table_name not in {"audit_logs", "movement_history"}:
        # Solo auditar tablas importantes, pero permitir ciertas excepciones
        pass
    
    try:
        with get_ai_engine().begin() as conexion:
            conexion.execute(
                text("""
                    INSERT INTO audit_logs (
                        action, table_name, record_id, 
                        old_value, new_value, 
                        user_id, ip_address, user_agent,
                        created_at
                    )
                    VALUES (
                        CAST(:action AS audit_action), :table_name, :record_id,
                        :old_value, :new_value,
                        :user_id, :ip_address, :user_agent,
                        NOW()
                    )
                """),
                {
                    "action": action,
                    "table_name": table_name[:100],
                    "record_id": record_id,
                    "old_value": json.dumps(old_value, default=str) if old_value else None,
                    "new_value": json.dumps(new_value, default=str) if new_value else None,
                    "user_id": user_id,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                }
            )
    except SQLAlchemyError as e:
        logger.exception(f"Error al registrar auditoría completa: {e}")
    except Exception as e:
        logger.exception(f"Error inesperado en auditoría: {e}")

def obtener_trazabilidad_completa(
    table_name: str | None = None,
    record_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Obtiene trazabilidad completa con filtros.
    """
    condiciones = ["1=1"]
    params = {"limit": limit, "offset": offset}
    
    if table_name:
        condiciones.append("al.table_name = :table_name")
        params["table_name"] = table_name
    if record_id:
        condiciones.append("al.record_id = :record_id")
        params["record_id"] = record_id
    if user_id:
        condiciones.append("al.user_id = :user_id")
        params["user_id"] = user_id
    
    sql = f"""
        SELECT 
            al.id,
            al.action,
            al.table_name,
            al.record_id,
            al.old_value,
            al.new_value,
            al.user_id,
            al.ip_address,
            al.user_agent,
            al.created_at,
            u.full_name as user_name,
            u.email as user_email
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        WHERE {' AND '.join(condiciones)}
        ORDER BY al.created_at DESC
        LIMIT :limit OFFSET :offset
    """
    
    try:
        with get_ai_engine().connect() as conn:
            result = conn.execute(text(sql), params)
            rows = result.mappings().all()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.exception(f"Error al obtener trazabilidad: {e}")
        return []
