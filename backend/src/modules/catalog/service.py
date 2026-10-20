"""
Servicio de Catálogo (maestro de productos).

AQUI VIVE EL "MAESTRO DE PRODUCTOS"
-----------------------------------
El maestro de productos de este sistema es la tabla `models`. No existe una tabla
`products` separada: hubo una migracion que la creaba, pero ningun modulo del
backend la usa (cero referencias a `products`/`product_id`), asi que quedo
archivada. El diseño real —el que consultan stock, purchasing y reporting— tiene
el SKU, el stock minimo/maximo y el costo en `models`.

Un "producto" del maestro es una combinacion marca + modelo + SKU, con su
categoria, sus puntos de reposicion y su costo. Cada equipo fisico (`devices`)
apunta a un producto (`model_id`), y de ahi sale el stock agregado.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.db.session import get_db_session


class ProductoInvalido(ValueError):
    """Regla de negocio del catalogo (SKU repetido, categoria inexistente)."""


def listar_categorias() -> list[dict[str, Any]]:
    """Categorias activas, con su codigo corto para armar SKUs."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT id, name, code, icon, color, sort_order, is_active
                FROM categories
                ORDER BY sort_order, name
                """
            )
        ).mappings().all()
        return [dict(f) for f in filas]
    finally:
        db.close()


def listar_productos(
    *,
    busqueda: str | None = None,
    categoria_id: int | None = None,
    solo_activos: bool = True,
) -> list[dict[str, Any]]:
    """
    Maestro de productos.

    El `deleted_at IS NULL` es el borrado logico: un producto que se dejo de
    vender no se borra, porque los equipos historicos siguen apuntandole.
    """
    condiciones = ["m.deleted_at IS NULL"]
    parametros: dict[str, Any] = {}

    if solo_activos:
        condiciones.append("m.is_active = TRUE")
    if categoria_id:
        condiciones.append("m.category_id = :categoria_id")
        parametros["categoria_id"] = categoria_id
    if busqueda:
        condiciones.append(
            "(m.brand ILIKE :busqueda OR m.model ILIKE :busqueda OR m.sku ILIKE :busqueda)"
        )
        parametros["busqueda"] = f"%{_escapar_like(busqueda)}%"

    where = " AND ".join(condiciones)

    db = get_db_session()
    try:
        filas = db.execute(
            text(
                f"""
                SELECT
                    m.id,
                    m.sku,
                    m.brand,
                    m.model,
                    m.variant,
                    m.category_id,
                    c.name AS categoria,
                    c.code AS categoria_code,
                    m.unit_cost,
                    m.list_price,
                    m.currency,
                    m.min_stock,
                    m.max_stock,
                    m.reorder_multiple,
                    m.lead_time_days,
                    m.is_active,
                    m.is_serialized,
                    (SELECT COUNT(*) FROM devices d
                      WHERE d.model_id = m.id AND d.deleted_at IS NULL) AS equipos
                FROM models m
                JOIN categories c ON c.id = m.category_id
                WHERE {where}
                ORDER BY m.brand, m.model
                """
            ),
            parametros,
        ).mappings().all()
        return [dict(f) for f in filas]
    finally:
        db.close()


def crear_producto(datos: dict[str, Any]) -> dict[str, Any]:
    """
    Alta de un producto del maestro.

    `sku` es obligatorio y unico: es la clave con la que se compra, se cuenta y
    se reporta. Sin SKU, dos altas del "mismo" producto se vuelven dos filas
    distintas y el stock deja de agregar.
    """
    categoria_id = datos.get("category_id")
    if not categoria_id:
        raise ProductoInvalido("La categoria es obligatoria.")

    db = get_db_session()
    try:
        existe = db.execute(
            text("SELECT 1 FROM categories WHERE id = :id AND is_active = TRUE"),
            {"id": categoria_id},
        ).first()
        if not existe:
            raise ProductoInvalido("La categoria seleccionada no existe.")

        try:
            producto_id = db.execute(
                text(
                    """
                    INSERT INTO models (
                        category_id, brand, model, sku, variant,
                        unit_cost, list_price, currency,
                        min_stock, max_stock, reorder_multiple, lead_time_days,
                        is_active
                    )
                    VALUES (
                        :category_id, :brand, :model, :sku, :variant,
                        :unit_cost, :list_price, :currency,
                        :min_stock, :max_stock, :reorder_multiple, :lead_time_days,
                        TRUE
                    )
                    RETURNING id
                    """
                ),
                {
                    "category_id": categoria_id,
                    "brand": datos["brand"],
                    "model": datos["model"],
                    "sku": datos["sku"],
                    "variant": datos.get("variant"),
                    "unit_cost": datos.get("unit_cost"),
                    "list_price": datos.get("list_price"),
                    "currency": datos.get("currency") or "USD",
                    "min_stock": datos.get("min_stock", 0),
                    "max_stock": datos.get("max_stock", 100),
                    "reorder_multiple": datos.get("reorder_multiple", 1),
                    "lead_time_days": datos.get("lead_time_days", 14),
                },
            ).scalar_one()
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            detalle = str(getattr(exc, "orig", exc))
            if "sku" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un producto con el SKU '{datos['sku']}'."
                ) from exc
            if "uq_models_brand_model" in detalle:
                raise ProductoInvalido(
                    f"Ya existe el modelo '{datos['brand']} {datos['model']}'."
                ) from exc
            raise

        fila = db.execute(
            text(
                """
                SELECT m.id, m.sku, m.brand, m.model, m.variant,
                       m.category_id, c.name AS categoria,
                       m.unit_cost, m.list_price, m.currency,
                       m.min_stock, m.max_stock, m.is_active
                FROM models m
                JOIN categories c ON c.id = m.category_id
                WHERE m.id = :id
                """
            ),
            {"id": producto_id},
        ).mappings().one()
        return dict(fila)
    finally:
        db.close()


def listar_proveedores() -> list[dict[str, Any]]:
    """Proveedores activos, para el select de la orden de compra."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT id, name, code, email, phone, contact_person, is_active
                FROM vendors
                WHERE is_active = TRUE
                ORDER BY name
                """
            )
        ).mappings().all()
        return [dict(f) for f in filas]
    finally:
        db.close()


def crear_proveedor(datos: dict[str, Any]) -> dict[str, Any]:
    """Alta de proveedor. El nombre es lo unico obligatorio."""
    db = get_db_session()
    try:
        try:
            proveedor_id = db.execute(
                text(
                    """
                    INSERT INTO vendors (name, code, email, phone, contact_person, is_active)
                    VALUES (:name, :code, :email, :phone, :contact_person, TRUE)
                    RETURNING id
                    """
                ),
                {
                    "name": datos["name"],
                    "code": datos.get("code"),
                    "email": datos.get("email"),
                    "phone": datos.get("phone"),
                    "contact_person": datos.get("contact_person"),
                },
            ).scalar_one()
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            detalle = str(getattr(exc, "orig", exc))
            if "code" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un proveedor con el codigo '{datos.get('code')}'."
                ) from exc
            raise

        fila = db.execute(
            text("SELECT id, name, code, email, phone, contact_person, is_active FROM vendors WHERE id = :id"),
            {"id": proveedor_id},
        ).mappings().one()
        return dict(fila)
    finally:
        db.close()


def _escapar_like(texto: str) -> str:
    r"""Escapa los comodines de LIKE para que se busquen como texto literal."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
