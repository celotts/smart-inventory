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

from fastapi import APIRouter, Depends, Query, Body, HTTPException, status

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.catalog import service
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
