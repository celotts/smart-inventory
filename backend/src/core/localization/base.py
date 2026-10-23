"""
Interfaz base para localización por país.
Cada país implementa SU validador de ID, SU división administrativa, SUS feriados.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, field_validator


@dataclass(frozen=True)
class AdminDivision:
    """División administrativa genérica: Región/Estado → Comuna/Municipio."""
    code: str           # Código oficial (ej. "13" Región Metropolitana, "13101" Santiago)
    name: str           # Nombre oficial
    parent_code: str | None = None  # Código del padre (región para comuna)
    type: str = "commune"           # "region" | "state" | "commune" | "municipality"
    metadata: dict[str, Any] | None = None  # Extensible: lat/lon, timezone, etc.


@dataclass(frozen=True)
class Holiday:
    """Feriado con metadata para lógica de negocio."""
    date: date
    name: str
    type: str                    # "national" | "regional" | "religious" | "irreducible" | "election"
    description: str | None = None
    regions: list[str] | None = None  # Códigos de región donde aplica (None = nacional)
    is_irreducible: bool = False    # Ley 19.973: comercio cerrado obligatorio


class ValidationResult(BaseModel):
    """Resultado estandarizado de validación de ID nacional."""
    valid: bool
    formatted: str | None = None   # Formato canónico (ej. "12.345.678-9")
    raw: str | None = None         # Input original
    error: str | None = None       # Mensaje legible si inválido
    metadata: dict[str, Any] = {}  # Ej: {"dv": "9", "type": "persona_natural"}

    @field_validator("formatted", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return v or None


class CountryLocalizationProvider(ABC):
    """
    Contrato que DEBE implementar cada país.
    El core NUNCA importa proveedores concretos; usa el registry.
    """

    # --- Identidad del país (constantes de clase) ---
    country_code: str              # ISO 3166-1 alpha-2: "CL", "MX", "US", "CO", ...
    country_name: str              # Nombre oficial en español
    currency_code: str             # ISO 4217: "CLP", "MXN", "USD", "COP", ...
    currency_symbol: str           # Símbolo local: "$", "MX$", "US$", "$"
    timezone_default: str          # IANA: "America/Santiago", "America/Mexico_City", ...
    locale_default: str            # BCP 47: "es-CL", "es-MX", "en-US", "es-CO", ...

    # --- Documento de identidad nacional ---
    @property
    @abstractmethod
    def id_field_label(self) -> str:
        """Label para UI: 'RUT', 'RFC', 'Cédula', 'DNI', 'NIT', 'CPF', ..."""

    @property
    @abstractmethod
    def id_field_placeholder(self) -> str:
        """Placeholder: '12.345.678-9', 'ABC123456AB1', ..."""

    @property
    @abstractmethod
    def id_pattern_regex(self) -> str:
        """Regex para validación básica en frontend (sin dígito verificador)."""

    @abstractmethod
    def validate_national_id(self, value: str) -> ValidationResult:
        """
        Valida y formatea el ID nacional (RUT, RFC, CURP, DNI, NIT, CPF, etc.).
        Debe ser determinista, sin side effects, puro.
        """

    @abstractmethod
    def generate_national_id(self, *, entity_type: str = "person") -> str:
        """
        Genera un ID válido aleatorio (para tests, seeding, demo).
        entity_type: "person" | "company" | "foreign" | ...
        """

    # --- División administrativa ---
    @abstractmethod
    def get_regions(self) -> list[AdminDivision]:
        """Regiones/Estados/Provincias de primer nivel."""

    @abstractmethod
    def get_communes(self, region_code: str | None = None) -> list[AdminDivision]:
        """
        Comunas/Municipios. Si region_code=None, devuelve TODAS.
        """

    @abstractmethod
    def find_commune(self, code: str) -> AdminDivision | None:
        """Busca comuna por código exacto."""

    @abstractmethod
    def search_communes(self, query: str, limit: int = 10) -> list[AdminDivision]:
        """Búsqueda fuzzy por nombre (para autocomplete)."""

    # --- Feriados ---
    @abstractmethod
    def get_holidays(self, year: int, region_code: str | None = None) -> list[Holiday]:
        """
        Feriados del año. Si region_code, incluye feriados regionales.
        """

    @abstractmethod
    def is_holiday(self, dt: date, region_code: str | None = None) -> Holiday | None:
        """Verifica si una fecha es feriado (retorna el feriado o None)."""

    @abstractmethod
    def get_irreducible_holidays(self, year: int) -> list[Holiday]:
        """Solo feriados irrenunciables (comercio cerrado)."""

    # --- Utilidades de negocio ---
    def is_business_day(self, dt: date, region_code: str | None = None) -> bool:
        """Día hábil = no fin de semana + no feriado (incluye irreducibles)."""
        if dt.weekday() >= 5:  # Sábado=5, Domingo=6
            return False
        return self.is_holiday(dt, region_code) is None

    def next_business_day(self, dt: date, region_code: str | None = None) -> date:
        """Primer día hábil >= dt."""
        current = dt
        while not self.is_business_day(current, region_code):
            current = current.replace(day=current.day + 1)
        return current

    def add_business_days(self, dt: date, days: int, region_code: str | None = None) -> date:
        """Suma días hábiles (para SLA)."""
        current = dt
        added = 0
        while added < days:
            current = current.replace(day=current.day + 1)
            if self.is_business_day(current, region_code):
                added += 1
        return current

    # --- Formateo ---
    @abstractmethod
    def format_currency(self, amount: int | float, *, symbol: bool = True) -> str:
        """Formatea moneda local (ej. '$ 1.234.567' CLP, 'MX$ 1,234.56' MXN)."""

    @abstractmethod
    def parse_currency(self, text: str) -> float | None:
        """Parsea string local a float (inverso de format_currency)."""

    def format_date(self, dt: date, *, short: bool = False) -> str:
        """Formato de fecha local (override si difiere de ISO)."""
        return dt.isoformat()

    # --- Metadata para UI ---
    def get_ui_config(self) -> dict[str, Any]:
        """Configuración que el frontend consume al iniciar."""
        return {
            "countryCode": self.country_code,
            "countryName": self.country_name,
            "currency": {"code": self.currency_code, "symbol": self.currency_symbol},
            "timezone": self.timezone_default,
            "locale": self.locale_default,
            "idField": {
                "label": self.id_field_label,
                "placeholder": self.id_field_placeholder,
                "pattern": self.id_pattern_regex,
            },
            "dateFormat": "DD/MM/YYYY",
            "numberFormat": {"decimal": ",", "thousands": ".", "grouping": 3},
        }