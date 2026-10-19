"""
Schemas de Compras.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PurchaseOrderLineCreate(BaseModel):
    model_id: int = Field(..., gt=0)
    cantidad: int = Field(..., gt=0)
    unit_cost: float = Field(0, ge=0)


class PurchaseOrderCreate(BaseModel):
    vendor_id: int = Field(..., gt=0)
    lineas: list[PurchaseOrderLineCreate] = Field(..., min_length=1)
    expected_date: date | None = None
    observations: str | None = Field(None, max_length=1000)


class PurchaseOrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    po_id: int
    model_id: int
    cantidad: int
    cantidad_recibida: int
    unit_cost: float
    sort_order: int


class PurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    vendor_name: str | None
    status: str
    expected_date: date | None
    observations: str | None
    total_estimado: float | None
    created_at: date
    created_by: UUID | None