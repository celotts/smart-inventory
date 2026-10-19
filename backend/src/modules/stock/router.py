"""
Router de Stock Agregado - Vistas rápidas para dashboards.
Montado en /api/stock.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.stock import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get(
    "/summary",
    summary="Stock agregado por modelo/ubicación",
)
def resumen_stock(
    solo_bajo: bool = Query(False, description="Solo modelos con available <= min_stock"),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    """
    Devuelve stock_levels con info de modelo + ubicación.
    Para dashboards: tabla con available, in_transit, installed, in_repair, status.
    """
    return service.resumen_stock(solo_bajo=solo_bajo)


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
    Para generar órdenes de compra automáticas.
    """
    return service.stock_bajo()


@router.post(
    "/recalculate",
    summary="Recalcula stock_levels (admin)",
)
def recalcular_stock(
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> dict:
    """Fuerza recálculo completo de stock_levels desde devices."""
    service.recalcular_todo()
    return {"status": "ok", "message": "Stock levels recalculados"}