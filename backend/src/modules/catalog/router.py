"""
Router de Catalogo - Maestro de productos (models), categorias y proveedores.
Montado en /api/catalog.

POR QUE EL MAESTRO DE PRODUCTOS ES `models`
-------------------------------------------
El sistema no tiene una tabla `products`: tiene `models`, que es donde ya viven
el SKU, el stock minimo/maximo y el costo, y que es la unica que el resto del
backend consulta (stock, purchasing, reporting). Este router expone ese maestro
al frontend, que hasta ahora no tenia forma de darlo de alta.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Body, HTTPException, Response, status

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.catalog import barcode, service
from src.modules.devices import qr as qr_dispositivo
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _error_de_negocio(exc: service.ProductoInvalido) -> HTTPException:
    """Una regla de negocio del catalogo no es un 500: es un 409 con el motivo."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# --- Categorias -------------------------------------------------------------


@router.get(
    "/categories",
    summary="Listar categorias de producto",
)
def listar_categorias(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.listar_categorias()


@router.get(
    "/categories/tree",
    summary="Categorias en arbol (con subcategorias)",
)
def arbol_categorias(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.arbol_categorias()


@router.post(
    "/categories",
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta una categoria (con categoria padre opcional)",
)
def crear_categoria(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body:
    {
      "name": "Notebooks",
      "code": "NB",
      "parent_id": 12,
      "icon": "laptop",
      "color": "#0277bd"
    }
    """
    try:
        return service.crear_categoria(body)
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


@router.put(
    "/categories/{categoria_id}",
    summary="Editar una categoria",
)
def actualizar_categoria(
    categoria_id: int,
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body (todo opcional):
    { "name": "Notebooks", "parent_id": 12, "is_active": true }
    """
    try:
        return service.actualizar_categoria(categoria_id, body)
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


@router.delete(
    "/categories/{categoria_id}",
    summary="Eliminar una categoria (borrado logico)",
)
def eliminar_categoria(
    categoria_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    No se puede eliminar una categoria con productos o con subcategorias: se
    descontinua (is_active = FALSE) para no perder el historial.
    """
    try:
        service.eliminar_categoria(categoria_id)
        return {"ok": True}
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


# --- Atributos de categoria -------------------------------------------------


@router.get(
    "/categories/{categoria_id}/attributes",
    summary="Atributos definidos para una categoria",
)
def listar_atributos(
    categoria_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.listar_atributos(categoria_id)


@router.post(
    "/categories/{categoria_id}/attributes",
    status_code=status.HTTP_201_CREATED,
    summary="Definir un atributo para una categoria",
)
def crear_atributo(
    categoria_id: int,
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body:
    {
      "key": "caducidad",
      "label": "Fecha de caducidad",
      "data_type": "date",
      "unit": null,
      "is_required": false,
      "applies_to": "product"
    }

    Para `data_type: "select"`, `options` es la lista de valores:
    { "key": "estado", "label": "Estado", "data_type": "select",
      "options": ["Nuevo", "Reacondicionado"] }
    """
    try:
        return service.crear_atributo(categoria_id, body)
    except service.AtributoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put(
    "/attributes/{atributo_id}",
    summary="Editar la definicion de un atributo",
)
def actualizar_atributo(
    atributo_id: int,
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    try:
        return service.actualizar_atributo(atributo_id, body)
    except service.AtributoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete(
    "/attributes/{atributo_id}",
    summary="Desactivar un atributo de categoria",
)
def eliminar_atributo(
    atributo_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Los valores ya cargados en los productos NO se borran: quedan en el JSONB.
    Un valor huerfano es informacion que el operador escribio.
    """
    try:
        service.eliminar_atributo(atributo_id)
        return {"ok": True}
    except service.AtributoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# --- Productos (modelos) ----------------------------------------------------


@router.get(
    "/products",
    summary="Listar el maestro de productos",
)
def listar_productos(
    busqueda: str | None = Query(None, max_length=100, description="Marca, modelo o SKU."),
    categoria_id: int | None = Query(None, gt=0),
    solo_activos: bool = Query(True),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.listar_productos(
        busqueda=busqueda,
        categoria_id=categoria_id,
        solo_activos=solo_activos,
    )


@router.post(
    "/products",
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un producto del maestro",
)
def crear_producto(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body:
    {
      "brand": "Dell",
      "model": "Latitude 5520",
      "sku": "NB-DELL-5520",
      "variant": "i5/16GB/512GB",
      "category_id": 1,
      "unit_cost": 850.00,
      "min_stock": 3,
      "max_stock": 20,
      "reorder_multiple": 5,
      "lead_time_days": 14
    }
    """
    for obligatorio in ("brand", "model", "sku"):
        if not (body.get(obligatorio) or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"El campo '{obligatorio}' es obligatorio.",
            )
    try:
        return service.crear_producto(body)
    except service.AtributoInvalido as exc:
        # Un atributo con un valor que no corresponde a su tipo, o un atributo
        # que la categoria no define: es un 409 con el motivo, no un 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


@router.put(
    "/products/{producto_id}",
    summary="Actualizar un producto del maestro",
)
def actualizar_producto(
    producto_id: int,
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body:
    {
      "brand": "Dell",
      "model": "Latitude 5520",
      "category_id": 1,
      "variant": "i5/16GB/512GB",
      "unit_cost": 850.00,
      "min_stock": 3,
      "max_stock": 20,
      "is_active": true
    }
    """
    for obligatorio in ("brand", "model"):
        if not (body.get(obligatorio) or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"El campo '{obligatorio}' es obligatorio.",
            )
    try:
        return service.actualizar_producto(producto_id, body)
    except service.AtributoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


@router.delete(
    "/products/{producto_id}",
    summary="Eliminar un producto (borrado logico)",
)
def eliminar_producto(
    producto_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Borrado logico de un producto. Solo se permite si NO tiene movimientos
    asociados. Si tiene movimientos, se debe descontinuar (is_active = FALSE).
    """
    try:
        service.eliminar_producto(producto_id)
        return {"ok": True}
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


# --- Etiqueta del producto (QR + codigo de barras) ---------------------------


@router.get(
    "/products/{producto_id}/qr",
    summary="QR de la etiqueta del producto (SVG)",
    response_class=Response,
    responses={
        200: {"content": {"image/svg+xml": {}}, "description": "QR en SVG."},
    },
)
def qr_de_producto(
    producto_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> Response:
    """
    El QR de la etiqueta del producto, listo para escanear o imprimir.

    QUE CODIFICA
    ------------
    El `qr_code` del producto (`PRD-<sku>` por defecto), que es el codigo de la
    ETIQUETA DEL PRODUCTO —la caja, el rack—, no la del equipo. La del equipo
    es la que ya existe, en `/api/devices/{id}/qr`.

    Se reutiliza el generador de QR de los dispositivos en vez de tener dos
    copias del mismo codigo: el SVG es el mismo, y un solo lugar donde tocarlo.
    """
    try:
        datos = service.obtener_codigos_producto(producto_id)
    except service.ProductoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(
        content=qr_dispositivo.qr_como_svg(datos["qr_code"] or datos["sku"]),
        media_type="image/svg+xml",
        headers={
            # La etiqueta de un producto no cambia: el QR se puede cachear.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get(
    "/products/{producto_id}/barcode",
    summary="Codigo de barras de la etiqueta del producto (SVG)",
    response_class=Response,
    responses={
        200: {"content": {"image/svg+xml": {}}, "description": "Codigo de barras en SVG."},
    },
)
def barcode_de_producto(
    producto_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> Response:
    """
    El codigo de barras de la etiqueta del producto, en Code 128.

    Es un SVG: pesa unos pocos KB, se ve nitido a cualquier tamaño y se
    imprime sin perdida. El fondo es blanco explicito, porque un lector mide
    el contraste entre las barras y el fondo.
    """
    try:
        datos = service.obtener_codigos_producto(producto_id)
    except service.ProductoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(
        content=barcode.codigo128_como_svg(datos["barcode"] or datos["sku"]),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "private, max-age=3600",
        },
    )


# --- Clientes ---------------------------------------------------------------

@router.get(
    "/clients",
    summary="Listar clientes",
)
def listar_clientes(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.listar_clientes()


@router.post(
    "/clients",
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un cliente",
)
def crear_cliente(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    if not (body.get("commercial_name") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El nombre comercial es obligatorio.",
        )
    if not (body.get("tax_id") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El tax_id es obligatorio.",
        )
    try:
        return service.crear_cliente(body)
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


@router.put(
    "/clients/{cliente_id}",
    summary="Actualizar un cliente",
)
def actualizar_cliente(
    cliente_id: int,
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    if not (body.get("commercial_name") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El nombre comercial es obligatorio.",
        )
    if not (body.get("tax_id") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El tax_id es obligatorio.",
        )
    try:
        return service.actualizar_cliente(cliente_id, body)
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


@router.delete(
    "/clients/{cliente_id}",
    summary="Eliminar un cliente (borrado logico)",
)
def eliminar_cliente(
    cliente_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    try:
        service.eliminar_cliente(cliente_id)
        return {"ok": True}
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc


# --- Proveedores ------------------------------------------------------------


@router.get(
    "/vendors",
    summary="Listar proveedores",
)
def listar_proveedores(
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    return service.listar_proveedores()


@router.post(
    "/vendors",
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un proveedor",
)
def crear_proveedor(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> dict:
    """
    Body:
    {
      "name": "Lenovo Argentina",
      "code": "LEN",
      "email": "ventas@lenovo.com",
      "phone": "+54 11 4000-0000",
      "contact_person": "Juan Perez"
    }
    """
    if not (body.get("name") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El nombre del proveedor es obligatorio.",
        )
    try:
        return service.crear_proveedor(body)
    except service.ProductoInvalido as exc:
        raise _error_de_negocio(exc) from exc
