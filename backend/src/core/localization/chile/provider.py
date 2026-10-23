"""Provider Chile - Implementación concreta."""
from __future__ import annotations

from datetime import date
from typing import Any

from src.core.localization.base import (
    AdminDivision,
    CountryLocalizationProvider,
    Holiday,
    ValidationResult,
)
from src.core.localization.chile import communes, holidays, rut


class ChileLocalizationProvider(CountryLocalizationProvider):
    country_code = "CL"
    country_name = "Chile"
    currency_code = "CLP"
    currency_symbol = "$"
    timezone_default = "America/Santiago"
    locale_default = "es-CL"

    @property
    def id_field_label(self) -> str:
        return "RUT"

    @property
    def id_field_placeholder(self) -> str:
        return "12.345.678-9"

    @property
    def id_pattern_regex(self) -> str:
        return r"^\d{1,3}(\.\d{3})*-[\dkK]$"

    def validate_national_id(self, value: str) -> ValidationResult:
        return rut.validar_rut(value)

    def generate_national_id(self, *, entity_type: str = "person") -> str:
        return rut.generar_rut_aleatorio()

    def get_regions(self) -> list[AdminDivision]:
        return communes.get_regiones()

    def get_communes(self, region_code: str | None = None) -> list[AdminDivision]:
        return communes.get_comunas(region_code)

    def find_commune(self, code: str) -> AdminDivision | None:
        return communes.find_comuna(code)

    def search_communes(self, query: str, limit: int = 10) -> list[AdminDivision]:
        return communes.search_comunas(query, limit)

    def get_holidays(self, year: int, region_code: str | None = None) -> list[Holiday]:
        return holidays.get_holidays_chile(year, region_code)

    def is_holiday(self, dt: date, region_code: str | None = None) -> Holiday | None:
        for h in self.get_holidays(dt.year, region_code):
            if h.date == dt:
                return h
        return None

    def get_irreducible_holidays(self, year: int) -> list[Holiday]:
        return [h for h in self.get_holidays(year) if h.is_irreducible]

    def format_currency(self, amount: int | float, *, symbol: bool = True) -> str:
        # CLP: sin decimales, separador de miles = punto, símbolo = $
        formatted = f"{int(amount):,}".replace(",", ".")
        return f"$ {formatted}" if symbol else formatted

    def parse_currency(self, text: str) -> float | None:
        try:
            clean = text.replace("$", "").replace(".", "").replace(",", "").strip()
            return float(clean)
        except ValueError:
            return None

    def format_date(self, dt: date, *, short: bool = False) -> str:
        return dt.strftime("%d/%m/%Y") if short else dt.strftime("%d de %B de %Y")


# Auto-registro al importar
from src.core.localization import register_provider
register_provider(ChileLocalizationProvider())