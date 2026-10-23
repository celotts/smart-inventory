# backend/src/core/localization/router.py
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core.localization import (
    get_active_provider,
    get_provider,
    list_available_countries,
    validate_national_id,
    get_regions,
    get_communes,
    get_holidays,
    is_business_day,
    add_business_days,
)

router = APIRouter(prefix="/api/localization", tags=["localization"])


class ValidateIdRequest(BaseModel):
    value: str
    country: str | None = None  # override opcional


class BusinessDayRequest(BaseModel):
    date: date
    region: str | None = None


class AddBusinessDaysRequest(BaseModel):
    date: date
    days: int
    region: str | None = None


@router.get("/config")
def localization_config(country: str | None = Query(None)) -> dict[str, Any]:
    """Config completa para el frontend (UI, validaciones, formatos)."""
    provider = get_provider(country) if country else get_active_provider()
    return provider.get_ui_config()


@router.get("/countries")
def available_countries() -> list[dict[str, str]]:
    """Países disponibles para selector en UI."""
    return list_available_countries()


@router.post("/validate-id")
def validate_id(request: ValidateIdRequest):
    """Valida ID nacional (RUT, RFC, etc.) según país activo o override."""
    provider = get_provider(request.country) if request.country else get_active_provider()
    result = provider.validate_national_id(request.value)
    return result.model_dump()


@router.get("/regions")
def regions(country: str | None = Query(None)) -> list[dict[str, Any]]:
    provider = get_provider(country) if country else get_active_provider()
    return [r.__dict__ for r in provider.get_regions()]


@router.get("/communes")
def communes(region: str | None = Query(None), country: str | None = Query(None)):
    provider = get_provider(country) if country else get_active_provider()
    return [c.__dict__ for c in provider.get_communes(region)]


@router.get("/communes/search")
def search_communes(q: str = Query(..., min_length=2), limit: int = 10, country: str | None = Query(None)):
    provider = get_provider(country) if country else get_active_provider()
    return [c.__dict__ for c in provider.search_communes(q, limit)]


@router.get("/holidays/{year}")
def holidays(year: int, region: str | None = Query(None), country: str | None = Query(None)):
    provider = get_provider(country) if country else get_active_provider()
    return [h.__dict__ for h in provider.get_holidays(year, region)]


@router.post("/business-day")
def check_business_day(request: BusinessDayRequest, country: str | None = Query(None)):
    provider = get_provider(country) if country else get_active_provider()
    return {"is_business_day": provider.is_business_day(request.date, request.region)}


@router.post("/add-business-days")
def add_business_days_endpoint(request: AddBusinessDaysRequest, country: str | None = Query(None)):
    provider = get_provider(country) if country else get_active_provider()
    result = provider.add_business_days(request.date, request.days, request.region)
    return {"date": result.isoformat()}


@router.get("/format-currency")
def format_currency(amount: float, symbol: bool = True, country: str | None = Query(None)):
    provider = get_provider(country) if country else get_active_provider()
    return {"formatted": provider.format_currency(amount, symbol=symbol)}