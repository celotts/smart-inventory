"""Provider México - Implementación concreta."""
from __future__ import annotations

from datetime import date
from typing import Any

from src.core.localization.base import (
    AdminDivision,
    CountryLocalizationProvider,
    Holiday,
    ValidationResult,
)
from src.core.localization.mexico import curp, holidays, rfc, states


class MexicoLocalizationProvider(CountryLocalizationProvider):
    country_code = "MX"
    country_name = "México"
    currency_code = "MXN"
    currency_symbol = "MX$"
    timezone_default = "America/Mexico_City"
    locale_default = "es-MX"

    @property
    def id_field_label(self) -> str:
        return "RFC / CURP"

    @property
    def id_field_placeholder(self) -> str:
        return "GARC800101XXX o GARC800101HDFRRL09"

    @property
    def id_pattern_regex(self) -> str:
        return r"^([A-ZÑ&]{3,4})(\d{6})([A-Z0-9]{3})$"

    def validate_national_id(self, value: str) -> ValidationResult:
        """
        Valida RFC (12-13 chars) o CURP (18 chars).
        Detecta automáticamente por longitud.
        """
        if not value or not value.strip():
            return ValidationResult(valid=False, raw=value, error="RFC/CURP es obligatorio")

        raw = value.strip().upper().replace(" ", "").replace("-", "")

        if len(raw) in (12, 13):
            return rfc.validar_rfc(value)
        elif len(raw) == 18:
            return curp.validar_curp(value)
        else:
            return ValidationResult(
                valid=False,
                raw=raw,
                error="Longitud inválida: RFC (12-13) o CURP (18 caracteres)",
            )

    def generate_national_id(self, *, entity_type: str = "person") -> str:
        # Generar RFC por defecto
        return rfc.generar_rfc_aleatorio(entity_type=entity_type)

    def get_regions(self) -> list[AdminDivision]:
        return states.get_estados()

    def get_communes(self, region_code: str | None = None) -> list[AdminDivision]:
        return states.get_municipios(region_code)

    def find_commune(self, code: str) -> AdminDivision | None:
        return states.find_municipio(code)

    def search_communes(self, query: str, limit: int = 10) -> list[AdminDivision]:
        return states.search_municipios(query, limit)

    def get_holidays(self, year: int, region_code: str | None = None) -> list[Holiday]:
        return holidays.get_holidays_mexico(year, region_code)

    def is_holiday(self, dt: date, region_code: str | None = None) -> Holiday | None:
        for h in self.get_holidays(dt.year, region_code):
            if h.date == dt:
                return h
        return None

    def get_irreducible_holidays(self, year: int) -> list[Holiday]:
        return [h for h in self.get_holidays(year) if h.is_irreducible]

    def format_currency(self, amount: int | float, *, symbol: bool = True) -> str:
        # MXN: 2 decimales, separador de miles = coma, decimal = punto, símbolo = MX$ o $
        formatted = f"{amount:,.2f}"
        return f"MX$ {formatted}" if symbol else formatted

    def parse_currency(self, text: str) -> float | None:
        try:
            clean = text.replace("MX$", "").replace("$", "").replace(",", "").strip()
            return float(clean)
        except ValueError:
            return None

    def format_date(self, dt: date, *, short: bool = False) -> str:
        return dt.strftime("%d/%m/%Y") if short else dt.strftime("%d de %B de %Y")


# Auto-registro al importar
from src.core.localization import register_provider
register_provider(MexicoLocalizationProvider())