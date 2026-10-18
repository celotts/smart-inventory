"""
Tests del formateo de tablas que ve el modelo.

POR QUE ESTOS CASOS EXISTEN
--------------------------
El formato de los datos no es un detalle de presentacion: decidia si el modelo
reportaba las cifras correctas o no. Con JSON de diccionarios, llama3.1:8b
reporto "no hay equipos en reparacion" habiendo uno. Estos tests fijan el
comportamiento del formateador, que es lo que se cambio para arreglarlo.

El modelo NO se prueba aca: un test que depende de un LLM es un test que falla
por la mitad de las veces y deja de頸 leerse. Eso lo cubre `make ai-test`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.ai.table_format import formatear_tabla


def test_agregado_usa_formato_vertical():
    """Una fila con pocas columnas: `nombre: valor`, etiqueta pegada al numero."""
    texto = formatear_tabla(
        ["equipos_activos", "en_reparacion"],
        [{"equipos_activos": 5, "en_reparacion": 1}],
    )
    assert "- equipos_activos: 5" in texto
    assert "- en_reparacion: 1" in texto


def test_valor_cero_no_se_confunde_con_nulo():
    """
    Un 0 y un NULL son cosas distintas. Un equipo que existe pero no esta en
    ningun estado es distinto de cero equipos, y el reporte tiene que poder
    decirlo.
    """
    texto = formatear_tabla(
        ["equipos", "sin_cliente"],
        [{"equipos": 0, "sin_cliente": None}],
    )
    assert "- equipos: 0" in texto
    assert "- sin_cliente: sin dato" in texto


def test_varias_filas_usan_tabla_horizontal():
    """Varias filas: la vertical repetiria los nombres y costaria un disparate."""
    texto = formatear_tabla(
        ["cliente", "equipos"],
        [
            {"cliente": "A", "equipos": 2},
            {"cliente": "B", "equipos": 1},
        ],
    )
    assert "| cliente | equipos |" in texto
    assert "| A | 2 |" in texto


def test_tabla_horizontal_avisa_si_trunca():
    filas = [{"cliente": str(i), "equipos": i} for i in range(80)]
    texto = formatear_tabla(["cliente", "equipos"], filas, max_filas=60)
    assert "se muestran 60 de 80" in texto


def test_sin_filas():
    texto = formatear_tabla(["a"], [])
    assert "no devolvio filas" in texto


def test_una_fila_con_muchas_columnas_usa_horizontal():
    """El corte es por columnas, no solo por filas: 13 columnas en una fila ya
    es mas legible horizontal."""
    columnas = [f"c{i}" for i in range(13)]
    fila = {c: i for i, c in enumerate(columnas)}
    texto = formatear_tabla(columnas, [fila])
    assert "| c0 | c1 |" in texto
