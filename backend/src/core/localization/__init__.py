"""
Registry de providers de localización.
Auto-descubre providers en subpaquetes y expone factory `get_provider(country_code)`.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from .base import CountryLocalizationProvider

# Cache de providers instanciados
_PROVIDERS: dict[str, CountryLocalizationProvider] = {}
_DISCOVERED = False


def _discover_providers() -> None:
    """Importa dinámicamente todos los módulos en chile/, mexico/, etc."""
    global _DISCOVERED
    if _DISCOVERED:
        return

    package = __name__  # "src.core.localization"
    for _, module_name, _ in pkgutil.iter_modules(__path__, prefix=f"{package}."):
        try:
            importlib.import_module(module_name)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "No se pudo cargar provider de localización %s: %s", module_name, e
            )
    _DISCOVERED = True


def register_provider(provider: CountryLocalizationProvider) -> None:
    """Los providers se auto-registran al importarse (ver __init__.py de cada país)."""
    code = provider.country_code.upper()
    if code in _PROVIDERS:
        raise ValueError(f"Provider duplicado para country_code={code}")
    _PROVIDERS[code] = provider


def get_provider(country_code: str) -> CountryLocalizationProvider:
    """
    Obtiene el provider para un país.
    Lanza KeyError si no existe (fallar rápido en config).
    """
    _discover_providers()
    code = country_code.upper()
    if code not in _PROVIDERS:
        available = ", ".join(sorted(_PROVIDERS.keys()))
        raise KeyError(
            f"Localización no disponible para '{code}'. "
            f"Países soportados: {available}"
        )
    return _PROVIDERS[code]


def list_available_countries() -> list[dict[str, str]]:
    """Lista de países disponibles para UI de selección."""
    _discover_providers()
    return [
        {
            "code": p.country_code,
            "name": p.country_name,
            "currency": p.currency_code,
            "locale": p.locale_default,
        }
        for p in _PROVIDERS.values()
    ]


def get_active_provider() -> CountryLocalizationProvider:
    """
    Provider activo según configuración de la app.
    Lee de `settings.localization_country` (default: "CL").
    """
    from src.core.config import get_settings
    return get_provider(get_settings().localization_country)


# --- Helpers de conveniencia (delegados al provider activo) ---

def validate_national_id(value: str):
    from .base import ValidationResult
    return get_active_provider().validate_national_id(value)


def get_regions():
    return get_active_provider().get_regions()


def get_communes(region_code: str | None = None):
    return get_active_provider().get_communes(region_code)


def get_holidays(year: int, region_code: str | None = None):
    return get_active_provider().get_holidays(year, region_code)


def is_business_day(dt, region_code: str | None = None) -> bool:
    return get_active_provider().is_business_day(dt, region_code)


def add_business_days(dt, days: int, region_code: str | None = None):
    return get_active_provider().add_business_days(dt, days, region_code)


def format_currency(amount: int | float, *, symbol: bool = True) -> str:
    return get_active_provider().format_currency(amount, symbol=symbol)


def get_ui_config() -> dict[str, Any]:
    return get_active_provider().get_ui_config()