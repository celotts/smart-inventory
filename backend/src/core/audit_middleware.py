"""
Middleware de auditoría para captura automática de contexto.
Registra IP, user_agent y user_id en cada request para trazabilidad completa.
"""
from __future__ import annotations

import contextvars
from typing import Callable
from uuid import UUID

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Contexto thread-safe para información de auditoría por request
audit_context = contextvars.ContextVar("audit_context", default={})

def obtener_contexto_auditoria() -> dict:
    """Obtiene el contexto de auditoría del request actual."""
    return audit_context.get({})

def set_audit_context(user_id: str | None = None, ip: str | None = None, user_agent: str | None = None):
    """Establece el contexto de auditoría."""
    ctx = audit_context.get({})
    ctx.update({
        "user_id": user_id,
        "ip_address": ip,
        "user_agent": user_agent,
    })
    audit_context.set(ctx)

class AuditMiddleware(BaseHTTPMiddleware):
    """
    Middleware que captura información del request para auditoría.
    Extrae IP, User-Agent y prepara contexto.
    """
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extraer IP (considerando proxies)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else None
        
        user_agent = request.headers.get("User-Agent")
        
        # Establecer contexto vacío inicialmente
        ctx = {
            "ip_address": ip,
            "user_agent": user_agent,
            "user_id": None,
        }
        audit_context.set(ctx)
        
        response = await call_next(request)
        return response
