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


LARGO_QR = 150
LARGO_BARCODE = 64


def _codigos_de_etiqueta(sku: str, datos: dict[str, Any]) -> tuple[str, str]:
    """
    Resuelve el QR y el codigo de barras de un producto.

    POR QUE HAY UN CODIGO POR PRODUCTO
    -----------------------------------
    Un producto del maestro es "una notebook tal modelo": hay cinco en el
    deposito. La etiqueta con el QR y el codigo de barras es la del PRODUCTO
    (la de la caja, la del rack, la del producto en la góndola del almacen),
    no la de cada equipo: la de cada equipo es la que ya existe, con el
    `qr_code` de `devices` y su serial.

    POR QUE EL QR NO ES EL SKU
    --------------------------
    El QR lleva un prefijo `PRD-` para que se distinga de un codigo de
    etiqueta de EQUIPO (`INV-...`) de un vistazo. Los dos conviven en el mismo
    deposito, y un operador que escanea con la app tiene que poder decir de
    un vistazo si esta mirando una caja o un equipo.

    El codigo de barras, en cambio, es el SKU: es lo que ya esta escrito en la
    caja y en el sistema del proveedor, y repetirlo es mejor que inventar un
    numero que el proveedor no conoce.

    Los dos son editables: si el proveedor ya tiene un codigo propio, se usa
    ese. Lo que NO se puede es que dos productos compartan el mismo codigo:
    es lo que hace inutil una etiqueta.
    """
    qr_code = (datos.get("qr_code") or "").strip() or f"PRD-{sku}"
    barcode = (datos.get("barcode") or "").strip() or sku

    if len(qr_code) > LARGO_QR:
        raise ProductoInvalido(
            f"El codigo QR no puede superar los {LARGO_QR} caracteres."
        )
    if len(barcode) > LARGO_BARCODE:
        raise ProductoInvalido(
            f"El codigo de barras no puede superar los {LARGO_BARCODE} caracteres."
        )

    return qr_code, barcode


def obtener_codigos_producto(producto_id: int) -> dict[str, Any]:
    """
    Los datos de etiqueta de un producto: lo que va codificado y lo que se
    imprime.

    Lanza `ProductoInvalido` si el producto no existe o fue eliminado.
    """
    db = get_db_session()
    try:
        fila = db.execute(
            text(
                """
                SELECT id, sku, brand, model, variant, qr_code, barcode
                FROM models
                WHERE id = :id AND deleted_at IS NULL
                """
            ),
            {"id": producto_id},
        ).mappings().first()
        if not fila:
            raise ProductoInvalido("El producto no existe o ya fue eliminado.")
        return dict(fila)
    finally:
        db.close()


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
                    m.qr_code,
                    m.barcode,
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

    sku = (datos["sku"] or "").strip()
    qr_code, barcode = _codigos_de_etiqueta(sku, datos)

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
                        qr_code, barcode,
                        is_active
                    )
                    VALUES (
                        :category_id, :brand, :model, :sku, :variant,
                        :unit_cost, :list_price, :currency,
                        :min_stock, :max_stock, :reorder_multiple, :lead_time_days,
                        :qr_code, :barcode,
                        TRUE
                    )
                    RETURNING id
                    """
                ),
                {
                    "category_id": categoria_id,
                    "brand": datos["brand"],
                    "model": datos["model"],
                    "sku": sku,
                    "variant": datos.get("variant"),
                    "unit_cost": datos.get("unit_cost"),
                    "list_price": datos.get("list_price"),
                    "currency": datos.get("currency") or "USD",
                    "min_stock": datos.get("min_stock", 0),
                    "max_stock": datos.get("max_stock", 100),
                    "reorder_multiple": datos.get("reorder_multiple", 1),
                    "lead_time_days": datos.get("lead_time_days", 14),
                    "qr_code": qr_code,
                    "barcode": barcode,
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
            if "barcode" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un producto con el codigo de barras '{barcode}'."
                ) from exc
            if "qr_code" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un producto con el codigo QR '{qr_code}'."
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
                       m.min_stock, m.max_stock, m.is_active,
                       m.qr_code, m.barcode
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


def actualizar_producto(producto_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """
    Actualizacion de un producto del maestro.

    No se permite cambiar el SKU (es la clave historica con la que se compro
    y se conto stock). Si se quiere otro SKU, se da de alta un producto nuevo.
    """
    categoria_id = datos.get("category_id")
    if not categoria_id:
        raise ProductoInvalido("La categoria es obligatoria.")

    # Los codigos de etiqueta SOLO se tocan si vienen en el body: el COALESCE
    # de abajo deja el valor anterior cuando no vienen. Si no, una edicion
    # que no toca la etiqueta la borraria, y con ella la ability de reimprimirla.
    qr_code = (datos.get("qr_code") or "").strip() or None
    barcode = (datos.get("barcode") or "").strip() or None

    db = get_db_session()
    try:
        existe = db.execute(
            text("SELECT 1 FROM categories WHERE id = :id AND is_active = TRUE"),
            {"id": categoria_id},
        ).first()
        if not existe:
            raise ProductoInvalido("La categoria seleccionada no existe.")

        try:
            db.execute(
                text(
                    """
                    UPDATE models SET
                        category_id = :category_id,
                        brand = :brand,
                        model = :model,
                        variant = :variant,
                        unit_cost = :unit_cost,
                        list_price = :list_price,
                        currency = :currency,
                        min_stock = :min_stock,
                        max_stock = :max_stock,
                        reorder_multiple = :reorder_multiple,
                        lead_time_days = :lead_time_days,
                        qr_code = COALESCE(:qr_code, qr_code),
                        barcode = COALESCE(:barcode, barcode),
                        is_active = :is_active
                    WHERE id = :id AND deleted_at IS NULL
                    """
                ),
                {
                    "id": producto_id,
                    "category_id": categoria_id,
                    "brand": datos["brand"],
                    "model": datos["model"],
                    "variant": datos.get("variant"),
                    "unit_cost": datos.get("unit_cost"),
                    "list_price": datos.get("list_price"),
                    "currency": datos.get("currency") or "USD",
                    "min_stock": datos.get("min_stock", 0),
                    "max_stock": datos.get("max_stock", 100),
                    "reorder_multiple": datos.get("reorder_multiple", 1),
                    "lead_time_days": datos.get("lead_time_days", 14),
                    "qr_code": qr_code,
                    "barcode": barcode,
                    "is_active": datos.get("is_active", True),
                },
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            detalle = str(getattr(exc, "orig", exc))
            if "barcode" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un producto con el codigo de barras '{barcode}'."
                ) from exc
            if "qr_code" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un producto con el codigo QR '{qr_code}'."
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
                       m.min_stock, m.max_stock, m.is_active,
                       m.qr_code, m.barcode
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


def eliminar_producto(producto_id: int) -> None:
    """
    Borrado logico de un producto. Solo se permite si NO tiene movimientos
    asociados. Si tiene movimientos, se debe descontinuar (is_active = FALSE)
    para no perder el historial.
    """
    db = get_db_session()
    try:
        # Verificar que el producto exista y no esté ya borrado
        fila = db.execute(
            text("SELECT id, brand, model, sku FROM models WHERE id = :id AND deleted_at IS NULL"),
            {"id": producto_id},
        ).mappings().first()
        if not fila:
            raise ProductoInvalido("El producto no existe o ya fue eliminado.")

        # Verificar si tiene movimientos asociados
        movimientos = db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM movement_history mh
                JOIN devices d ON d.id = mh.device_id
                WHERE d.model_id = :model_id
                """
            ),
            {"model_id": producto_id},
        ).scalar()

        if movimientos > 0:
            raise ProductoInvalido(
                f"El producto '{fila['brand']} {fila['model']}' tiene {movimientos} movimientos asociados. "
                "No se puede eliminar. Descontinúelo (is_active = FALSE) para mantener el historial."
            )

        # Borrado logico
        db.execute(
            text("UPDATE models SET deleted_at = NOW() WHERE id = :id"),
            {"id": producto_id},
        )
        db.commit()
    finally:
        db.close()


def listar_clientes() -> list[dict[str, Any]]:
    """Clientes activos."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT id, commercial_name, tax_id, contact_name, contact_email, contact_phone
                FROM clients
                WHERE deleted_at IS NULL
                ORDER BY commercial_name
                """
            )
        ).mappings().all()
        return [dict(f) for f in filas]
    finally:
        db.close()


def crear_cliente(datos: dict[str, Any]) -> dict[str, Any]:
    """Alta de cliente."""
    db = get_db_session()
    try:
        try:
            cliente_id = db.execute(
                text(
                    """
                    INSERT INTO clients (commercial_name, tax_id, contact_name, contact_email, contact_phone)
                    VALUES (:commercial_name, :tax_id, :contact_name, :contact_email, :contact_phone)
                    RETURNING id
                    """
                ),
                {
                    "commercial_name": datos["commercial_name"],
                    "tax_id": datos["tax_id"],
                    "contact_name": datos.get("contact_name"),
                    "contact_email": datos.get("contact_email"),
                    "contact_phone": datos.get("contact_phone"),
                },
            ).scalar_one()
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            detalle = str(getattr(exc, "orig", exc))
            if "tax_id" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un cliente con el tax_id '{datos['tax_id']}'."
                ) from exc
            raise

        fila = db.execute(
            text("SELECT id, commercial_name, tax_id, contact_name, contact_email, contact_phone FROM clients WHERE id = :id"),
            {"id": cliente_id},
        ).mappings().one()
        return dict(fila)
    finally:
        db.close()


def actualizar_cliente(cliente_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """Actualizacion de cliente."""
    db = get_db_session()
    try:
        try:
            db.execute(
                text(
                    """
                    UPDATE clients SET
                        commercial_name = :commercial_name,
                        tax_id = :tax_id,
                        contact_name = :contact_name,
                        contact_email = :contact_email,
                        contact_phone = :contact_phone
                    WHERE id = :id AND deleted_at IS NULL
                    """
                ),
                {
                    "id": cliente_id,
                    "commercial_name": datos["commercial_name"],
                    "tax_id": datos["tax_id"],
                    "contact_name": datos.get("contact_name"),
                    "contact_email": datos.get("contact_email"),
                    "contact_phone": datos.get("contact_phone"),
                },
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            detalle = str(getattr(exc, "orig", exc))
            if "tax_id" in detalle:
                raise ProductoInvalido(
                    f"Ya existe un cliente con el tax_id '{datos['tax_id']}'."
                ) from exc
            raise

        fila = db.execute(
            text("SELECT id, commercial_name, tax_id, contact_name, contact_email, contact_phone FROM clients WHERE id = :id"),
            {"id": cliente_id},
        ).mappings().one()
        return dict(fila)
    finally:
        db.close()


def eliminar_cliente(cliente_id: int) -> None:
    """Borrado logico de cliente."""
    db = get_db_session()
    try:
        db.execute(
            text("UPDATE clients SET deleted_at = NOW() WHERE id = :id AND deleted_at IS NULL"),
            {"id": cliente_id},
        )
        db.commit()
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
