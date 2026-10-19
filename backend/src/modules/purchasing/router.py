"""
Router de Compras - Órdenes de compra, recepción, sugerencias.
Montado en /api/purchase-orders.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Path, Body

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.purchasing import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/purchase-orders", tags=["purchasing"])


@router.post(
    "",
    summary="Crear orden de compra (estado BORRADOR)",
)
def crear_orden(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> dict:
    """
    Body:
    {
      "vendor_id": 1,
      "lineas": [
        {"model_id": 5, "cantidad": 10, "unit_cost": 250.00}
      ],
      "expected_date": "2026-12-01",
      "observations": "Urgente para proyecto X"
    }
    """
    return service.crear_orden(
        vendor_id=body["vendor_id"],
        lineas=body["lineas"],
        expected_date=body.get("expected_date"),
        observations=body.get("observations"),
        user_id=str(_usuario.id),
    )


@router.get(
    "",
    summary="Listar órdenes de compra con filtros",
)
def listar_ordenes(
    estado: str | None = Query(None, pattern="^(BORRADOR|ENVIADA|CONFIRMADA|RECIBIDA|CANCELADA)$"),
    vendor_id: int | None = Query(None, gt=0),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    return service.listar_ordenes(estado=estado, vendor_id=vendor_id, desde=desde, hasta=hasta)


@router.post(
    "/{po_id}/receive",
    summary="Recibir orden de compra (crea devices + WAREHOUSE_ENTRY)",
)
def recibir_orden(
    po_id: int = Path(..., gt=0),
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> dict:
    """
    Body:
    {
      "lineas": [
        {"po_line_id": 1, "cantidad_recibida": 5, "serial_numbers": ["SN-001", "SN-002"]}
      ],
      "observations": "Recibido en buen estado"
    }
    """
    return service.recibir_orden(
        po_id=po_id,
        lineas=body["lineas"],
        observations=body.get("observations"),
        user_id=str(_usuario.id),
    )


@router.post(
    "/suggest",
    summary="Sugerir reorden basada en stock_levels",
)
def sugerir_reorden(
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    """
    Calcula: (max_stock - available_count) redondeado UP a reorder_multiple.
    Solo modelos is_active con available <= min_stock.
    """
    return service.sugerir_reorden()