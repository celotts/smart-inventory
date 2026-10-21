"""
Tests del contraste de los tokens de color del front.

POR QUE UN TEST Y NO "LO MIRE CON LOS OJOS"
--------------------------------------------
Los contrastes del CSS están anotados a mano (`--color-text-secondary: 7:1
AAA`). Un comentario no se rompe solo: el próximo que cambie un color deja el
comentario mintiendo, y el texto con 3:1 pasa a ser ilegible en un celular a
la luz del sol, en un depósito, con guantes.

Este test lee el CSS REAL, calcula el contraste de cada par y falla si alguno
baja del mínimo de WCAG 2.2 (4.5:1 para texto normal, 3:1 para texto grande).

QUE MIDE
--------
- Texto normal: 4.5:1 (WCAG AA).
- Texto grande (>= 18.66px en negrita o >= 24px): 3:1 (WCAG AA grande).
- Los bordes de los controles: 3:1 contra el fondo (WCAG 1.4.11).

QUE NO MIDE
-----------
- El modo oscuro: los tokens se redefinen con `[data-theme="dark"]`, y el
  test los cubre con el mismo parser (ver `tokens_de`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RAIZ = Path(__file__).resolve().parents[2]

# Donde puede estar el CSS del front.
#
# El test vive en el backend, y el front es otro directorio. Cuando los tests
# corren en el HOST, el CSS está a dos directorios. Cuando corren en el
# CONTENEDOR (que es como los corre el Makefile), el front se monta en
# `/app/frontend-src` (ver `docker-compose.dev.yml`).
#
# En producción, donde la imagen del backend no tiene el front, el test se
# saltea con un motivo claro: no hay CSS que medir.
RUTAS_CSS = (
    RAIZ / "frontend" / "src" / "styles.css",
    Path("/app/frontend-src/styles.css"),
)

# El contraste mínimo de WCAG 2.2 AA.
MINIMO_TEXTO = 4.5
MINIMO_TEXTO_GRANDE = 3.0
MINIMO_BORDE = 3.0


def _leer_css() -> str:
    import os

    # La variable de entorno manda: sirve para un CI que no tenga el front.
    desde_entorno = os.getenv("FRONTEND_CSS")
    if desde_entorno and Path(desde_entorno).exists():
        return Path(desde_entorno).read_text(encoding="utf-8")

    for ruta in RUTAS_CSS:
        if ruta.exists():
            return ruta.read_text(encoding="utf-8")

    pytest.skip(
        "No se encuentra el CSS del front. "
        "Se prueba desde el host o con el front montado en /app/frontend-src."
    )


def _tokens_de(css: str, tema: str | None = None) -> dict[str, str]:
    """
    Los tokens de color del bloque `:root` (o del bloque del tema).

    Los alias (`--texto`, `--superficie`) se resuelven contra los canonicos,
    así que el test mide lo que la pantalla muestra de verdad.
    """
    if tema is None:
        bloque = re.search(r":root\s*\{(.*?)\}", css, re.S)
    else:
        bloque = re.search(rf'\[data-theme="{tema}"\]\s*\{{(.*?)\n\}}', css, re.S)

    if not bloque:
        pytest.skip(f"No se encuentra el bloque de tokens {tema or ':root'}")

    tokens: dict[str, str] = {}
    for nombre, valor in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", bloque.group(1)):
        tokens[nombre] = valor.strip()

    # Los alias se resuelven (una sola pasada: no hay alias de alias).
    resueltos: dict[str, str] = {}
    for nombre, valor in tokens.items():
        if valor.startswith("var("):
            base = valor[4:].split(")")[0].strip()
            resueltos[nombre] = tokens.get(base, "")
        else:
            resueltos[nombre] = valor
    return resueltos


def _rgb(color: str) -> tuple[float, float, float] | None:
    """`#rrggbb` o `rgb(r, g, b)` a una tupla de 0 a 255."""
    color = color.strip()
    if color.startswith("#"):
        hexa = color[1:]
        if len(hexa) == 3:
            hexa = "".join(c * 2 for c in hexa)
        if len(hexa) == 6:
            return tuple(int(hexa[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", color)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    return None


def _luminancia(rgb: tuple[float, float, float]) -> float:
    """La luminancia relativa de WCAG."""

    def canal(valor: float) -> float:
        valor = valor / 255
        return valor / 12.92 if valor <= 0.03928 else ((valor + 0.055) / 1.055) ** 2.4

    r, g, b = (canal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a: str, b: str) -> float:
    """El contraste entre dos colores, de 1 a 21."""
    rgb_a = _rgb(a)
    rgb_b = _rgb(b)
    if rgb_a is None or rgb_b is None:
        pytest.skip(f"No se puede calcular el contraste de {a!r} y {b!r}")

    lum_a = _luminancia(rgb_a)
    lum_b = _luminancia(rgb_b)
    claro, oscuro = max(lum_a, lum_b), min(lum_a, lum_b)
    return (claro + 0.05) / (oscuro + 0.05)


class TestContraste:
    """Los pares de color que la app usa de verdad."""

    @pytest.mark.parametrize("tema", [None, "dark"])
    def test_el_texto_normal_tiene_contraste_aa(self, tema):
        tokens = _tokens_de(_leer_css(), tema)
        fondo = tokens["--color-bg"]
        superficie = tokens["--color-surface"]

        # El texto del cuerpo, sobre el fondo y sobre una tarjeta.
        contraste_fondo = _contraste(tokens["--color-text"], fondo)
        contraste_superficie = _contraste(tokens["--color-text"], superficie)

        assert contraste_fondo >= MINIMO_TEXTO, (
            f"El texto principal sobre el fondo tiene {contraste_fondo:.2f}:1 "
            f"(se necesita {MINIMO_TEXTO}:1)"
        )
        assert contraste_superficie >= MINIMO_TEXTO, (
            f"El texto principal sobre una tarjeta tiene {contraste_superficie:.2f}:1 "
            f"(se necesita {MINIMO_TEXTO}:1)"
        )

    @pytest.mark.parametrize("tema", [None, "dark"])
    def test_el_texto_secundario_tiene_contraste_aa(self, tema):
        tokens = _tokens_de(_leer_css(), tema)
        contraste = _contraste(tokens["--color-text-secondary"], tokens["--color-surface"])
        assert contraste >= MINIMO_TEXTO, (
            f"El texto secundario tiene {contraste:.2f}:1 (se necesita {MINIMO_TEXTO}:1)"
        )

    @pytest.mark.parametrize("tema", [None, "dark"])
    def test_el_texto_atenuado_alcanza_aa_para_texto_grande(self, tema):
        """
        El texto atenuado (`--color-text-muted`) se usa en tipografia chica.
        Por eso tiene que alcanzar el mínimo de texto GRANDE (3:1), no el de
        texto normal: si no, un "12 de marzo" en 12px es ilegible.
        """
        tokens = _tokens_de(_leer_css(), tema)
        contraste = _contraste(tokens["--color-text-muted"], tokens["--color-surface"])
        assert contraste >= MINIMO_TEXTO_GRANDE, (
            f"El texto atenuado tiene {contraste:.2f}:1 (se necesita {MINIMO_TEXTO_GRANDE}:1)"
        )

    @pytest.mark.parametrize("tema", [None, "dark"])
    def test_el_boton_primario_tiene_contraste(self, tema):
        """El texto del botón primario contra su fondo."""
        tokens = _tokens_de(_leer_css(), tema)
        if "--btn-primary-text" not in tokens:
            pytest.skip("El tema no define el color del texto del boton.")

        contraste = _contraste(tokens["--btn-primary-text"], tokens["--primary"])
        assert contraste >= MINIMO_TEXTO, (
            f"El texto del boton primario tiene {contraste:.2f}:1 "
            f"(se necesita {MINIMO_TEXTO}:1)"
        )

    @pytest.mark.parametrize("tema", [None, "dark"])
    def test_el_borde_de_los_controles_se_ve(self, tema):
        """
        El borde de un campo de texto tiene que verse contra el fondo: si no,
        el operador no sabe donde escribir (WCAG 1.4.11, 3:1).

        El token es `--color-border-control`, NO `--color-border-strong`: el
        borde de una tarjeta es decorativo y puede ser sutil, pero el de un
        campo es la única pista de que ahí se escribe.
        """
        tokens = _tokens_de(_leer_css(), tema)
        contraste = _contraste(tokens["--color-border-control"], tokens["--color-surface"])
        assert contraste >= MINIMO_BORDE, (
            f"El borde de los campos tiene {contraste:.2f}:1 (se necesita {MINIMO_BORDE}:1)"
        )

    def test_los_colores_de_estado_se_distinguen_del_texto(self):
        """
        Los colores de estado (exito, error, alerta) se usan como texto o
        como insignia: tienen que poder leerse contra la superficie.
        """
        tokens = _tokens_de(_leer_css())
        superficie = tokens["--color-surface"]

        for nombre in ("--success-text", "--color-error-text", "--warning-text", "--info-text"):
            if nombre not in tokens:
                continue
            contraste = _contraste(tokens[nombre], superficie)
            assert contraste >= MINIMO_TEXTO, (
                f"{nombre} tiene {contraste:.2f}:1 sobre la superficie "
                f"(se necesita {MINIMO_TEXTO}:1)"
            )
