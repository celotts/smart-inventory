"""Feriados México (Ley Federal del Trabajo + estatales + puentes)."""
from __future__ import annotations

from datetime import date, timedelta
from src.core.localization.base import Holiday


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """
    Retorna el n-ésimo weekday del mes (1=primero, 2=segundo, -1=último, -2=penúltimo).
    weekday: 0=Lunes, 6=Domingo
    """
    # Primer día del mes
    first = date(year, month, 1)
    # Diferencia al primer weekday buscado
    days_ahead = (weekday - first.weekday()) % 7
    first_target = first + timedelta(days=days_ahead)

    if n > 0:
        return first_target + timedelta(weeks=n - 1)
    else:
        # Último: ir al primer día del mes siguiente y retroceder
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        last_day = next_month - timedelta(days=1)
        days_back = (last_day.weekday() - weekday) % 7
        last_target = last_day - timedelta(days=days_back)
        return last_target + timedelta(weeks=n + 1)  # n es negativo


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


def get_holidays_mexico(year: int, estado_code: str | None = None) -> list[Holiday]:
    holidays: list[Holiday] = []

    # ---- FERIADOS FEDERALES OBLIGATORIOS (Ley Federal del Trabajo Art. 74) ----

    # 1 de Enero - Año Nuevo
    holidays.append(Holiday(
        date=date(year, 1, 1),
        name="Año Nuevo",
        type="national",
        is_irreducible=True,
    ))

    # Primer lunes de febrero - Constitución (5 de febrero)
    holidays.append(Holiday(
        date=_nth_weekday(year, 2, 0, 1),  # 0=Lunes, 1=primero
        name="Día de la Constitución",
        type="national",
        is_irreducible=False,  # Se mueve al lunes
    ))

    # Tercer lunes de marzo - Natalicio de Benito Juárez (21 de marzo)
    holidays.append(Holiday(
        date=_nth_weekday(year, 3, 0, 3),  # 3=tercero
        name="Natalicio de Benito Juárez",
        type="national",
        is_irreducible=False,
    ))

    # Jueves y Viernes Santos (Semana Santa)
    pascua = _calcular_pascua(year)
    jueves_santo = pascua - timedelta(days=3)
    viernes_santo = pascua - timedelta(days=2)
    holidays.append(Holiday(
        date=jueves_santo,
        name="Jueves Santo",
        type="religious",
        is_irreducible=False,
    ))
    holidays.append(Holiday(
        date=viernes_santo,
        name="Viernes Santo",
        type="religious",
        is_irreducible=False,
    ))

    # 1 de Mayo - Día del Trabajo
    holidays.append(Holiday(
        date=date(year, 5, 1),
        name="Día del Trabajo",
        type="national",
        is_irreducible=True,
    ))

    # 16 de Septiembre - Independencia
    holidays.append(Holiday(
        date=date(year, 9, 16),
        name="Día de la Independencia",
        type="national",
        is_irreducible=True,
    ))

    # Tercer lunes de noviembre - Revolución (20 de noviembre)
    holidays.append(Holiday(
        date=_nth_weekday(year, 11, 0, 3),
        name="Día de la Revolución",
        type="national",
        is_irreducible=False,
    ))

    # 25 de Diciembre - Navidad
    holidays.append(Holiday(
        date=date(year, 12, 25),
        name="Navidad",
        type="national",
        is_irreducible=True,
    ))

    # ---- FERIADOS NO OBLIGATORIOS PERO COMUNES (bancarios, escolares) ----

    # 6 de Enero - Día de Reyes (solo escolar)
    holidays.append(Holiday(
        date=date(year, 1, 6),
        name="Día de Reyes",
        type="religious",
        is_irreducible=False,
    ))

    # 2 de Febrero - Día de la Candelaria
    holidays.append(Holiday(
        date=date(year, 2, 2),
        name="Día de la Candelaria",
        type="religious",
        is_irreducible=False,
    ))

    # 5 de Mayo - Batalla de Puebla
    holidays.append(Holiday(
        date=date(year, 5, 5),
        name="Batalla de Puebla",
        type="national",
        is_irreducible=False,
    ))

    # 10 de Mayo - Día de las Madres
    holidays.append(Holiday(
        date=date(year, 5, 10),
        name="Día de las Madres",
        type="cultural",
        is_irreducible=False,
    ))

    # 15 de Mayo - Día del Maestro
    holidays.append(Holiday(
        date=date(year, 5, 15),
        name="Día del Maestro",
        type="cultural",
        is_irreducible=False,
    ))

    # 1 de Noviembre - Día de Todos los Santos
    holidays.append(Holiday(
        date=date(year, 11, 1),
        name="Día de Todos los Santos",
        type="religious",
        is_irreducible=False,
    ))

    # 2 de Noviembre - Día de Muertos
    holidays.append(Holiday(
        date=date(year, 11, 2),
        name="Día de Muertos",
        type="cultural",
        is_irreducible=False,
    ))

    # 12 de Diciembre - Virgen de Guadalupe
    holidays.append(Holiday(
        date=date(year, 12, 12),
        name="Día de la Virgen de Guadalupe",
        type="religious",
        is_irreducible=False,
    ))

    # 24 de Diciembre - Nochebuena
    holidays.append(Holiday(
        date=date(year, 12, 24),
        name="Nochebuena",
        type="religious",
        is_irreducible=False,
    ))

    # 31 de Diciembre - Fin de Año
    holidays.append(Holiday(
        date=date(year, 12, 31),
        name="Fin de Año",
        type="cultural",
        is_irreducible=False,
    ))

    # ---- FERIADOS ESTATALES (ejemplos) ----
    if estado_code == "09":  # Ciudad de México
        holidays.append(Holiday(
            date=date(year, 2, 14),
            name="Aniversario de la Fundación de México-Tenochtitlan",
            type="regional",
            regions=["09"],
        ))

    if estado_code == "14":  # Jalisco
        holidays.append(Holiday(
            date=date(year, 3, 19),
            name="Día de San José (Patrono de Guadalajara)",
            type="regional",
            regions=["14"],
        ))

    if estado_code == "19":  # Nuevo León
        holidays.append(Holiday(
            date=date(year, 9, 23),
            name="Aniversario de la Fundación de Monterrey",
            type="regional",
            regions=["19"],
        ))

    return sorted(holidays, key=lambda h: h.date)