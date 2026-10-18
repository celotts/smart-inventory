"""Function calling: esquema de herramientas para el inventario."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Registro de funciones invocables por el modelo
REGISTRY: dict[str, Callable[..., Any]] = {}


def tool(name: str):
    def decorator(fn):
        REGISTRY[name] = fn
        return fn

    return decorator


@tool("buscar_dispositivo")
def buscar_dispositivo(query: str) -> dict:
    """Busca dispositivos por serial o nombre."""
    from src.modules.ai.executor import get_ai_engine

    engine = get_ai_engine()
    with engine.connect() as conn:
        rows = (
            conn.execute(
                __import__("sqlalchemy").text(
                    "SELECT id, serial, nombre FROM devices WHERE serial ILIKE :q OR nombre ILIKE :q LIMIT 10"
                ),
                {"q": f"%{query}%"},
            )
            .mappings()
            .all()
        )
        return {"resultados": [dict(r) for r in rows]}


@tool("stock_por_ubicacion")
def stock_por_ubicacion(ubicacion: str) -> dict:
    """Stock disponible en una ubicación."""
    from src.modules.ai.executor import get_ai_engine

    engine = get_ai_engine()
    with engine.connect() as conn:
        rows = (
            conn.execute(
                __import__("sqlalchemy").text(
                    "SELECT COUNT(*) AS total FROM devices WHERE ubicacion = :u"
                ),
                {"u": ubicacion},
            )
            .mappings()
            .first()
        )
        return {"ubicacion": ubicacion, "total": (rows["total"] if rows else 0)}
