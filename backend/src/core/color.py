"""
Las medidas de color: contraste, distinguibilidad y calidez.

POR QUE ESTE MODULO Y NO DENTRO DEL SCRIPT
-------------------------------------------
La logica de medir color vivia en `scripts/paleta.py`, que era un script de
consola: se ejecutaba a mano y su resultado era un cartel que alguien leia y
decidia si hacer caso. Eso no es un control: es una costumbre.

Lo que hace este modulo es lo mismo que hacia el script, pero como
FUNCIONES: sin imprimir nada, sin leer archivos y sin decidir nada. Con eso
se puede:

  - llamar desde un test (y que falle solo si la paleta REAL se rompe),
  - llamar desde el script (que sigue sirviendo para mirar el detalle),
  - usar en cualquier otra parte que necesite medir.

QUE MIDE
--------
1. **Contraste** (WCAG 2.1): si dos colores se leen uno sobre otro.
2. **Distancia perceptual** (CIE76 sobre un espacio Lab aproximado): si dos
   colores se CONFUNDEN entre si. No es lo mismo que el contraste: dos
   colores pueden tener 6:1 de contraste y aun asi ser el mismo color para
   el ojo.
3. **Calidez**: cuanto se acerca el canal rojo al azul. Es la unica medida
   objetiva de "calidez" que importa: la percepcion de temperatura de un
   color es, en la practica, el balance de sus canales, y un gris azulado se
   siente clinico aunque tenga el mismo tono.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def hex_a_rgb(color: str) -> tuple[int, int, int]:
    """`#rrggbb` (o `#rgb`) a una tupla de 0 a 255."""
    h = color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"Color no valido: {color!r}")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def a_rgb_lineal(color: str) -> list[float]:
    """Los canales en escala lineal (lo que necesita la luminancia)."""
    return [_lineal(v / 255) for v in hex_a_rgb(color)]


def _lineal(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


# ---------------------------------------------------------------------------
# Contraste
# ---------------------------------------------------------------------------

def luminancia_relativa(color: str) -> float:
    """La luminancia relativa de WCAG 2.1."""
    r, g, b = a_rgb_lineal(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a: str, b: str) -> float:
    """La razon de contraste de WCAG, entre 1 y 21."""
    la, lb = luminancia_relativa(a), luminancia_relativa(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# Distinguibilidad
# ---------------------------------------------------------------------------

def distancia_perceptual(a: str, b: str) -> float:
    """
    Distancia perceptual (CIE76 sobre un Lab aproximado).

    Es la que contesta la pregunta que importa: "un operador confundiría
    estos dos estados?". Dos colores pueden tener 6:1 de contraste entre si
    y aun asi ser el mismo color para el ojo.

    Los valores tipicos van de 0 (identicos) a 100 (muy distintos). Por
    encima de 25 dos colores se distinguen de un vistazo; por debajo de 20
    hay que forzar la mirada.
    """
    def a_lab(hexc: str) -> tuple[float, float, float]:
        r, g, b = a_rgb_lineal(hexc)
        x = r * 0.4124 + g * 0.3576 + b * 0.1805
        y = r * 0.2126 + g * 0.7152 + b * 0.0722
        z = r * 0.0193 + g * 0.1192 + b * 0.9505

        def f(t: float) -> float:
            return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

        return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))

    l1, a1, b1 = a_lab(a)
    l2, a2, b2 = a_lab(b)
    return ((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2) ** 0.5


# ---------------------------------------------------------------------------
# Calidez y saturacion
# ---------------------------------------------------------------------------

def temperatura(color: str) -> float:
    """
    "frio" < 0 < "calido": cuanto se acerca el canal rojo al azul.

    Va de -1 (azul puro) a +1 (rojo puro).
    """
    r, g, b = hex_a_rgb(color)
    return (r - b) / 255


def saturacion_relativa(color: str) -> float:
    """De 0 (gris) a 1 (color puro)."""
    r, g, b = hex_a_rgb(color)
    mx, mn = max(r, g, b), min(r, g, b)
    return 0 if mx == 0 else (mx - mn) / mx
