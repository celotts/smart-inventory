"""
Sesión de base de datos compartida.

Patrón: engine global + function factory para sesiones.
Compatible con el estilo usado en ai.executor y users.models.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine import Engine

from src.core.config import get_settings


_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def get_engine() -> Engine:
    """Engine global con pool configurado."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_size=3,
            max_overflow=2,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> sessionmaker:
    """Factory de sesiones para dependency injection."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=Session,
        )
    return _SessionFactory


def get_db_session() -> Session:
    """Nueva sesión para uso directo (no DI)."""
    return get_session_factory()()


# Alias para compatibilidad con código existente
def get_db() -> Session:
    """Alias usado en users.models."""
    return get_db_session()