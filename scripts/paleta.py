#!/usr/bin/env python3
"""
El diagnóstico de la paleta, en la consola.

QUE HACE
--------
Muestra, tema por tema, las tres medidas que definen si una paleta funciona:

  1. CONTRASTE: si un texto se lee sobre su fondo (WCAG 2.1).
  2. DISTINGUIBILIDAD: si dos estados se confunden entre si (Lab).
  3. CALIDEZ: si la pantalla se siente calida o clinica.

POR QUE UN SCRIPT Y NO SOLO UN TEST
----------------------------------
El test (`backend/tests/test_paleta.py`) es el que IMPONE: falla si una
paleta se rompe. Este script es el que EXPLICA: cuando algo falla, muestra
el número y por que se pide ese número, que es la parte que un test no
puede dar.

Los dos usan la misma logica (`backend/src/core/color.py`), asi que el
script no puede mentir: si el script dice que algo esta bien, el test pasa.

QUE MIDE: LA PALETA REAL
------------------------
Mide los tokens del `frontend/src/styles.css`, los mismos que la pantalla
usa. No mide paletas de muestra: una paleta que no esta en el CSS no le
importa a nadie.

    python3 scripts/paleta.py          # el diagnóstico
    python3 scripts/paleta.py --json   # en JSON, para otra herramienta
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from src.core.color import (  # noqa: E402
    contraste,
    distancia_perceptual,
    saturacion_relativa,
    temperatura,
)

CSS = RAIZ / "frontend" / "src" / "styles.css"

# Los umbrales. Son los mismos que usa el test.
MINIMO_TEXTO = 4.5
MINIMO_COMPONENTE = 3.0
MINIMO_ESTADO_VS_ESTADO = 25.0
MINIMO_ACENTO_VS_ESTADO = 20.0

# El tema: el nombre con el que lo muestra el script, y el selector del CSS.
TEMAS = {
    "claro": None,
    "oscuro": '[data-theme="dark"]',
    "exterior": '[data-outdoor="1"]',
}

# Los estados con tinta PROPIA (los que compiten por el color).
ESTADOS = {
    "deposito": "--color-info",
    "transito": "--warning",
    "instalado": "--success",
    "peligro": "--color-error",
}

# Los pares de texto que tienen que leerse.
TEXTOS = [
    ("texto principal / fondo", "--color-text", "--color-bg", MINIMO_TEXTO),
    ("texto principal / superficie", "--color-text", "--color-surface", MINIMO_TEXTO),
    ("texto suave / superficie", "--color-text-secondary", "--color-surface", MINIMO_TEXTO),
    ("texto de etiqueta / superficie", "--color-label", "--color-surface", MINIMO_TEXTO),
    ("texto de boton / primario", "--btn-primary-text", "--primary", MINIMO_TEXTO),
]

# Los componentes: no son texto, asi que el minimo es 3:1.
COMPONENTES = [
    ("borde de campo / superficie", "--color-border-control", "--color-surface", MINIMO_COMPONENTE),
    ("primario / superficie", "--primary", "--color-surface", MINIMO_COMPONENTE),
]


def _leer_css() -> str:
    if not CSS.exists():
        raise SystemExit(f"No se encuentra {CSS}")
    return CSS.read_text(encoding="utf-8")


def _tokens(css: str, tema: str | None) -> dict[str, str]:
    """
    Los tokens del tema, con los alias resueltos.

    El tema hereda de `:root`: los alias (`--primario`) viven ahi y los
    canonicos (`--primary`) los redefine el tema.
    """
    base = re.search(r":root\s*\{(.*?)\n\}", css, re.S)
    if not base:
        raise SystemExit("No se encuentra el bloque :root del CSS.")

    tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", base.group(1)))

    if tema is not None:
        selector = TEMAS[tema]
        # El tema claro no tiene bloque propio: es el `:root`.
        if selector:
            bloque = re.search(rf"\[{re.escape(selector)}\]\s*\{{(.*?)\n\}}", css, re.S)
            if bloque:
                tokens.update(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", bloque.group(1)))

    resueltos = {}
    for nombre, valor in tokens.items():
        for _ in range(4):
            if not valor.startswith("var("):
                break
            valor = tokens.get(valor[4:].split(")")[0].strip(), "")
        resueltos[nombre] = valor
    return resueltos


def _color(tokens: dict[str, str], nombre: str) -> str | None:
    valor = tokens.get(nombre, "")
    return valor if valor.startswith("#") else None


def auditar(css: str, tema: str) -> list[dict]:
    """Las mediciones de un tema. Cada una lleva si cumple."""
    tokens = _tokens(css, tema)
    mediciones: list[dict] = []

    def agregar(grupo: str, etiqueta: str, valor: float, minimo: float, detalle: str = ""):
        mediciones.append({
            "grupo": grupo,
            "medicion": etiqueta,
            "valor": round(valor, 2),
            "minimo": minimo,
            "cumple": valor >= minimo,
            "detalle": detalle,
        })

    for etiqueta, a, b, minimo in TEXTOS:
        ca, cb = _color(tokens, a), _color(tokens, b)
        if ca and cb:
            agregar("texto", etiqueta, contraste(ca, cb), minimo, f"{ca} sobre {cb}")

    for etiqueta, a, b, minimo in COMPONENTES:
        ca, cb = _color(tokens, a), _color(tokens, b)
        if ca and cb:
            agregar("componentes", etiqueta, contraste(ca, cb), minimo, f"{ca} sobre {cb}")

    tintas = {n: _color(tokens, t) for n, t in ESTADOS.items()}
    tintas = {n: c for n, c in tintas.items() if c}
    nombres = list(tintas)
    for i, a in enumerate(nombres):
        for b in nombres[i + 1:]:
            agregar(
                "estados",
                f"{a} vs {b}",
                distancia_perceptual(tintas[a], tintas[b]),
                MINIMO_ESTADO_VS_ESTADO,
                f"{tintas[a]} vs {tintas[b]}",
            )

    primario = _color(tokens, "--primary")
    if primario:
        for nombre, tinta in tintas.items():
            agregar(
                "acento",
                f"primario vs {nombre}",
                distancia_perceptual(primario, tinta),
                MINIMO_ACENTO_VS_ESTADO,
                f"{primario} vs {tinta}",
            )

    for nombre in ("--color-bg", "--color-surface"):
        color = _color(tokens, nombre)
        if color:
            agregar("calidez", f"temperatura de {nombre}", temperatura(color), 0.0, color)

    for nombre, tinta in tintas.items():
        agregar("saturacion", f"saturacion de {nombre}", saturacion_relativa(tinta), 0.15, tinta)

    return mediciones


def imprimir(mediciones: list[dict], titulo: str) -> int:
    fallos = 0
    print(f"\n{'=' * 76}\n{titulo}\n{'=' * 76}")
    grupo_actual = ""
    for m in mediciones:
        if m["grupo"] != grupo_actual:
            grupo_actual = m["grupo"]
            print(f"\n  {grupo_actual.upper()}")
        marca = "OK  " if m["cumple"] else "FALLA"
        if not m["cumple"]:
            fallos += 1
        print(
            f"    [{marca}] {m['medicion']:<38} {m['valor']:>8.2f}"
            f"   (min {m['minimo']})  {m['detalle']}"
        )
    print(f"\n  RESULTADO: {fallos} fallo(s)")
    return fallos


def main() -> int:
    css = _leer_css()
    if "--json" in sys.argv:
        salida = {
            tema: auditar(css, tema)
            for tema in TEMAS
        }
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        return 0

    total = 0
    for tema in TEMAS:
        total += imprimir(auditar(css, tema), f"PALETA {tema.upper()}")

    print(f"\n{'=' * 76}\nTOTAL DE FALLOS: {total}\n{'=' * 76}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
