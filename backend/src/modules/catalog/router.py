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
