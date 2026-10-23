"""Validador RUT chileno (algoritmo módulo 11)."""
from __future__ import annotations

import re

from src.core.localization.base import ValidationResult


_RUT_REGEX = re.compile(r"^(\d{1,3}(?:\.\d{3})*)-([\dkK])$")
_RUT_CLEAN_REGEX = re.compile(r"[^0-9kK]")


def _calcular_dv(cuerpo: int) -> str:
    """Calcula dígito verificador módulo 11."""
    suma = 0
    multiplo = 2
    while cuerpo > 0:
        suma += (cuerpo % 10) * multiplo
        cuerpo //= 10
        multiplo = multiplo + 1 if multiplo < 7 else 2
    dv = 11 - (suma % 11)
    if dv == 11:
        return "0"
    if dv == 10:
        return "K"
    return str(dv)


def validar_rut(value: str) -> ValidationResult:
    """
    Valida RUT chileno.
    Acepta: "12345678-9", "12.345.678-9", "123456789", "12345678k"
    Devuelve formato canónico: "12.345.678-9"
    """
    if not value or not value.strip():
        return ValidationResult(valid=False, raw=value, error="RUT es obligatorio")

    raw = value.strip().upper()
    clean = _RUT_CLEAN_REGEX.sub("", raw)

    if len(clean) < 2:
        return ValidationResult(valid=False, raw=raw, error="RUT muy corto")

    cuerpo_str, dv_ingresado = clean[:-1], clean[-1]

    if not cuerpo_str.isdigit():
        return ValidationResult(valid=False, raw=raw, error="Cuerpo del RUT debe ser numérico")

    cuerpo = int(cuerpo_str)
    if cuerpo == 0:
        return ValidationResult(valid=False, raw=raw, error="RUT no puede ser cero")

    dv_calculado = _calcular_dv(cuerpo)

    if dv_ingresado != dv_calculado:
        return ValidationResult(
            valid=False,
            raw=raw,
            error=f"Dígito verificador inválido (debe ser {dv_calculado})",
        )

    # Formatear con puntos y guión
    cuerpo_formateado = ""
    while cuerpo > 0:
        cuerpo_formateado = f"{cuerpo % 1000:03d}" + ("." + cuerpo_formateado if cuerpo_formateado else "")
        cuerpo //= 1000

    return ValidationResult(
        valid=True,
        raw=raw,
        formatted=f"{cuerpo_formateado}-{dv_calculado}",
        metadata={"dv": dv_calculado, "type": "persona_natural" if cuerpo < 50_000_000 else "persona_juridica"},
    )


def formatear_rut(cuerpo: int, dv: str) -> str:
    """Formatea cuerpo numérico + DV a string canónico."""
    cuerpo_str = str(cuerpo)
    partes = []
    while len(cuerpo_str) > 3:
        partes.insert(0, cuerpo_str[-3:])
        cuerpo_str = cuerpo_str[:-3]
    partes.insert(0, cuerpo_str)
    return ".".join(partes) + "-" + dv.upper()


def generar_rut_aleatorio() -> str:
    """Genera RUT válido aleatorio (para tests/seeding)."""
    import random
    cuerpo = random.randint(1_000_000, 30_000_000)
    dv = _calcular_dv(cuerpo)
    return formatear_rut(cuerpo, dv)