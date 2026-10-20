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

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.core.arbol import anidar, rutas_por_padres
from src.db.session import get_db_session
from src.modules.locations.service import ids_descendientes_categoria


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
    """
    Categorias activas, con su codigo corto para armar SKUs y su RUTA.

    La ruta ("Portatiles > Notebooks") es lo que la interfaz muestra: un
    operador que tiene 40 equipos y 7 categorias necesita ver de que rama
    sale cada cosa, no un nombre suelto que se repite.
    """
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT id, name, code, icon, color, sort_order, is_active, parent_id
                FROM categories
                WHERE deleted_at IS NULL
                ORDER BY sort_order, name
                """
            )
        ).mappings().all()

        datos = [dict(f) for f in filas]
        rutas = rutas_por_padres(datos, nombre="name")
        for d in datos:
            d["ruta"] = rutas.get(d["id"], d["name"])
            d["nivel"] = d["ruta"].count(">")
        return datos
    finally:
        db.close()


def arbol_categorias() -> list[dict[str, Any]]:
    """Las categorias en arbol, con sus hijas."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT id, name, code, icon, color, sort_order, is_active, parent_id
                FROM categories
                WHERE deleted_at IS NULL
                ORDER BY sort_order, name
                """
            )
        ).mappings().all()
    finally:
        db.close()

    return anidar([dict(f) for f in filas], nombre="name")


def _verificar_categoria_padre(parent_id: int, categoria_id: int | None = None) -> None:
    """
    El padre tiene que existir, y no puede ser uno mismo ni un descendiente.

    Un ciclo en `parent_id` cuelga la categoria de si misma: el filtro por
    subcategorias se cuda solo y la pantalla de categorias deja de pintar.
    """
    if categoria_id is not None and int(parent_id) == int(categoria_id):
        raise ProductoInvalido("Una categoria no puede ser su propia categoria padre.")

    db = get_db_session()
    try:
        existe = db.execute(
            text("SELECT 1 FROM categories WHERE id = :id AND deleted_at IS NULL"),
            {"id": parent_id},
        ).first()
        if not existe:
            raise ProductoInvalido("La categoria padre no existe.")
    finally:
        db.close()

    if categoria_id is not None:
        descendientes = ids_descendientes_categoria(categoria_id)
        if int(parent_id) in descendientes:
            raise ProductoInvalido(
                "Una categoria no puede colgar de una de sus propias subcategorias."
            )


def crear_categoria(datos: dict[str, Any]) -> dict[str, Any]:
    """
    Alta de una categoria.

    `parent_id` es lo que hace la jerarquia: una categoria sin padre es una
    rama, y con padre es una subcategoria.
    """
    nombre = (datos.get("name") or "").strip()
    if not nombre:
        raise ProductoInvalido("El nombre de la categoria es obligatorio.")

    parent_id = datos.get("parent_id")
    if parent_id:
        _verificar_categoria_padre(parent_id)

    db = get_db_session()
    try:
        categoria_id = db.execute(
            text(
                """
                INSERT INTO categories (name, description, code, icon, color, sort_order, is_active, parent_id)
                VALUES (:name, :description, :code, :icon, :color, :sort_order, :is_active, :parent_id)
                RETURNING id
                """
            ),
            {
                "name": nombre,
                "description": datos.get("description"),
                "code": (datos.get("code") or "").strip().upper() or None,
                "icon": datos.get("icon"),
                "color": datos.get("color"),
                "sort_order": datos.get("sort_order") or 0,
                "is_active": datos.get("is_active", True),
                "parent_id": parent_id,
            },
        ).scalar_one()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detalle = str(getattr(exc, "orig", exc))
        if "name" in detalle:
            raise ProductoInvalido(f"Ya existe una categoria con el nombre '{nombre}'.") from exc
        if "code" in detalle:
            raise ProductoInvalido(
                f"Ya existe una categoria con el codigo '{datos.get('code')}'."
            ) from exc
        raise
    finally:
        db.close()

    for categoria in listar_categorias():
        if categoria["id"] == categoria_id:
            return categoria
    raise ProductoInvalido("La categoria se creo pero no se pudo leer.")


def actualizar_categoria(categoria_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """Edicion de una categoria."""
    if "parent_id" in datos and datos["parent_id"]:
        _verificar_categoria_padre(datos["parent_id"], categoria_id)

    db = get_db_session()
    try:
        db.execute(
            text(
                """
                UPDATE categories SET
                    name = COALESCE(:name, name),
                    description = COALESCE(:description, description),
                    code = COALESCE(:code, code),
                    icon = COALESCE(:icon, icon),
                    color = COALESCE(:color, color),
                    sort_order = COALESCE(:sort_order, sort_order),
                    is_active = COALESCE(:is_active, is_active),
                    parent_id = :parent_id
                WHERE id = :id AND deleted_at IS NULL
                """
            ),
            {
                "id": categoria_id,
                "name": (datos.get("name") or "").strip() or None,
                "description": datos.get("description"),
                "code": (datos.get("code") or "").strip().upper() or None,
                "icon": datos.get("icon"),
                "color": datos.get("color"),
                "sort_order": datos.get("sort_order"),
                "is_active": datos.get("is_active"),
                "parent_id": datos.get("parent_id"),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detalle = str(getattr(exc, "orig", exc))
        if "name" in detalle:
            raise ProductoInvalido(f"Ya existe una categoria con ese nombre.") from exc
        if "code" in detalle:
            raise ProductoInvalido(f"Ya existe una categoria con ese codigo.") from exc
        raise
    finally:
        db.close()

    for categoria in listar_categorias():
        if categoria["id"] == categoria_id:
            return categoria
    raise ProductoInvalido("La categoria no existe.")


def eliminar_categoria(categoria_id: int) -> None:
    """
    Borrado logico de una categoria.

    No se puede borrar una categoria con productos: los equipos historicos
    siguen apuntandole, y dejarlos sin categoria los saca de cualquier
    agrupacion. Hay que descontinuarla.
    """
    db = get_db_session()
    try:
        productos = db.execute(
            text(
                "SELECT COUNT(*) FROM models WHERE category_id = :id AND deleted_at IS NULL"
            ),
            {"id": categoria_id},
        ).scalar_one()

        if productos:
            raise ProductoInvalido(
                f"La categoria tiene {productos} productos. No se puede eliminar: "
                "descontinuela (is_active = FALSE) para no perder el historial."
            )

        hijas = db.execute(
            text("SELECT COUNT(*) FROM categories WHERE parent_id = :id AND deleted_at IS NULL"),
            {"id": categoria_id},
        ).scalar_one()

        if hijas:
            raise ProductoInvalido(
                f"La categoria tiene {hijas} subcategorias. No se puede eliminar."
            )

        db.execute(
            text("UPDATE categories SET deleted_at = NOW() WHERE id = :id"),
            {"id": categoria_id},
        )
        db.commit()
    finally:
        db.close()


# --- Atributos personalizados por categoria ---------------------------------


class AtributoInvalido(ValueError):
    """Regla de negocio de los atributos (clave repetida, tipo desconocido)."""


# Los tipos de dato que un atributo puede tener. El CHECK de Postgres es la
# ultima linea de defensa; esta lista es la que valida la API y la que usa el
# formulario para elegir el control.
TIPOS_ATRIBUTO = ("text", "number", "date", "boolean", "select")


def _normalizar_clave_atributo(clave: str) -> str:
    """
    La clave de un atributo, en la forma en que viaja.

    Es la clave del JSONB de valores, asi que tiene que ser estable: sin
    espacios, en minuscula y con guiones bajos. "Fecha de Caducidad" y
    "fecha_de_caducidad" tienen que ser EL MISMO atributo, no dos.
    """
    return (clave or "").strip().lower().replace(" ", "_")


def listar_atributos(categoria_id: int) -> list[dict[str, Any]]:
    """Los atributos definidos para una categoria."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT id, category_id, key, label, data_type, options, unit,
                       is_required, applies_to, sort_order, is_active
                FROM category_attributes
                WHERE category_id = :categoria_id AND is_active = TRUE
                ORDER BY sort_order, label
                """
            ),
            {"categoria_id": categoria_id},
        ).mappings().all()
        return [dict(f) for f in filas]
    finally:
        db.close()


def crear_atributo(categoria_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """Define un atributo para una categoria."""
    clave = _normalizar_clave_atributo(datos.get("key") or "")
    etiqueta = (datos.get("label") or "").strip()
    tipo = (datos.get("data_type") or "text").lower()

    if not clave:
        raise AtributoInvalido("La clave del atributo es obligatoria.")
    if not etiqueta:
        raise AtributoInvalido("La etiqueta del atributo es obligatoria.")
    if tipo not in TIPOS_ATRIBUTO:
        raise AtributoInvalido(
            f"Tipo de atributo desconocido: {tipo!r}. Validos: {', '.join(TIPOS_ATRIBUTO)}."
        )
    if tipo == "select" and not datos.get("options"):
        raise AtributoInvalido("Un atributo de tipo 'select' necesita su lista de opciones.")

    db = get_db_session()
    try:
        try:
            atributo_id = db.execute(
                text(
                    """
                    INSERT INTO category_attributes (
                        category_id, key, label, data_type, options, unit,
                        is_required, applies_to, sort_order
                    )
                    VALUES (
                        :categoria_id, :key, :label, :data_type, :options, :unit,
                        :is_required, :applies_to, :sort_order
                    )
                    RETURNING id
                    """
                ),
                {
                    "categoria_id": categoria_id,
                    "key": clave,
                    "label": etiqueta,
                    "data_type": tipo,
                    "options": json.dumps(datos.get("options")) if datos.get("options") else None,
                    "unit": datos.get("unit"),
                    "is_required": datos.get("is_required", False),
                    "applies_to": datos.get("applies_to") or "product",
                    "sort_order": datos.get("sort_order") or 0,
                },
            ).scalar_one()
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AtributoInvalido(
                f"La categoria ya tiene un atributo con la clave '{clave}'."
            ) from exc
    finally:
        db.close()

    for atributo in listar_atributos(categoria_id):
        if atributo["id"] == atributo_id:
            return atributo
    raise AtributoInvalido("El atributo se creo pero no se pudo leer.")


def actualizar_atributo(atributo_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """Edita la definicion de un atributo."""
    db = get_db_session()
    try:
        fila = db.execute(
            text("SELECT category_id FROM category_attributes WHERE id = :id"),
            {"id": atributo_id},
        ).mappings().first()
        if not fila:
            raise AtributoInvalido("El atributo no existe.")

        tipo = (datos.get("data_type") or "").lower()
        if tipo and tipo not in TIPOS_ATRIBUTO:
            raise AtributoInvalido(f"Tipo de atributo desconocido: {tipo!r}.")

        db.execute(
            text(
                """
                UPDATE category_attributes SET
                    label = COALESCE(:label, label),
                    data_type = COALESCE(:data_type, data_type),
                    options = COALESCE(:options, options),
                    unit = COALESCE(:unit, unit),
                    is_required = COALESCE(:is_required, is_required),
                    applies_to = COALESCE(:applies_to, applies_to),
                    sort_order = COALESCE(:sort_order, sort_order),
                    is_active = COALESCE(:is_active, is_active)
                WHERE id = :id
                """
            ),
            {
                "id": atributo_id,
                "label": (datos.get("label") or "").strip() or None,
                "data_type": tipo or None,
                "options": json.dumps(datos.get("options")) if datos.get("options") else None,
                "unit": datos.get("unit"),
                "is_required": datos.get("is_required"),
                "applies_to": datos.get("applies_to"),
                "sort_order": datos.get("sort_order"),
                "is_active": datos.get("is_active"),
            },
        )
        db.commit()
    finally:
        db.close()

    for atributo in listar_atributos(fila["category_id"]):
        if atributo["id"] == atributo_id:
            return atributo
    raise AtributoInvalido("El atributo no existe.")


def eliminar_atributo(atributo_id: int) -> None:
    """
    Borra la definicion de un atributo.

    Los VALORES que se cargaron en los productos NO se borran: quedan en el
    JSONB. Un valor huerfano es informacion que el operador escribio, y
    borrarla sin que nadie lo pida es perder datos.
    """
    db = get_db_session()
    try:
        db.execute(
            text("UPDATE category_attributes SET is_active = FALSE WHERE id = :id"),
            {"id": atributo_id},
        )
        db.commit()
    finally:
        db.close()


def validar_atributos(categoria_id: int, valores: dict[str, Any]) -> dict[str, Any]:
    """
    Valida los valores de los atributos contra la definicion de la categoria.

    Devuelve el diccionario limpio, listo para guardar en
    `models.specifications`.

    QUE RECHAZA
    -----------
    - Una clave que la categoria no define: seria un dato sin columna ni
      definicion, invisible para todos los informes.
    - Un valor que no se puede convertir al tipo declarado ("12" en un
      `number` es un numero, "doce" no).
    - Un atributo obligatorio sin valor.
    """
    definiciones = {a["key"]: a for a in listar_atributos(categoria_id)}
    limpios: dict[str, Any] = {}

    for clave, valor in (valores or {}).items():
        clave = str(clave).strip()
        if clave not in definiciones:
            raise AtributoInvalido(
                f"La categoria no define el atributo '{clave}'. "
                f"Definidos: {', '.join(definiciones) or 'ninguno'}."
            )
        if valor is None or valor == "":
            continue

        definicion = definiciones[clave]
        tipo = definicion["data_type"]

        if tipo == "number":
            try:
                limpios[clave] = float(valor)
            except (TypeError, ValueError) as exc:
                raise AtributoInvalido(
                    f"El valor de '{definicion['label']}' tiene que ser un numero."
                ) from exc
        elif tipo == "boolean":
            limpios[clave] = str(valor).lower() in ("1", "true", "si", "sí", "yes")
        elif tipo == "date":
            limpios[clave] = str(valor)[:10]
        else:
            if tipo == "select":
                opciones = definicion.get("options") or []
                if isinstance(opciones, str):
                    opciones = json.loads(opciones)
                if opciones and str(valor) not in [str(o) for o in opciones]:
                    raise AtributoInvalido(
                        f"'{valor}' no es una opcion valida para '{definicion['label']}'. "
                        f"Opciones: {', '.join(str(o) for o in opciones)}."
                    )
            limpios[clave] = str(valor)

    faltantes = [
        definiciones[clave]["label"]
        for clave, definicion in definiciones.items()
        if definicion["is_required"] and clave not in limpios
    ]
    if faltantes:
        raise AtributoInvalido(
            f"Faltan los atributos obligatorios: {', '.join(faltantes)}."
        )

    return limpios


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
        # La busqueda es de TEXTO COMPLETO: "portatil dell" tiene que encontrar
        # "Dell Latitude 5520", no solo las filas que tienen la palabra exacta
        # en el SKU. El ILIKE queda como red de seguridad para los codigos con
        # simbolos (un `INV-a1b2/3` no es una palabra del diccionario).
        condiciones.append(
            "(m.brand ILIKE :busqueda OR m.model ILIKE :busqueda OR m.sku ILIKE :busqueda "
            "OR m.qr_code ILIKE :busqueda OR m.barcode ILIKE :busqueda "
            "OR m.search_vector @@ plainto_tsquery('spanish', :busqueda))"
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
                    m.specifications,
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
        datos = [dict(f) for f in filas]
    finally:
        db.close()

    # La ruta de la categoria ("Portatiles > Notebooks") y los atributos del
    # producto, en una sola pasada por la lista de categorias.
    rutas = {c["id"]: c["ruta"] for c in listar_categorias()}
    for d in datos:
        d["categoria_ruta"] = rutas.get(d["category_id"], d["categoria"])
        d["atributos"] = d.get("specifications") or {}
    return datos


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

    # Los atributos de la categoria (caducidad, lote, dimensiones...) se validan
    # CONTRA la definicion antes de guardarse: un valor sin definir es un dato
    # invisible para todos los informes.
    atributos = validar_atributos(categoria_id, datos.get("atributos") or {})
    especificaciones = json.dumps(atributos) if atributos else None

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
                        qr_code, barcode, specifications,
                        is_active
                    )
                    VALUES (
                        :category_id, :brand, :model, :sku, :variant,
                        :unit_cost, :list_price, :currency,
                        :min_stock, :max_stock, :reorder_multiple, :lead_time_days,
                        :qr_code, :barcode, :specifications,
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
                    "specifications": especificaciones,
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
                       m.qr_code, m.barcode, m.specifications
                FROM models m
                JOIN categories c ON c.id = m.category_id
                WHERE m.id = :id
                """
            ),
            {"id": producto_id},
        ).mappings().one()
        datos = dict(fila)
        datos["atributos"] = datos.get("specifications") or {}
        return datos
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

    # Los atributos se FUSIONAN con los que ya tenia el producto, no se
    # reemplazan: `specifications` tambien puede traer cosas que no son
    # atributos de la categoria (es un JSONB libre), y pisarlo seria perder
    # datos. Un atributo enviado VACIO borra su valor: es la unica forma de
    # que el operador pueda limpiar un campo.
    especificaciones = None
    if "atributos" in datos:
        crudos = datos.get("atributos") or {}
        atributos = validar_atributos(categoria_id, crudos)

        db_lectura = get_db_session()
        try:
            fila = db_lectura.execute(
                text("SELECT specifications FROM models WHERE id = :id"),
                {"id": producto_id},
            ).mappings().first()
        finally:
            db_lectura.close()

        specs = dict(fila["specifications"] or {}) if fila else {}
        specs.update(atributos)
        for clave, valor in crudos.items():
            if valor is None or valor == "":
                specs.pop(clave, None)
        especificaciones = json.dumps(specs) if specs else None

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
                        specifications = COALESCE(:specifications, specifications),
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
                    "specifications": especificaciones,
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
                       m.qr_code, m.barcode, m.specifications
                FROM models m
                JOIN categories c ON c.id = m.category_id
                WHERE m.id = :id
                """
            ),
            {"id": producto_id},
        ).mappings().one()
        datos = dict(fila)
        datos["atributos"] = datos.get("specifications") or {}
        return datos
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
