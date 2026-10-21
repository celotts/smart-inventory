"""
Servicio de Reportes - KPIs, tendencias, reporte operativo LLM.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import get_db_session


def reporte_operativo() -> dict[str, Any]:
    """
    Genera reporte ejecutivo completo.
    Combina datos de múltiples vistas y delega redacción a LLM (endpoint /ai/reporte).
    Aquí solo preparamos los datos estructurados.
    """
    db = get_db_session()
    try:
        # 1. Alertas críticas
        alertas = _get_alertas_criticas(db)

        # 2. KPIs principales
        kpis = _get_kpis_principales(db)

        # 3. Stock por estado
        stock = _get_stock_por_estado(db)

        # 4. Movimientos recientes (7 días)
        movimientos = _get_movimientos_recientes(db, dias=7)

        # 5. Mantenimientos overdue
        mantenimientos = _get_mantenimientos_overdue(db)

        # 6. POs pendientes
        pos = _get_pos_pendientes(db)

        return {
            "alertas": alertas,
            "kpis": kpis,
            "stock_por_estado": stock,
            "movimientos_7d": movimientos,
            "mantenimientos_overdue": mantenimientos,
            "pos_pendientes": pos,
            "generated_at": "NOW()",
        }
    finally:
        db.close()


def _get_alertas_criticas(db: Session) -> list[dict]:
    query = """
        SELECT
            'STOCK_BAJO' AS tipo,
            m.sku,
            m.model AS modelo,
            -- `::text` en las dos ramas: el UNION exige que las columnas
            -- tengan el MISMO tipo, y la rama de orden de compra pone una
            -- fecha en `actual` (texto) donde la de stock bajo pone un
            -- conteo (entero). Sin el cast, Postgres responde "UNION types
            -- integer and text cannot be matched" y el reporte entero
            -- devuelve 500.
            sl.available_count::text AS actual,
            m.min_stock::text         AS minimo,
            l.store_name AS ubicacion
        FROM stock_levels sl
        JOIN models m ON m.id = sl.model_id AND m.is_active
        JOIN locations l ON l.id = sl.location_id
        WHERE sl.available_count <= m.min_stock
        UNION ALL
        SELECT
            'MAINT_OVERDUE',
            d.serial_number,
            mo.model,
            NULL,
            NULL,
            l.store_name
        FROM maintenance m
        JOIN devices d ON d.id = m.device_id
        JOIN models mo ON mo.id = d.model_id
        JOIN locations l ON l.id = d.current_location_id
        WHERE m.status = 'OVERDUE'
        UNION ALL
        SELECT
            'PO_VENCIDA',
            po.id::text,
            v.name,
            po.expected_date::text,
            NULL,
            NULL
        FROM purchase_orders po
        LEFT JOIN vendors v ON v.id = po.vendor_id
        WHERE po.status IN ('ENVIADA', 'CONFIRMADA')
          AND po.expected_date < CURRENT_DATE
        LIMIT 20
    """
    return [dict(r) for r in db.execute(text(query)).mappings().all()]


def _get_kpis_principales(db: Session) -> dict:
    query = """
        SELECT
            (SELECT COUNT(*) FROM devices WHERE deleted_at IS NULL) AS total_devices,
            (SELECT COUNT(*) FROM devices WHERE current_status = 'IN_WAREHOUSE') AS en_deposito,
            (SELECT COUNT(*) FROM devices WHERE current_status = 'IN_TRANSIT') AS en_transito,
            (SELECT COUNT(*) FROM devices WHERE current_status = 'INSTALLED') AS instalados,
            (SELECT COUNT(*) FROM devices WHERE current_status = 'IN_REPAIR') AS en_taller,
            (SELECT COUNT(*) FROM devices WHERE current_status = 'PERMANENTLY_DISPOSED') AS dados_baja,
            (SELECT COALESCE(SUM(unit_cost), 0) FROM devices d JOIN models m ON m.id = d.model_id WHERE d.current_status = 'INSTALLED') AS valor_instalado,
            (SELECT COUNT(*) FROM movement_history WHERE movement_date >= NOW() - INTERVAL '30 days') AS movimientos_30d,
            (SELECT COUNT(*) FROM maintenance WHERE status IN ('PENDING', 'DUE_SOON', 'OVERDUE')) AS mantenimientos_pendientes,
            (SELECT COUNT(*) FROM purchase_orders WHERE status IN ('BORRADOR', 'ENVIADA', 'CONFIRMADA')) AS pos_abiertas
    """
    row = db.execute(text(query)).mappings().first()
    return dict(row) if row else {}


def _get_stock_por_estado(db: Session) -> list[dict]:
    query = """
        SELECT current_status AS estado, COUNT(*) AS cantidad
        FROM devices
        WHERE deleted_at IS NULL
        GROUP BY current_status
        ORDER BY cantidad DESC
    """
    return [dict(r) for r in db.execute(text(query)).mappings().all()]


def _get_movimientos_recientes(db: Session, dias: int = 7) -> list[dict]:
    query = """
        SELECT
            mt.name AS movement_type,
            COUNT(*) AS cantidad,
            DATE(mh.movement_date) AS fecha
        FROM movement_history mh
        JOIN movement_types mt ON mt.id = mh.movement_type_id
        WHERE mh.movement_date >= NOW() - INTERVAL '%s days'
        GROUP BY mt.name, DATE(mh.movement_date)
        ORDER BY fecha DESC, cantidad DESC
    """ % dias
    return [dict(r) for r in db.execute(text(query)).mappings().all()]


def _get_mantenimientos_overdue(db: Session) -> list[dict]:
    query = """
        SELECT
            d.serial_number,
            d.qr_code,
            mo.model,
            mp.name AS plan,
            m.due_date,
            CURRENT_DATE - m.due_date AS dias_vencido,
            l.store_name AS ubicacion
        FROM maintenance m
        JOIN devices d ON d.id = m.device_id
        JOIN models mo ON mo.id = d.model_id
        JOIN maintenance_plans mp ON mp.id = m.plan_id
        JOIN locations l ON l.id = d.current_location_id
        WHERE m.status = 'OVERDUE'
        ORDER BY m.due_date ASC
        LIMIT 20
    """
    return [dict(r) for r in db.execute(text(query)).mappings().all()]


def _get_pos_pendientes(db: Session) -> list[dict]:
    query = """
        SELECT
            po.id,
            v.name AS proveedor,
            po.status,
            po.expected_date,
            CURRENT_DATE - po.expected_date AS dias_retraso
        FROM purchase_orders po
        LEFT JOIN vendors v ON v.id = po.vendor_id
        WHERE po.status IN ('ENVIADA', 'CONFIRMADA')
          AND po.expected_date < CURRENT_DATE
        ORDER BY po.expected_date ASC
        LIMIT 10
    """
    return [dict(r) for r in db.execute(text(query)).mappings().all()]


def dashboard() -> dict[str, Any]:
    """
    La pantalla de inicio: lo que hay que mirar primero.

    UNA SOLA LLAMADA
    ----------------
    El dashboard hace varias consultas. Si cada una fuera un endpoint
    distinto, la pantalla de inicio haria seis pedidos al abrirse, y en un
    celular con 4G eso son seis esperas: la pantalla tarda medio minuto en
    aparecer, o aparece a medias.

    QUE TIENE
    ---------
    - Los KPIs: quantos equipos hay, cuantos estan en deposito, el valor.
    - El stock por estado, para ver de un vistazo como esta repartido.
    - Las alertas: stock bajo y mantenimiento vencido.
    - Los ultimos movimientos: lo que paso hace un rato.
    - Los productos criticos: los que estan por quedarse sin stock.
    """
    db = get_db_session()
    try:
        return {
            "kpis": _get_kpis_principales(db),
            "stock_por_estado": _get_stock_por_estado(db),
            "alertas": _get_alertas_criticas(db),
            "movimientos_recientes": _get_ultimos_movimientos(db, limite=8),
            "productos_criticos": _get_productos_criticos(db, limite=8),
            "mantenimientos_overdue": _get_mantenimientos_overdue(db),
            # Las alertas de stock bajo PERSISTIDAS: el producto bajo minimo,
            # desde cuando, y si la reposicion llega a tiempo. El calculo
            # (`productos_criticos`) dice QUE falta; esto dice desde CUANDO y
            # si hay que correr.
            "alertas_stock": _get_alertas_stock(db, limite=8),
            # El valor libro de la flota: lo que vale HOY.
            "valor_libro": _get_valor_libro(db),
        }
    finally:
        db.close()


def _get_alertas_stock(db: Session, limite: int = 8) -> list[dict]:
    """
    Las alertas de stock bajo que estan ABIERTAS.

    Que el producto este bajo el minimo ya lo dice `productos_criticos`. Lo
    que NO decia es DESDE CUANDO: sin esa fecha, el operador ve "6 productos
    por reponer" y no sabe si hay que comprar hoy o el jueves.
    """
    filas = db.execute(
        text(
            """
            SELECT
                a.id,
                a.sku,
                a.producto,
                a.ubicacion,
                a.minimo,
                a.actual,
                a.faltante,
                a.lead_time_days,
                a.created_at,
                GREATEST(0, EXTRACT(DAYS FROM (NOW() - a.created_at))::INT) AS dias_bajo_minimo,
                CASE
                    WHEN a.lead_time_days IS NULL OR a.lead_time_days <= 0 THEN FALSE
                    ELSE EXTRACT(DAYS FROM (NOW() - a.created_at))::INT > a.lead_time_days
                END AS reposicion_tarde
            FROM stock_alerts a
            WHERE a.resuelto = FALSE AND a.model_id IS NOT NULL
            ORDER BY reposicion_tarde DESC, dias_bajo_minimo DESC, a.faltante DESC
            LIMIT :limite
            """
        ),
        {"limite": limite},
    ).mappings().all()
    return [{**dict(f), "id": str(f["id"])} for f in filas]


def _get_valor_libro(db: Session) -> dict:
    """
    El valor libro de la flota, en un solo numero.

    Es la pregunta que hace el cliente: "cuanto vale mi flota hoy". Se
    calcula con la misma formula del reporte (`reporting/valor_libro.py`),
    pero aqui en SQL y sin el detalle, porque el tablero necesita el numero,
    no la lista.
    """
    fila = db.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (WHERE d.purchase_value IS NOT NULL) AS equipos_con_precio,
                COALESCE(SUM(d.purchase_value), 0) AS costo_total,
                COALESCE(SUM(
                    CASE
                        WHEN d.purchase_value IS NULL OR m.useful_life_months IS NULL
                             OR m.useful_life_months <= 0 THEN 0
                        ELSE LEAST(
                            d.purchase_value,
                            GREATEST(0, (
                                (EXTRACT(YEAR FROM AGE(CURRENT_DATE, d.purchase_date))::INT * 12)
                                + EXTRACT(MONTH FROM AGE(CURRENT_DATE, d.purchase_date))::INT
                            )) * (d.purchase_value / m.useful_life_months)
                        )
                    END
                ), 0) AS depreciacion_acumulada,
                COALESCE(SUM(
                    CASE
                        WHEN d.purchase_value IS NULL THEN 0
                        WHEN m.useful_life_months IS NULL OR m.useful_life_months <= 0
                            THEN d.purchase_value
                        ELSE GREATEST(
                            0,
                            d.purchase_value - LEAST(
                                d.purchase_value,
                                GREATEST(0, (
                                    (EXTRACT(YEAR FROM AGE(CURRENT_DATE, d.purchase_date))::INT * 12)
                                    + EXTRACT(MONTH FROM AGE(CURRENT_DATE, d.purchase_date))::INT
                                )) * (d.purchase_value / m.useful_life_months)
                            )
                        )
                    END
                ), 0) AS valor_libro_total
            FROM devices d
            JOIN models m ON m.id = d.model_id
            WHERE d.deleted_at IS NULL AND m.deleted_at IS NULL
            """
        )
    ).mappings().one()

    datos = dict(fila)
    costo = float(datos.get("costo_total") or 0)
    depreciado = float(datos.get("depreciacion_acumulada") or 0)
    datos["costo_total"] = round(costo, 2)
    datos["depreciacion_acumulada"] = round(depreciado, 2)
    datos["valor_libro_total"] = round(float(datos.get("valor_libro_total") or 0), 2)
    datos["depreciacion_pct"] = round(100.0 * depreciado / costo, 1) if costo > 0 else None
    return datos


def _get_ultimos_movimientos(db: Session, limite: int = 8) -> list[dict]:
    """Los ultimos movimientos, con el equipo y de donde a donde."""
    query = """
        SELECT
            mh.movement_date,
            mt.label AS movimiento,
            d.serial_number,
            mo.sku,
            mo.brand,
            mo.model,
            lo.store_name AS origen,
            ld.store_name AS destino
        FROM movement_history mh
        JOIN devices d ON d.id = mh.device_id
        JOIN models mo ON mo.id = d.model_id
        JOIN movement_types mt ON mt.id = mh.movement_type_id
        LEFT JOIN locations lo ON lo.id = mh.origin_location_id
        LEFT JOIN locations ld ON ld.id = mh.destination_location_id
        ORDER BY mh.movement_date DESC
        LIMIT :limite
    """
    return [dict(r) for r in db.execute(text(query), {"limite": limite}).mappings().all()]


def _get_productos_criticos(db: Session, limite: int = 8) -> list[dict]:
    """
    Los productos que estan por quedarse sin stock.

    "Critico" es "por debajo del minimo" o "sin unidades en deposito": un
    producto con stock 0 en el deposito y 5 instalados no es un problema de
    reposicion, es un producto que no se puede entregar.

    USA LA MISMA CONSULTA QUE EL MODAL (stock/productos_por_reponer): la
    tarjeta del tablero y el detalle que se abre al pulsarla tienen que decir
    LO MISMO. Con dos criterios distintos, el operador pulsaba un producto y
    el modal le decia que no habia nada que reponer.
    """
    from src.modules.stock import service as stock_service

    return stock_service.productos_por_reponer(limite=limite)


def estadisticas_inventario() -> dict[str, Any]:
    """KPIs numéricos puros para dashboards."""
    db = get_db_session()
    try:
        return _get_kpis_principales(db)
    finally:
        db.close()


def rotacion_modelos(dias: int = 30, top: int = 10) -> list[dict]:
    """Modelos con más movimientos en período."""
    db = get_db_session()
    try:
        query = """
            SELECT
                m.id AS model_id,
                m.sku,
                m.model,
                m.brand,
                COUNT(mh.id) AS total_movimientos,
                COUNT(mh.id) FILTER (WHERE mt.name IN ('OUT_TO_CLIENT', 'WAREHOUSE_ENTRY')) AS entradas_salidas,
                COUNT(mh.id) FILTER (WHERE mt.name = 'INSTALLATION') AS instalaciones
            FROM models m
            JOIN devices d ON d.model_id = m.id
            JOIN movement_history mh ON mh.device_id = d.id
            JOIN movement_types mt ON mt.id = mh.movement_type_id
            WHERE mh.movement_date >= NOW() - INTERVAL '%s days'
            GROUP BY m.id, m.sku, m.model, m.brand
            ORDER BY total_movimientos DESC
            LIMIT %s
        """ % (dias, top)
        return [dict(r) for r in db.execute(text(query)).mappings().all()]
    finally:
        db.close()


def tiempo_instalacion(model_id: int | None = None, cliente_id: int | None = None, dias: int = 90) -> list[dict]:
    """Promedio días OUT_TO_CLIENT → INSTALLATION."""
    db = get_db_session()
    try:
        query = """
            WITH out_events AS (
                SELECT mh.device_id, mh.movement_date AS fecha_out
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                WHERE mt.name = 'OUT_TO_CLIENT'
                  AND mh.movement_date >= NOW() - INTERVAL '%s days'
            ),
            install_events AS (
                SELECT mh.device_id, mh.movement_date AS fecha_inst
                FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                WHERE mt.name = 'INSTALLATION'
                  AND mh.movement_date >= NOW() - INTERVAL '%s days'
            )
            SELECT
                mo.id AS model_id,
                mo.sku,
                mo.model,
                mo.brand,
                c.commercial_name AS cliente,
                AVG(i.fecha_inst - o.fecha_out) AS promedio_dias,
                COUNT(*) AS instalaciones
            FROM out_events o
            JOIN install_events i ON i.device_id = o.device_id AND i.fecha_inst > o.fecha_out
            JOIN devices d ON d.id = o.device_id
            JOIN models mo ON mo.id = d.model_id
            LEFT JOIN locations l ON l.id = d.current_location_id
            LEFT JOIN clients c ON c.id = l.client_id
        """ % (dias, dias)

        params = {}
        if model_id:
            query += " AND mo.id = :model_id"
            params["model_id"] = model_id
        if cliente_id:
            query += " AND c.id = :cliente_id"
            params["cliente_id"] = cliente_id

        query += " GROUP BY mo.id, mo.sku, mo.model, mo.brand, c.commercial_name ORDER BY instalaciones DESC LIMIT 20"

        return [dict(r) for r in db.execute(text(query), params).mappings().all()]
    finally:
        db.close()