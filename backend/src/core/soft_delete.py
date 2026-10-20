"""
Helpers para borrado lógico en todo el sistema.
Garantiza que nada se borra físicamente, solo se marca deleted_at.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.modules.ai.executor import get_ai_engine

def soft_delete(
    table_name: str,
    record_id: Any,
    user_id: str | None = None,
    reason: str | None = None
) -> bool:
    """
    Realiza borrado lógico marcando deleted_at.
    Nunca hace DELETE físico.
    """
    try:
        with get_ai_engine().begin() as conn:
            sql = f"""
                UPDATE {table_name}
                SET deleted_at = :deleted_at,
                    deleted_by = :user_id
                WHERE id = :id AND deleted_at IS NULL
            """
            result = conn.execute(text(sql), {
                "deleted_at": datetime.utcnow(),
                "user_id": user_id,
                "id": record_id
            })
            return result.rowcount > 0
    except Exception as e:
        print(f"Error en soft_delete: {e}")
        return False

def restore(
    table_name: str,
    record_id: Any,
    user_id: str | None = None
) -> bool:
    """Restaura un registro con borrado lógico."""
    try:
        with get_ai_engine().begin() as conn:
            sql = f"""
                UPDATE {table_name}
                SET deleted_at = NULL,
                    deleted_by = NULL
                WHERE id = :id
            """
            result = conn.execute(text(sql), {
                "id": record_id
            })
            return result.rowcount > 0
    except Exception as e:
        print(f"Error en restore: {e}")
        return False
