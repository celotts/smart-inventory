"""Helper para registrar acciones sensibles en audit_log."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from src.modules.ai.executor import get_ai_engine


def registrar(
    *,
    user_id: str | None,
    action: str,
    resource: str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    engine = get_ai_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO audit_log (user_id, action, resource, ip_address, request_id, metadata)
                    VALUES (:uid, :action, :resource, :ip, :rid, :meta)
                    """
                ),
                {
                    "uid": user_id,
                    "action": action,
                    "resource": resource,
                    "ip": ip_address,
                    "rid": request_id,
                    "meta": json.dumps(metadata) if metadata else None,
                },
            )
    except Exception:
        # El audit log nunca debe tumbar la operación principal.
        pass
