"""
Router de Reportes - Reportes ejecutivos, KPIs, tendencias.
Montado en /api/reports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.reporting import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/reports", tags=["reporting"])


@router.get(
    "/operational",
    summary="Reporte operativo completo (redactado por LLM)",
)
def reporte_operativo(
    _usuario: UsuarioActual = Depends(exigir(Permiso.IA_PREGUNTAR)),
) -> dict:
    """Genera reporte en prosa: resumen, alertas, KPIs, detalle, recomendaciones."""
    return service.reporte_operativo()


@router.get(
    "/inventory-stats",
    summary="KPIs de inventario: total, por estado, por ubicación, valor, rotación",
)
def estadisticas_inventario(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    return service.estadisticas_inventario()


@router.get(
    "/rotation",
    summary="Rotación de modelos: fast/slow movers en N días",
)
def rotacion_modelos(
    dias: int = Query(30, ge=1, le=365),
    top: int = Query(10, ge=1, le=50),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.rotacion_modelos(dias=dias, top=top)


@router.get(
    "/install-time",
    summary="Tiempo promedio OUT_TO_CLIENT → INSTALLATION por modelo/cliente",
)
def tiempo_instalacion(
    model_id: int | None = Query(None, gt=0),
    cliente_id: int | None = Query(None, gt=0),
    dias: int = Query(90, ge=1, le=365),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    return service.tiempo_instalacion(model_id=model_id, cliente_id=cliente_id, dias=dias)