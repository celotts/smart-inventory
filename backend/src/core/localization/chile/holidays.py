"""Feriados Chile (Ley 19.973 + religiosos + regionales)."""
from __future__ import annotations

from datetime import date, timedelta
from src.core.localization.base import Holiday


# Feriados fijos nacionales
_FIJOS_NACIONALES = [
    (1, 1, "Año Nuevo", "national", False),
    (5, 1, "Día del Trabajo", "national", True),   # irrenunciable
    (5, 21, "Día de las Glorias Navales", "national", False),
    (6, 29, "San Pedro y San Pablo", "religious", False),
    (7, 16, "Virgen del Carmen", "religious", False),
    (8, 15, "Asunción de la Virgen", "religious", False),
    (9, 18, "Independencia Nacional", "national", True),   # irrenunciable
    (9, 19, "Día de las Glorias del Ejército", "national", True),  # irrenunciable
    (10, 12, "Encuentro de Dos Mundos", "national", False),
    (10, 31, "Día de las Iglesias Evangélicas", "religious", False),
    (11, 1, "Todos los Santos", "religious", False),
    (12, 8, "Inmaculada Concepción", "religious", False),
    (12, 25, "Navidad", "religious", True),  # irrenunciable
]

# Feriados variables (Semana Santa)
def _calcular_pascua(year: int) -> date:
    """Algoritmo de Gauss para domingo de Pascua."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 12
    dia = ((h + l - 7 * m + 114) % 12) + 1
    return date(year, mes, dia)


def get_holidays_chile(year: int, region_code: str | None = None) -> list[Holiday]:
    holidays: list[Holiday] = []

    # Fijos
    for mes, dia, nombre, tipo, irrenunciable in _FIJOS_NACIONALES:
        holidays.append(Holiday(
            date=date(year, mes, dia),
            name=nombre,
            type=tipo,
            is_irreducible=irrenunciable,
        ))

    # Variables (Semana Santa)
    pascua = _calcular_pascua(year)
    viernes_santo = pascua - timedelta(days=2)
    sabado_santo = pascua - timedelta(days=1)
    holidays.append(Holiday(date=viernes_santo, name="Viernes Santo", type="religious", is_irreducible=False))
    holidays.append(Holiday(date=sabado_santo, name="Sábado Santo", type="religious", is_irreducible=False))

    # Regionales (ejemplo: 20/10 Región de Arica - Fundación de Arica)
    if region_code == "15":
        holidays.append(Holiday(
            date=date(year, 10, 20),
            name="Fundación de Arica",
            type="regional",
            regions=["15"],
        ))

    return sorted(holidays, key=lambda h: h.date)