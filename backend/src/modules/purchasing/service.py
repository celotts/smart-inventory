"""
Servicio de Compras - Órdenes de compra, recepción, sugerencias de reorden.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import get_db_session


def crear_orden(
    vendor_id: int,
    lineas: list[dict],
    expected_date: str | None = None,
    observations: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Crea PO en BORRADOR con líneas.
    Cada línea: model_id, cantidad, unit_cost.
    """
    db = get_db_session()
    try:
        user_uuid = UUID(user_id) if user_id else None
        po_id = None

        # Insertar cabecera
        query_po = """
            INSERT INTO purchase_orders (vendor_id, expected_date, observations, created_by, status)
            VALUES (:vendor_id, :expected_date, :observations, :created_by, 'BORRADOR')
            RETURNING id
        """
        res = db.execute(text(query_po), {
            "vendor_id": vendor_id,
            "expected_date": expected_date,
            "observations": observations,
            "created_by": user_uuid,
        })
        po_id = res.scalar()
        if not po_id:
            raise ValueError("No se pudo crear la orden de compra")

        # Insertar líneas
        for i, linea in enumerate(lineas):
            query_line = """
                INSERT INTO purchase_order_lines (po_id, model_id, cantidad, unit_cost, sort_order)
                VALUES (:po_id, :model_id, :cantidad, :unit_cost, :sort_order)
            """
            db.execute(text(query_line), {
                "po_id": po_id,
                "model_id": linea["model_id"],
                "cantidad": linea["cantidad"],
                "unit_cost": linea.get("unit_cost", 0),
                "sort_order": i,
            })

        db.commit()
        return {"po_id": po_id, "status": "BORRADOR", "lineas_creadas": len(lineas)}
    finally:
        db.close()


def listar_ordenes(
    estado: str | None = None,
    vendor_id: int | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict[str, Any]]:
    db = get_db_session()
    try:
        query = """
            SELECT
                po.id, po.vendor_id, v.name AS vendor_name,
                po.status, po.expected_date, po.observations,
                po.created_at, po.created_by,
                (SELECT SUM(cantidad * unit_cost) FROM purchase_order_lines WHERE po_id = po.id) AS total_estimado
            FROM purchase_orders po
            LEFT JOIN vendors v ON v.id = po.vendor_id
            WHERE 1=1
        """
        params = {}
        if estado:
            query += " AND po.status = :estado"
            params["estado"] = estado
        if vendor_id:
            query += " AND po.vendor_id = :vendor_id"
            params["vendor_id"] = vendor_id
        if desde:
            query += " AND po.created_at >= :desde"
            params["desde"] = desde
        if hasta:
            query += " AND po.created_at <= :hasta"
            params["hasta"] = hasta
        query += " ORDER BY po.created_at DESC LIMIT 100"

        result = db.execute(text(query), params)
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def obtener_orden(po_id: int) -> dict[str, Any]:
    """
    Una orden con sus líneas y lo que queda pendiente de recibir.

    `pendiente` es la resta `cantidad - cantidad_recibida`. El frontend la usa
    para no ofrecer "recibir 5" cuando solo faltan 3: mostrar la cuenta que el
    operador tendría que hacer a mano es pedirle que se equivoque.
    """
    db = get_db_session()
    try:
        orden = db.execute(
            text(
                """
                SELECT po.id, po.vendor_id, v.name AS vendor_name,
                       po.status, po.expected_date, po.observations,
                       po.created_at, po.created_by
                FROM purchase_orders po
                LEFT JOIN vendors v ON v.id = po.vendor_id
                WHERE po.id = :po_id
                """
            ),
            {"po_id": po_id},
        ).mappings().first()
        if not orden:
            raise ValueError(f"La orden de compra {po_id} no existe.")

        lineas = db.execute(
            text(
                """
                SELECT pol.id, pol.model_id, pol.cantidad, pol.cantidad_recibida,
                       (pol.cantidad - pol.cantidad_recibida) AS pendiente,
                       pol.unit_cost,
                       m.sku, m.brand, m.model AS model_name
                FROM purchase_order_lines pol
                JOIN models m ON m.id = pol.model_id
                WHERE pol.po_id = :po_id
                ORDER BY pol.sort_order, pol.id
                """
            ),
            {"po_id": po_id},
        ).mappings().all()

        resultado = dict(orden)
        resultado["lineas"] = [dict(l) for l in lineas]
        return resultado
    finally:
        db.close()


def recibir_orden(
    po_id: int,
    lineas: list[dict],
    observations: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Recibe líneas de PO. Para cada línea:
    - Actualiza purchase_order_lines.cantidad_recibida
    - Crea devices (uno por unidad) con serial único
    - Registra movimiento WAREHOUSE_ENTRY por cada device
    - Si todas las líneas completas → PO status = RECIBIDA
    """
    db = get_db_session()
    try:
        user_uuid = UUID(user_id) if user_id else None
        devices_creados = 0

        # Ubicación de destino: el depósito. Se resuelve por la primera ubicación
        # activa en vez de hardcodear `1`. El `1` original asumía que el depósito
        # era siempre la primera fila, y en esta base las ubicaciones arrancan en
        # id=4: la recepción fallaba con FK violation contra una ubicación que no
        # existía.
        deposito = db.execute(
            text("SELECT id FROM locations WHERE is_active = TRUE ORDER BY id LIMIT 1")
        ).scalar()
        if deposito is None:
            raise ValueError(
                "No hay ninguna ubicación activa para recibir la mercancía. "
                "Creá una ubicación (depósito) antes de recibir una orden."
            )

        for linea in lineas:
            po_line_id = linea["po_line_id"]
            cantidad = linea["cantidad_recibida"]
            serials_provided = linea.get("serial_numbers", [])

            # Verificar línea existe y stock pendiente
            q = """
                SELECT pol.id, pol.model_id, pol.cantidad, pol.cantidad_recibida
                FROM purchase_order_lines pol
                WHERE pol.id = :pol_id AND pol.po_id = :po_id
            """
            pol = db.execute(text(q), {"pol_id": po_line_id, "po_id": po_id}).mappings().first()
            if not pol:
                raise ValueError(f"Línea {po_line_id} no existe en PO {po_id}")

            pendiente = pol["cantidad"] - pol["cantidad_recibida"]
            if cantidad > pendiente:
                raise ValueError(f"Cantidad {cantidad} excede pendiente {pendiente}")

            # Obtener info del modelo para serial prefix
            model = db.execute(text("SELECT sku, brand, model FROM models WHERE id = :mid"), {"mid": pol["model_id"]}).mappings().first()
            if not model:
                raise ValueError(f"Modelo {pol['model_id']} no encontrado")

            # Generar serials
            for i in range(cantidad):
                serial = serials_provided[i] if i < len(serials_provided) else f"{model['sku']}-{uuid4().hex[:8].upper()}"
                qr = f"INV-{uuid4().hex[:8]}"

                # Crear device
                q_dev = """
                    INSERT INTO devices (serial_number, qr_code, model_id, current_status, current_location_id, is_warehouse, warehouse_entry_date)
                    VALUES (:serial, :qr, :model_id, 'IN_WAREHOUSE', :destino, TRUE, NOW())
                    RETURNING id
                """
                dev_id = db.execute(
                    text(q_dev),
                    {"serial": serial, "qr": qr, "model_id": pol["model_id"], "destino": deposito},
                ).scalar()

                # Movimiento WAREHOUSE_ENTRY.
                #
                # Las columnas son las REALES de movement_history: `user_id` (UUID),
                # no `registered_by`, y `movement_type_id` (la FK a
                # movement_types), no el ENUM `movement_type` —que la migracion
                # 0010 dropeo—. El INSERT original nombraba columnas que no
                # existen en la tabla.
                q_mov = """
                    INSERT INTO movement_history (
                        device_id, movement_type_id, movement_date,
                        origin_location_id, destination_location_id,
                        scan_latitude, scan_longitude, is_valid_location, user_id
                    )
                    VALUES (
                        :dev_id,
                        (SELECT id FROM movement_types WHERE name = 'WAREHOUSE_ENTRY'),
                        NOW(),
                        NULL, :destino,
                        NULL, NULL, TRUE, :user_id
                    )
                """
                db.execute(text(q_mov), {
                    "dev_id": dev_id,
                    "destino": deposito,
                    "user_id": UUID(user_id) if user_id else None,
                })
                devices_creados += 1

            # Actualizar cantidad_recibida en línea
            db.execute(text("UPDATE purchase_order_lines SET cantidad_recibida = cantidad_recibida + :cant WHERE id = :pid"),
                       {"cant": cantidad, "pid": po_line_id})

        # Verificar si PO completa
        q_check = "SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = :po_id AND cantidad_recibida < cantidad"
        pendientes = db.execute(text(q_check), {"po_id": po_id}).scalar()
        if pendientes == 0:
            db.execute(text("UPDATE purchase_orders SET status = 'RECIBIDA' WHERE id = :po_id"), {"po_id": po_id})

        if observations:
            db.execute(text("UPDATE purchase_orders SET observations = COALESCE(observations, '') || '\n' || :obs WHERE id = :po_id"),
                       {"obs": observations, "po_id": po_id})

        db.commit()
        return {"po_id": po_id, "devices_creados": devices_creados, "status": "RECIBIDA" if pendientes == 0 else "PARCIAL"}
    finally:
        db.close()


def sugerir_reorden() -> list[dict[str, Any]]:
    """Basado en v_low_stock: genera líneas sugeridas para PO."""
    db = get_db_session()
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
                m.preferred_vendor_id,
                sl.location_id,
                loc.store_name AS location_name,
                sl.available_count,
                GREATEST(m.min_stock - sl.available_count, 0) AS deficit,
                CEIL((m.max_stock - sl.available_count) / m.reorder_multiple::NUMERIC) * m.reorder_multiple AS suggested_qty,
                m.unit_cost
            FROM stock_levels sl
            JOIN models m ON m.id = sl.model_id AND m.is_active
            JOIN locations loc ON loc.id = sl.location_id
            WHERE sl.available_count <= m.min_stock
               OR (m.max_stock > 0 AND sl.available_count >= m.max_stock)
            ORDER BY sl.available_count ASC
        """
        result = db.execute(text(query))
        rows = result.mappings().all()

        # Agrupar por vendor para sugerir POs
        por_vendor = {}
        for r in rows:
            vid = r["preferred_vendor_id"] or 1
            if vid not in por_vendor:
                por_vendor[vid] = {"vendor_id": vid, "lineas": [], "total_estimado": 0}
            por_vendor[vid]["lineas"].append({
                "model_id": r["model_id"],
                "cantidad": int(r["suggested_qty"]),
                "unit_cost": float(r["unit_cost"] or 0),
            })
            por_vendor[vid]["total_estimado"] += int(r["suggested_qty"]) * float(r["unit_cost"] or 0)

        return list(por_vendor.values())
    finally:
        db.close()