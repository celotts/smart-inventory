"""
Router de Stock Agregado - Vistas rápidas para dashboards.
Montado en /api/stock.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.stock import alertas, service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get(
    "/summary",
    summary="Stock agregado por modelo/ubicacion",
)
def resumen_stock(
    solo_bajo: bool = Query(False, description="Solo modelos con available <= min_stock"),
    limite: int | None = Query(
        None,
        ge=1,
        le=5000,
        description="Cuantas filas se devuelven. Sin limite, todas (para los resumenes).",
    ),
    offset: int = Query(0, ge=0),
    busqueda: str | None = Query(
        None, max_length=100, description="SKU, marca, modelo o ubicacion."
    ),
    location_id: int | None = Query(None, gt=0, description="Filtra por ubicacion."),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    La existencia por producto y por ubicacion, paginada.

    Con 800 productos en 6 ubicaciones son 4800 filas: sin límite, la API las
    devuelve todas y el navegador las pinta todas, y la app deja de
    responder. Por eso el `límite`, el `offset` y el `total`.

    El filtro va en el SERVIDOR: con la lista paginada, filtrar en el
    navegador solo mira las filas de la página, y el operador ve "no hay"
    con 4800 existencias.
    """
    items, total = service.resumen_stock(
        solo_bajo=solo_bajo,
        busqueda=busqueda,
        location_id=location_id,
        limite=limite,
        offset=offset,
    )
    return {
        "items": items,
        "total": total,
        "limite": limite,
        "offset": offset,
    }


@router.get(
    "/low",
    summary="Modelos con stock por debajo del mínimo",
)
def stock_bajo(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    """
    Modelos donde available_count <= min_stock.
    Incluye suggested_reorder_qty (redondeado a reorder_multiple).
    Para generar órdenes de compra automaticas.
    """
    return service.stock_bajo()


@router.get(
    "/por-reponer",
    summary="Productos por reponer (mismo criterio que el tablero)",
)
def productos_por_reponer(
    limite: int = Query(100, ge=1, le=1000),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    """
    Los productos por reponer.

    Es la MISMA consulta que usa la tarjeta "Por reponer" del tablero: el
    total disponible del producto contra su mínimo. La tarjeta y el modal
    tienen que decir lo mismo, o el operador pulsa un producto y el detalle
    le dice que no hay nada que reponer.
    """
    return service.productos_por_reponer(limite=limite)


@router.get(
    "/alertas",
    summary="Alertas de stock bajo (persistidas)",
)
def listar_alertas(
    solo_abiertas: bool = Query(True, description="Solo las que siguen sin resolverse."),
    limite: int = Query(100, ge=1, le=500),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    """
    Las alertas de stock bajo, con la fecha desde la que el producto está
    por debajo del mínimo.

    Es la diferencia entre "hay 6 productos por reponer" y "este va 9 días
    bajo el mínimo y el proveedor tarda 14: si no se pide hoy, se rompe".
    """
    return alertas.listar_alertas(solo_abiertas=solo_abiertas, limite=limite)


@router.post(
    "/alertas/barrer",
    summary="Correr el barrido de alertas (admin)",
)
def barrer_alertas(
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> dict:
    """
    Fuerza el barrido de alertas.

    El backend lo corre solo cada minuto (igual que el barrido de SLA). Este
    endpoint existe para probarlo y para forzarlo sin esperar.
    """
    return alertas.barrer_alertas()


@router.post(
    "/recalculate",
    summary="Recalcula stock_levels (admin)",
)
def recalcular_stock(
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> dict:
    """Fuerza recalculo completo de stock_levels desde devices."""
    service.recalcular_todo()
    return {"status": "ok", "message": "Stock levels recalculados"}