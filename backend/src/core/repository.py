"""Repositorio base generico con helpers comunes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from src.modules.ai.executor import get_ai_engine


class BaseRepository:
    def __init__(self):
        self.engine = get_ai_engine()

    def fetch_one(self, sql: str, params: dict | None = None) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), params or {}).mappings().first()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: dict | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params or {}).mappings().all()
            return [dict(r) for r in rows]

    def execute(self, sql: str, params: dict | None = None) -> Any:
        with self.engine.begin() as conn:
            return conn.execute(text(sql), params or {})
