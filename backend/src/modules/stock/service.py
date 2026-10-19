"""
Servicio de Stock Agregado.
Mantiene stock_levels sincronizado via triggers y provee vistas de lectura.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import get_db_session as db_session_factory


def resumen_stock(solo_bajo: bool = False) -> list[dict[str, Any]]:
    """
    Vista v_stock_summary con filtro opcional.
    """
    db = db_session_factory()
    try:
        query = """
            SELECT
                m.id AS model_id,
                m.sku,
                m.model AS model_name,
                m.brand,
                c.name AS category_name,
                c.code AS category_code,
                m.min_stock,
                m.max_stock,
                m.unit_cost,
                sl.location_id,
                l.store_name AS location_name,
                sl.available_count,
                sl.in_transit_count,
                sl.installed_count,
                sl.in_repair_count,
                (sl.available_count + sl.in_transit_count + sl.installed_count + sl.in_repair_count) AS total_units,
                sl.total_value,
                CASE
                    WHEN sl.available_count <= m.min_stock THEN 'LOW'
                    WHEN m.max_stock > 0 AND sl.available_count >= m.max_stock THEN 'HIGH'
                    ELSE 'OK'
                END AS stock_status
            FROM stock_levels sl
            JOIN models m ON m.id = sl.model_id AND m.is_active
            JOIN categories c ON c.id = m.category_id
            JOIN locations l ON l.id = sl.location_id
        """
        if solo_bajo:
            query += " WHERE sl.available_count <= m.min_stock"
        query += " ORDER BY m.brand, m.model, l.store_name"

        result = db.execute(text(query))
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def stock_bajo() -> list[dict[str, Any]]:
    """
    Vista v_low_stock: modelos con available <= min_stock.
    Incluye suggested_reorder_qty calculado.
    """
    db = db_session_factory()
    try:
        query = """
            SELECT
                sl.model_id,
                m.sku,
                m.model AS model_name,
                m.brand,
                m.min_stock,
                m.max_stock,
                m.reorder_multiple,
                m.lead_time_days,
                sl.location_id,
                l.store_name AS location_name,
                sl.available_count,
                sl.available_count <= m.min_stock AS is_below_min,
                GREATEST(m.min_stock - sl.available_count, 0) AS deficit,
                CASE
                    WHEN m.max_stock > sl.available_count
                    THEN CEIL((m.max_stock - sl.available_count) / m.reorder_multiple::NUMERIC) * m.reorder_multiple
                    ELSE 0
                END AS suggested_reorder_qty
            FROM stock_levels sl
            JOIN models m ON m.id = sl.model_id AND m.is_active
            JOIN locations l ON l.id = sl.location_id
            WHERE sl.available_count <= m.min_stock
               OR (m.max_stock > 0 AND sl.available_count >= m.max_stock)
            ORDER BY sl.available_count ASC
        """
        result = db.execute(text(query))
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def recalcular_todo() -> None:
    """Recalcula stock_levels para todos los model/location con devices."""
    db = db_session_factory()
    try:
        query = """
            SELECT DISTINCT model_id, current_location_id
            FROM devices
            WHERE model_id IS NOT NULL AND current_location_id IS NOT NULL
        """
        pares = db.execute(text(query)).fetchall()

        for model_id, location_id in pares:
            db.execute(text("SELECT recalculate_stock_levels(%s, %s)"), (model_id, location_id))

        db.commit()
    finally:
        db.close()