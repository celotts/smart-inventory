"""
Router de exportacion. Montado en /api/export.

QUE ES
------
Cuatro CSV: el inventario, el maestro de productos, el historial de
movimientos y la auditoria. Son los cuatro "¿pasame esto en Excel?" que un
supervisor hace en un deposito.

POR QUE EL ARCHIVO SE DEVUELVE COMO TEXTO Y NO COMO JSON
-------------------------------------------------------
Un CSV no es un JSON: el cliente lo baja como archivo. El `Content-Type` es
`text/csv` y el `Content-Disposition` le dice al navegador el nombre. El
frontend lo pide con `comoTexto` y arma el `Blob` en el navegador, porque un
`<a download>` no puede mandar el token de autorizacion.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.export import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/export", tags=["exportacion"])


def _csv(nombre: str, contenido: str) -> Response:
    return Response(
        content=contenido.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            # El export es un archivo que se regenera cada vez: cachearlo
            # serviria un inventario viejo, que es peor que no servirlo.
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/inventario.csv",
    summary="Exportar el inventario a CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Inventario en CSV."}},
)
def exportar_inventario(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> Response:
    return _csv("inventario.csv", service.inventario())


@router.get(
    "/productos.csv",
    summary="Exportar el maestro de productos a CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Productos en CSV."}},
)
def exportar_productos(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> Response:
    return _csv("productos.csv", service.productos())


@router.get(
    "/movimientos.csv",
    summary="Exportar el historial de movimientos a CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Movimientos en CSV."}},
)
def exportar_movimientos(
    limite: int = Query(5000, ge=1, le=50000),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> Response:
    return _csv("movimientos.csv", service.movimientos(limite=limite))


@router.get(
    "/auditoria.csv",
    summary="Exportar la auditoria a CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Auditoria en CSV."}},
)
def exportar_auditoria(
    limite: int = Query(5000, ge=1, le=50000),
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> Response:
    return _csv("auditoria.csv", service.auditoria(limite=limite))
