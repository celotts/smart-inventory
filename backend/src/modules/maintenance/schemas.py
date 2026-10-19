"""
Schemas de Mantenimiento.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MaintenancePlanCreate(BaseModel):
    model_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    type: str = Field("PREVENTIVE", pattern="^(PREVENTIVE|PREDICTIVE|CALIBRATION|INSPECTION)$")
    interval_months: int = Field(..., gt=0)
    interval_basis: str = Field("INSTALL_DATE", pattern="^(INSTALL_DATE|MANUFACTURE_DATE|LAST_MAINTENANCE)$")
    lead_time_days: int = Field(30, ge=0)
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    estimated_duration_minutes: int | None = Field(None, gt=0)
    requires_specialist: bool = False


class MaintenancePlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    model_id: int
    name: str
    description: str | None
    type: str
    interval_months: int
    interval_basis: str
    lead_time_days: int
    checklist: list[dict[str, Any]]
    estimated_duration_minutes: int | None
    requires_specialist: bool
    is_active: bool


class MaintenanceComplete(BaseModel):
    """Body para completar mantenimiento."""
    result: str = Field(..., pattern="^(OK|NEEDS_REPAIR|NEEDS_REPLACEMENT|OBSERVATION)$")
    observations: str | None = Field(None, max_length=2000)
    checklist_results: list[dict[str, Any]] = Field(default_factory=list)
    labor_cost: float | None = Field(None, ge=0)
    parts_cost: float | None = Field(None, ge=0)
    next_scheduled_for: date | None = None


class MaintenanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_id: int
    plan_id: int | None
    tipo: str | None
    descripcion: str | None
    scheduled_for: date
    due_date: date
    completed_at: date | None
    status: str
    result: str | None
    observations: str | None
    checklist_results: list[dict[str, Any]]
    labor_cost: float | None
    parts_cost: float | None
    total_cost: float | None
    next_scheduled_for: date | None
    tecnico_id: UUID | None