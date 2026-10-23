"""
Decoradores para auditoría automática en servicios.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from src.core.audit_enhanced import registrar_auditoria_completa
from src.core.audit_middleware import obtener_contexto_auditoria

def auditar(
    action: str, 
    table_name: str,
    get_record_id: Callable[[Any], str | None] | None = None,
    get_old_value: Callable[[Any], dict] | None = None,
    get_new_value: Callable[[Any], dict] | None = None,
):
    """
    Decorador para auditar automaticamente operaciones CRUD.
    
    Ejemplo:
    @auditar(action="UPDATE", table_name="devices", 
             get_record_id=lambda r: str(r.id),
             get_old_value=lambda _: old_state)
    def actualizar_dispositivo(...):
        ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Ejecutar función
            result = func(*args, **kwargs)
            
            # Obtener contexto
            ctx = obtener_contexto_auditoria()
            
            # Extraer datos
            record_id = get_record_id(result) if get_record_id else None
            old_val = get_old_value(result) if get_old_value else None
            new_val = get_new_value(result) if get_new_value else None
            
            # Registrar auditoría
            registrar_auditoria_completa(
                user_id=ctx.get("user_id"),
                action=action,
                table_name=table_name,
                record_id=record_id,
                old_value=old_val,
                new_value=new_val,
                ip_address=ctx.get("ip_address"),
                user_agent=ctx.get("user_agent"),
            )
            return result
        return wrapper
    return decorator
