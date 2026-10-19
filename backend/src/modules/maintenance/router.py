"""
Router de Mantenimiento - Planes, checklists, mantenimientos pendientes.
Montado en /api/maintenance.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Path, Body

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.maintenance import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get(
    "/pending",
    summary="Mantenimientos pendientes (PENDING, DUE_SOON, OVERDUE)",
)
def listar_pendientes(
    estado: str = Query("ALL", pattern="^(ALL|PENDING|DUE_SOON|OVERDUE)$"),
    tecnico_id: str | None = Query(None, description="Filtrar por técnico asignado"),
    limite: int = Query(50, ge=1, le=200),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    """Vista v_devices_maintenance_due con filtros."""
    return service.listar_pendientes(estado=estado, tecnico_id=tecnico_id, limite=limite)


@router.get(
    "/plans",
    summary="Planes de mantenimiento por modelo",
)
def listar_planes(
    model_id: int | None = Query(None, gt=0, description="Filtrar por modelo"),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    return service.listar_planes(model_id=model_id)


@router.post(
    "/{maintenance_id}/complete",
    summary="Completar mantenimiento con resultados",
)
def completar_mantenimiento(
    maintenance_id: str = Path(..., description="UUID del mantenimiento"),
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> dict:
    """
    Body:
    {
      "result": "OK|NEEDS_REPAIR|NEEDS_REPLACEMENT|OBSERVATION",
      "observations": "string",
      "checklist_results": [{"step": "...", "result": "OK", "notes": "..."}],
      "labor_cost": 0,
      "parts_cost": 0,
      "next_scheduled_for": "2026-12-01"
    }
    """
    return service.completar_mantenimiento(
        maintenance_id=maintenance_id,
        result=body.get("result"),
        observations=body.get("observations"),
        checklist_results=body.get("checklist_results"),
        labor_cost=body.get("labor_cost"),
        parts_cost=body.get("parts_cost"),
        next_scheduled_for=body.get("next_scheduled_for"),
        user_id=str(_usuario.id),
    )


@router.post(
    "/generate",
    summary="Generar mantenimientos pendientes (batch nocturno)",
)
def generar_mantenimientos(
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> list[dict]:
    """Ejecuta generate_pending_maintenances(). Devuelve creados."""
    return service.generar_mantenimientos()