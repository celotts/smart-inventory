"""
Router de ubicaciones. Montado en /api/locations.

POR QUE UN ROUTER PROPIO
------------------------
`locations` era una tabla sin modulo: el unico endpoint que la tocaba era
`GET /api/devices/locations`, que devolvia un select plano de nombres. Con la
jerarquia (bodega > pasillo > estante) hacen falta mas cosas: el arbol, el
alta, y la edicion del padre.

Se mantiene `GET /api/devices/locations` por compatibilidad: el frontend viejo
lo usa, y un endpoint que desaparece se rompe en el cliente que no se actualiza
al mismo tiempo.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.locations import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/locations", tags=["ubicaciones"])


def _error(exc: service.UbicacionInvalida) -> HTTPException:
    """Una regla de negocio no es un 500: es un 409 con el motivo."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "",
    summary="Listar ubicaciones (con su ruta y su tipo)",
)
def listar(
    solo_activas: bool = True,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.listar_ubicaciones(solo_activas=solo_activas)


@router.get(
    "/arbol",
    summary="Ubicaciones en arbol (bodegas, pasillos, estantes)",
)
def arbol(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.arbol_ubicaciones()


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta una ubicacion",
)
def crear(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body:
    {
      "store_name": "Deposito Norte",
      "address": "Av. siempre viva 742",
      "location_type": "WAREHOUSE",
      "parent_id": null,
      "tolerance_radius_meters": 50
    }
    """
    try:
        return service.crear_ubicacion(body)
    except service.UbicacionInvalida as exc:
        raise _error(exc) from exc


@router.put(
    "/{ubicacion_id}",
    summary="Editar una ubicacion",
)
def actualizar(
    ubicacion_id: int,
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Body (todo opcional):
    {
      "store_name": "Deposito Norte - A",
      "parent_id": 3,
      "location_type": "AISLE",
      "is_active": true
    }
    """
    try:
        return service.actualizar_ubicacion(ubicacion_id, body)
    except service.UbicacionInvalida as exc:
        raise _error(exc) from exc
