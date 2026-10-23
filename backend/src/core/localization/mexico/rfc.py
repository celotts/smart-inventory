"""Validador RFC mexicano (personas físicas y morales)."""
from __future__ import annotations

import re
from datetime import date

from src.core.localization.base import ValidationResult


# Constantes para validación
_RFC_PERSONA_FISICA_REGEX = re.compile(
    r"^([A-ZÑ&]{4})(\d{6})([A-Z0-9]{3})$"
)
_RFC_PERSONA_MORAL_REGEX = re.compile(
    r"^([A-ZÑ&]{3})(\d{6})([A-Z0-9]{3})$"
)

# Caracteres válidos para el homoclave (último dígito puede ser dígito o A)
_CARACTERES_RFC = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"

# Valores para cálculo del dígito verificador
_VALORES_CARACTERES = {c: i for i, c in enumerate(_CARACTERES_RFC)}


def _calcular_dv_rfc(rfc_sin_dv: str) -> str:
    """
    Calcula el dígito verificador del RFC (algoritmo SAT).
    """
    suma = 0
    longitud = len(rfc_sin_dv)
    for i, char in enumerate(rfc_sin_dv):
        valor = _VALORES_CARACTERES.get(char, 0)
        peso = longitud + 1 - i
        suma += valor * peso

    residuo = suma % 11
    if residuo == 0:
        return "0"
    dv = 11 - residuo
    if dv == 10:
        return "A"
    return str(dv)


def _es_fecha_valida(anio: int, mes: int, dia: int) -> bool:
    """Verifica si la fecha es válida (incluye años bisiestos)."""
    try:
        date(anio + 1900 if anio < 100 else anio, mes, dia)
        return True
    except ValueError:
        return False


def validar_rfc(value: str) -> ValidationResult:
    """
    Valida RFC mexicano (persona física o moral).
    Acepta: "GARC800101XXX" (13 chars) o "EMP900101XXX" (12 chars)
    Devuelve formato canónico en mayúsculas.
    """
    if not value or not value.strip():
        return ValidationResult(valid=False, raw=value, error="RFC es obligatorio")

    raw = value.strip().upper().replace(" ", "").replace("-", "")

    # Verificar longitud
    if len(raw) not in (12, 13):
        return ValidationResult(
            valid=False,
            raw=raw,
            error="RFC debe tener 12 (persona moral) o 13 (persona física) caracteres",
        )

    # Determinar tipo por longitud
    es_fisica = len(raw) == 13

    if es_fisica:
        match = _RFC_PERSONA_FISICA_REGEX.match(raw)
        if not match:
            return ValidationResult(
                valid=False,
                raw=raw,
                error="Formato RFC persona física inválido: 4 letras + 6 dígitos fecha + 3 homoclave",
            )
        letras, fecha_str, homoclave = match.groups()
    else:
        match = _RFC_PERSONA_MORAL_REGEX.match(raw)
        if not match:
            return ValidationResult(
                valid=False,
                raw=raw,
                error="Formato RFC persona moral inválido: 3 letras + 6 dígitos fecha + 3 homoclave",
            )
        letras, fecha_str, homoclave = match.groups()

    # Validar fecha (AAMMDD)
    try:
        anio = int(fecha_str[:2])
        mes = int(fecha_str[2:4])
        dia = int(fecha_str[4:6])
    except ValueError:
        return ValidationResult(
            valid=False, raw=raw, error="Fecha en RFC inválida (debe ser AAMMDD)"
        )

    # Ajustar año: RFC usa dos dígitos, asumir 1900-1999 para >25, 2000-2025 para <=25
    anio_completo = 1900 + anio if anio > 25 else 2000 + anio
    if not _es_fecha_valida(anio_completo, mes, dia):
        return ValidationResult(
            valid=False, raw=raw, error=f"Fecha {fecha_str} inválida en RFC"
        )

    # Validar homoclave (3 caracteres alfanuméricos)
    if not re.match(r"^[A-Z0-9]{3}$", homoclave):
        return ValidationResult(
            valid=False, raw=raw, error="Homoclave inválida (3 caracteres A-Z, 0-9)"
        )

    # Validar dígito verificador (último char de homoclave)
    rfc_sin_dv = raw[:-1]
    dv_calculado = _calcular_dv_rfc(rfc_sin_dv)
    dv_ingresado = raw[-1]

    if dv_ingresado != dv_calculado:
        return ValidationResult(
            valid=False,
            raw=raw,
            error=f"Dígito verificador inválido (debe ser {dv_calculado})",
        )

    return ValidationResult(
        valid=True,
        raw=raw,
        formatted=raw,
        metadata={
            "tipo": "persona_fisica" if es_fisica else "persona_moral",
            "fecha_nacimiento_constitucion": f"{anio_completo:04d}-{mes:02d}-{dia:02d}",
            "dv": dv_calculado,
        },
    )


def generar_rfc_aleatorio(*, entity_type: str = "person") -> str:
    """Genera RFC válido aleatorio (para tests/seeding)."""
    import random

    # Letras aleatorias (evitar palabras ofensivas - simplificado)
    consonantes = "BCDFGHJKLMNPQRSTVWXYZ"
    vocales = "AEIOU"

    if entity_type == "person":
        # Persona física: 4 letras
        letra1 = random.choice(consonantes)
        letra2 = random.choice(vocales)
        letra3 = random.choice(consonantes)
        letra4 = random.choice(consonantes)
        letras = letra1 + letra2 + letra3 + letra4
    else:
        # Persona moral: 3 letras
        letras = "".join(random.choices(consonantes + vocales, k=3))

    # Fecha aleatoria (entre 1940 y 2010 para personas, 1980-2020 para morales)
    if entity_type == "person":
        anio = random.randint(40, 99)  # 1940-1999
    else:
        anio = random.randint(80, 20)  # 1980-2020 (wrap around)

    mes = random.randint(1, 12)
    dia = random.randint(1, 28)  # Simplificado

    fecha_str = f"{anio:02d}{mes:02d}{dia:02d}"

    # Homoclave aleatoria (2 chars + dígito verificador)
    homoclave_base = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=2))
    rfc_sin_dv = letras + fecha_str + homoclave_base
    dv = _calcular_dv_rfc(rfc_sin_dv)

    return rfc_sin_dv + dv