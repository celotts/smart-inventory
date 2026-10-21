"""
Servicio de Stock Agregado.
Mantiene stock_levels sincronizado via triggers y provee vistas de lectura.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import get_db_session as db_session_factory


def _escapar_like(texto: str) -> str:
    r"""Escapa los comodines de LIKE: un "%" del operador es un "%"."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resumen_stock(
    solo_bajo: bool = False,
    *,
    busqueda: str | None = None,
    location_id: int | None = None,
    limite: int | None = None,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    Vista v_stock_summary con filtro opcional.

    LA EXISTENCIA POR PRODUCTO Y Ubicación
    --------------------------------------
    Está lista crece con el PRODUCTO por la Ubicación: 800 productos en 6
    ubicaciones son 4800 filas. Sin límite, la API las devuelve todas y el
    navegador las pinta todas: con 5000 equipos la app deja de responder.

    Por eso el `límite` y el `total`: la pantalla página, y el operador ve
    cuantas hay en total.
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
        condiciones: list[str] = []
        parametros: dict[str, Any] = {}
        if solo_bajo:
            condiciones.append("sl.available_count <= m.min_stock")
        if location_id:
            condiciones.append("sl.location_id = :location_id")
            parametros["location_id"] = location_id
        if busqueda:
            # El filtro va en el SERVIDOR, no en el navegador: con la lista
            # paginada, filtrar en el cliente solo mira las 50 filas de la
            # página, y el operador ve "no hay" con 4800 existencias.
            condiciones.append(
                "(m.sku ILIKE :busqueda OR m.brand ILIKE :busqueda "
                "OR m.model ILIKE :busqueda OR l.store_name ILIKE :busqueda)"
            )
            parametros["busqueda"] = f"%{_escapar_like(busqueda)}%"

        where = " AND ".join(condiciones)
        if where:
            query += f" WHERE {where}"
        query += " ORDER BY m.brand, m.model, l.store_name"

        # El total, con el MISMO filtro: si no, el "de 4800" no corresponde a
        # lo que el operador está viendo.
        conteo = f"""
            SELECT COUNT(*)
            FROM stock_levels sl
            JOIN models m ON m.id = sl.model_id AND m.is_active
            JOIN categories c ON c.id = m.category_id
            JOIN locations l ON l.id = sl.location_id
            {where}
        """

        if limite is not None:
            query += " LIMIT :limite OFFSET :offset"
            parametros = {**parametros, "limite": limite, "offset": offset}

        result = db.execute(text(query), parametros)
        rows = result.mappings().all()
        total = db.execute(text(conteo), parametros).scalar_one()
        return [dict(r) for r in rows], int(total or 0)
    finally:
        db.close()


def productos_por_reponer(limite: int = 100) -> list[dict[str, Any]]:
    """
    Los productos POR REPONER, con el MISMO criterio que el tablero.

    POR QUE UNA SOLA FUENTE
    -----------------------
    Antes, la tarjeta "Por reponer" del tablero y el modal que se abre al
    pulsarla usaban DOS consultas distintas: una por MODELO (el total
    disponible contra el mínimo) y otra por MODELO Y Ubicación. El operador
    pulsaba un producto del tablero y el modal decia "todos los productos
    están por encima de su mínimo": la misma pantalla, dos verdades, y la
    segunda con la información EQUIVOCADA.

    Ahora las dos salen de acá: el criterio es el total disponible del
    PRODUCTO (en todas las ubicaciones) contra su mínimo. Es el que tiene
    sentido para decidir una compra: si hay 4 unidades del producto
    repartidas en dos depósitos, el depósito puede estar por debajo de su
    mínimo, pero el PRODUCTO no está por reponer.
    """
    db = db_session_factory()
    try:
        filas = db.execute(
            text(
                """
                SELECT
                    m.id AS model_id,
                    m.sku,
                    m.brand,
                    m.model AS model_name,
                    m.min_stock,
                    m.max_stock,
                    m.lead_time_days,
                    COALESCE(SUM(sl.available_count), 0) AS disponibles,
                    GREATEST(m.min_stock - COALESCE(SUM(sl.available_count), 0), 0) AS faltante,
                    COALESCE(
                        (SELECT l.store_name
                         FROM stock_levels sl2
                         JOIN locations l ON l.id = sl2.location_id
                         WHERE sl2.model_id = m.id AND sl2.available_count > 0
                         ORDER BY sl2.available_count DESC
                         LIMIT 1),
                        'Sin ubicacion'
                    ) AS ubicacion
                FROM models m
                LEFT JOIN stock_levels sl ON sl.model_id = m.id
                WHERE m.deleted_at IS NULL AND m.is_active
                GROUP BY m.id
                HAVING COALESCE(SUM(sl.available_count), 0) <= m.min_stock
                ORDER BY COALESCE(SUM(sl.available_count), 0) ASC, m.brand
                LIMIT :limite
                """
            ),
            {"limite": limite},
        ).mappings().all()
        return [dict(f) for f in filas]
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