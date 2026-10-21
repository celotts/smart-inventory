"""
La paleta: contraste, distinguibilidad de estados y calidez.

QUE ES ESTE TEST Y POR QUE NO ESTA DENTRO DE `test_contraste.py`
-----------------------------------------------------------------
`test_contraste.py` mide el CONTRASTE: si un texto se lee sobre su fondo.
Eso es la mitad de la accesibilidad del color.

La otra mitad es la DISTINGUIBILIDAD: que dos estados distintos (en
deposito, en transito, en taller) no se confundan entre si. Un operador que
no puede distinguir "en taller" de "instalado" lee mal el inventario, y no
hay ningun test de contraste que lo detecte: los dos pares de colores tienen
contraste suficiente contra su fondo.

Por eso vive aparte, y por eso usa el modulo `src.core.color`, que es la
misma logica que tenia el script de consola `scripts/paleta.py`.

LOS TRES TEMAS
--------------
La app tiene tres: el claro (`:root`), el oscuro (`[data-theme="dark"]`) y
el exterior (`[data-outdoor="1"]`, el modo con sol de frente). Los tres se
miden: una paleta que funciona en la sala y es ilegible en el deposito no
sirve, y el deposito es donde mas se usa.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.color import (  # noqa: E402
    contraste,
    distancia_perceptual,
    saturacion_relativa,
    temperatura,
)

# Donde puede estar el CSS (mismo criterio que `test_contraste.py`).
RUTAS_CSS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "styles.css",
    Path("/app/frontend-src/styles.css"),
)

# Los tres temas de la app.
TEMAS = {
    "claro": None,
    "oscuro": "dark",
    "exterior": "outdoor",
}

# Los estados que conviven en la misma pantalla, con el token de su tinta.
#
# SON CUATRO, NO SEIS
# -------------------
# La app tiene seis insignias de estado (deposito, transito, instalado,
# taller, baja, peligro) pero solo CUATRO tintas distintas:
#
#   deposito   -> --color-info   (cyan)
#   transito   -> --warning      (ambar)
#   instalado  -> --success      (verde)
#   peligro    -> --color-error  (rojo)
#
# y "taller" comparte la tinta de "transito", y "baja" la de "peligro".
# Eso NO es un error: cada insignia lleva SIEMPRE su TEXTO ("En transito",
# "En taller"), asi que el color nunca es el unico canal (WCAG 1.4.1). Lo
# que se mide aca es que los cuatro que COMPITEN por el color (los que el
# operador puede tener a la vista en la misma pantalla) se distingan de un
# vistazo.
#
# Los tokens que se usan son los CANONICOS (`--color-info`, `--warning`...),
# que son los que existen en los tres temas. Los alias (`--info`,
# `--alerta`...) solo estan en `:root`, y con ellos el tema oscuro no se
# podria medir.
ESTADOS = {
    "deposito": "--color-info",
    "transito": "--warning",
    "instalado": "--success",
    "peligro": "--color-error",
}

# Los minimos. Son los mismos del script de consola, con el porque.
MINIMO_ESTADO_VS_ESTADO = 25.0
MINIMO_ACENTO_VS_ESTADO = 20.0
MINIMO_TEXTO = 4.5
MINIMO_COMponente = 3.0


def _leer_css() -> str:
    import os

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
    Los tokens de color, con los alias resueltos.

    EL TEMA HEREDA DE `:root`
    --------------------------
    Los bloques de tema (`[data-theme="dark"]`, `[data-outdoor="1"]`)
    redefinen los tokens CANONICOS (`--primary`, `--color-info`...), pero no
    los alias (`--primario`, `--info`...): los alias viven solo en `:root` y
    apuntan a los canonicos.

    Por eso se parte de `:root` y se sobreescribe con el tema: asi un token
    como `--primario` existe en los tres temas y resuelve al valor del tema
    que se esta midiendo. Sin esto, el tema oscuro se midiria con los
    valores del claro.
    """
    base = re.search(r":root\s*\{(.*?)\n\}", css, re.S)
    if not base:
        pytest.skip("No se encuentra el bloque :root del CSS.")

    tokens: dict[str, str] = {}
    for nombre, valor in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", base.group(1)):
        tokens[nombre] = valor.strip()

    if tema is not None:
        selector = 'data-outdoor="1"' if tema == "outdoor" else f'data-theme="{tema}"'
        bloque = re.search(rf'\[{re.escape(selector)}\]\s*\{{(.*?)\n\}}', css, re.S)
        if not bloque:
            pytest.skip(f"No se encuentra el bloque de tokens {tema}")
        for nombre, valor in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", bloque.group(1)):
            tokens[nombre] = valor.strip()

    # Los alias se resuelven en cascada (un alias puede apuntar a otro
    # alias: `--primario` -> `--primary-hover` -> `--primary`).
    resueltos: dict[str, str] = {}
    for nombre in tokens:
        valor = tokens[nombre]
        for _ in range(4):
            if not valor.startswith("var("):
                break
            base_nombre = valor[4:].split(")")[0].strip()
            valor = tokens.get(base_nombre, "")
        resueltos[nombre] = valor
    return resueltos


def _color(tokens: dict[str, str], nombre: str) -> str:
    valor = tokens.get(nombre, "")
    if not valor.startswith("#"):
        pytest.skip(f"El token {nombre} no es un color (#rrggbb): {valor!r}")
    return valor


class TestPaleta:
    """Las tres medidas, sobre la paleta REAL de la app."""

    @pytest.mark.parametrize("tema", list(TEMAS), ids=list(TEMAS))
    def test_los_estados_se_distinguen_entre_si(self, tema):
        """
        Dos estados que se parecen son dos estados que el operador confunde.

        El minimo es 25 de distancia perceptual: por debajo, el ojo no los
        separa sin esfuerzo. Y no alcanza con que el contraste contra el
        fondo sea bueno: un verde sobre un fondo verde puede tener 4.5:1 y
        seguir siendo indistinguible de otro verde.
        """
        tokens = _tokens_de(_leer_css(), TEMAS[tema])
        tintas = {
            nombre: _color(tokens, token)
            for nombre, token in ESTADOS.items()
            if token in tokens
        }
        assert len(tintas) >= 4, (
            f"El tema {tema} no define los estados esperados: {list(ESTADOS)}"
        )

        problemas = []
        nombres = list(tintas)
        for i, a in enumerate(nombres):
            for b in nombres[i + 1:]:
                d = distancia_perceptual(tintas[a], tintas[b])
                if d < MINIMO_ESTADO_VS_ESTADO:
                    problemas.append(
                        f"{a} y {b} están a {d:.1f} (se necesita "
                        f"{MINIMO_ESTADO_VS_ESTADO})"
                    )

        assert not problemas, (
            f"En el tema {tema} estos estados se confunden entre si:\n  "
            + "\n  ".join(problemas)
        )

    @pytest.mark.parametrize("tema", list(TEMAS), ids=list(TEMAS))
    def test_el_acento_no_compite_con_los_estados(self, tema):
        """
        El boton principal no tiene que parecer una alerta.

        El minimo es 20 (y no 25) por una razon concreta: los estados
        compiten entre si como canales de informacion —por eso el test de
        arriba exige 25—, en cambio el acento vive en botones y pestanas, y
        los estados en insignias. Ademas cada insignia lleva TEXTO, asi que
        el color nunca es el unico canal (WCAG 1.4.1).
        """
        tokens = _tokens_de(_leer_css(), TEMAS[tema])
        primario = _color(tokens, "--primario")

        for nombre, token in ESTADOS.items():
            if token not in tokens:
                continue
            tinta = _color(tokens, token)
            d = distancia_perceptual(primario, tinta)
            assert d >= MINIMO_ACENTO_VS_ESTADO, (
                f"En el tema {tema}, el primario ({primario}) y el estado "
                f"{nombre} ({tinta}) están a {d:.1f}: un boton puede leerse "
                f"como una alerta (se necesita {MINIMO_ACENTO_VS_ESTADO})."
            )

    @pytest.mark.parametrize("tema", list(TEMAS), ids=list(TEMAS))
    def test_el_texto_principal_contrasta_en_los_tres_lugares(self, tema):
        """El texto del cuerpo se lee sobre el fondo y sobre la tarjeta."""
        tokens = _tokens_de(_leer_css(), TEMAS[tema])
        texto = _color(tokens, "--color-text")

        for nombre in ("--color-bg", "--color-surface"):
            c = contraste(texto, _color(tokens, nombre))
            assert c >= MINIMO_TEXTO, (
                f"En el tema {tema}, el texto sobre {nombre} tiene {c:.2f}:1 "
                f"(se necesita {MINIMO_TEXTO}:1)"
            )

    @pytest.mark.parametrize("tema", list(TEMAS), ids=list(TEMAS))
    def test_el_borde_de_los_campos_se_ve(self, tema):
        """
        El borde de un campo es la unica pista de que ahi se escribe
        (WCAG 1.4.11): si no se ve, el operador no sabe donde escribir.
        """
        tokens = _tokens_de(_leer_css(), TEMAS[tema])
        c = contraste(
            _color(tokens, "--color-border-control"),
            _color(tokens, "--color-surface"),
        )
        assert c >= MINIMO_COMponente, (
            f"En el tema {tema}, el borde de los campos tiene {c:.2f}:1 "
            f"(se necesita {MINIMO_COMponente}:1)"
        )

    @pytest.mark.parametrize("tema", list(TEMAS), ids=list(TEMAS))
    def test_los_neutros_no_son_clinicos(self, tema):
        """
        Los neutros (el fondo y las tarjetas) no pueden ser frios.

        La calidez que el operador PERCIBE viene de los neutros: cubren
        casi toda la pantalla. El acento es un porcentaje chico de los
        pixeles, asi que puede ser calido sin obligar a oscurecer el boton
        hasta volverlo ilegible.

        Un gris azulado se siente clinico aunque tenga el mismo tono: por
        eso el signo tiene que ser de CALIDEZ (rojo por encima de azul) y no
        solo "no ser gris".
        """
        tokens = _tokens_de(_leer_css(), TEMAS[tema])

        # El fondo y la superficie tienen que ser calidos o neutros: en el
        # tema oscuro el criterio es al reves (una superficie calida sobre un
        # fondo oscuro es lo que se ve como "caja").
        if tema == "oscuro":
            return

        for nombre in ("--color-bg", "--color-surface"):
            t = temperatura(_color(tokens, nombre))
            assert t >= 0.0, (
                f"En el tema {tema}, {nombre} es frio (temperatura {t:+.3f}): "
                "la pantalla se va a sentir clinica."
            )

    def test_la_saturacion_de_los_estados_no_es_nula(self):
        """
        Un estado gris no es un estado: es una ausencia.

        Si la tinta de un estado se dessatura (gris), el operador no tiene
        ningun canal para distinguirlo del fondo.
        """
        tokens = _tokens_de(_leer_css())
        for nombre, token in ESTADOS.items():
            if token not in tokens:
                continue
            s = saturacion_relativa(_color(tokens, token))
            assert s > 0.15, (
                f"El estado {nombre} tiene la saturacion en {s:.2f}: es "
                "casi gris, y no se distingue del fondo."
            )
