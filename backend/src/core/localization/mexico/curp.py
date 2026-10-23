"""Validador CURP mexicano."""
from __future__ import annotations

import re
from datetime import date

from src.core.localization.base import ValidationResult


_CURP_REGEX = re.compile(
    r"^([A-ZÑ]{4})(\d{6})([HM])([A-Z]{2})([A-Z]{3})([0-9A-Z])(\d)$"
)

# Estados mexicanos (códigos de 2 letras)
_ESTADOS_CURP = {
    "AS", "BC", "BS", "CC", "CL", "CM", "CS", "CH", "DF", "DG",
    "GT", "GR", "HG", "JC", "MC", "MN", "MS", "NT", "NL", "OC",
    "PL", "QT", "QR", "SP", "SL", "SR", "TC", "TS", "TL", "VZ",
    "YN", "ZS", "NE"  # Nacido en el extranjero
}

# Valores para cálculo del dígito verificador
_CARACTERES_CURP = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
_VALORES_CURP = {c: i for i, c in enumerate(_CARACTERES_CURP)}


def _calcular_dv_curp(curp_sin_dv: str) -> str:
    """Calcula el dígito verificador del CURP."""
    suma = 0
    for i, char in enumerate(curp_sin_dv):
        valor = _VALORES_CURP.get(char, 0)
        peso = 18 - i
        suma += valor * peso

    residuo = suma % 10
    if residuo == 0:
        return "0"
    return str(10 - residuo)


def _es_fecha_valida(anio: int, mes: int, dia: int) -> bool:
    try:
        date(anio, mes, dia)
        return True
    except ValueError:
        return False


def validar_curp(value: str) -> ValidationResult:
    """
    Valida CURP mexicano (18 caracteres).
    Formato: 4 letras + 6 dígitos fecha + H/M + 2 letras estado + 3 letras apellido/nombre + 1 dígito + 1 dígito verificador
    """
    if not value or not value.strip():
        return ValidationResult(valid=False, raw=value, error="CURP es obligatorio")

    raw = value.strip().upper().replace(" ", "").replace("-", "")

    if len(raw) != 18:
        return ValidationResult(
            valid=False, raw=raw, error="CURP debe tener 18 caracteres"
        )

    match = _CURP_REGEX.match(raw)
    if not match:
        return ValidationResult(
            valid=False, raw=raw, error="Formato CURP inválido"
        )

    letras_iniciales, fecha_str, sexo, estado, resto, digito_verif_1, digito_verif_2 = match.groups()

    # Validar fecha (AAMMDD)
    try:
        anio = int(fecha_str[:2])
        mes = int(fecha_str[2:4])
        dia = int(fecha_str[4:6])
    except ValueError:
        return ValidationResult(valid=False, raw=raw, error="Fecha en CURP inválida")

    anio_completo = 1900 + anio if anio > 25 else 2000 + anio
    if not _es_fecha_valida(anio_completo, mes, dia):
        return ValidationResult(valid=False, raw=raw, error=f"Fecha {fecha_str} inválida en CURP")

    # Validar sexo
    if sexo not in ("H", "M"):
        return ValidationResult(valid=False, raw=raw, error="Sexo inválido (H/M)")

    # Validar estado
    if estado not in _ESTADOS_CURP:
        return ValidationResult(valid=False, raw=raw, error=f"Código de estado inválido: {estado}")

    # Validar dígito verificador (posición 17, base 10)
    # El algoritmo oficial usa el dígito 17 como verificador
    curp_17 = raw[:17]
    dv_calculado = _calcular_dv_curp(curp_17)

    if digito_verif_2 != dv_calculado:
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
            "sexo": "Hombre" if sexo == "H" else "Mujer",
            "estado": estado,
            "fecha_nacimiento": f"{anio_completo:04d}-{mes:02d}-{dia:02d}",
            "dv": dv_calculado,
        },
    )


def generar_curp_aleatorio() -> str:
    """Genera CURP válido aleatorio (para tests/seeding)."""
    import random

    consonantes = "BCDFGHJKLMNPQRSTVWXYZ"
    vocales = "AEIOU"

    # 4 letras iniciales
    letras = (
        random.choice(consonantes) +
        random.choice(vocales) +
        random.choice(consonantes) +
        random.choice(consonantes)
    )

    # Fecha (1940-2010)
    anio = random.randint(40, 99)
    mes = random.randint(1, 12)
    dia = random.randint(1, 28)
    fecha_str = f"{anio:02d}{mes:02d}{dia:02d}"

    # Sexo
    sexo = random.choice(["H", "M"])

    # Estado
    estado = random.choice(list(_ESTADOS_CURP))

    # 3 letras del nombre
    nombre_letras = "".join(random.choices(consonantes + vocales, k=3))

    # Dígito de diferenciación (0-9, A-Z)
    diff = random.choice("0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ")

    # Construir CURP sin dígito verificador
    curp_17 = letras + fecha_str + sexo + estado + nombre_letras + diff

    # Calcular dígito verificador
    dv = _calcular_dv_curp(curp_17)

    return curp_17 + dv