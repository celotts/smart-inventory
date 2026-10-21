"""
Tests de lo que está ronda agrego: jerarquía, atributos, busqueda de texto
completo, dashboard y exportacion.

POR QUE SON UNITARIOS Y NO DE Integración
-----------------------------------------
La mayoria de lo que se prueba acá es Lógica PURA: el armado del árbol, el
ruta de una categoría, la validación de un atributo, la tabla de amplitudes
del Code 128. Eso se prueba sin base de datos y sin red: un test que depende
de la base tarda y falla por reasons que no son del código.

Lo que SI necesita base (los endpoints) se prueba en `test_scan_e2e.py`, que
ya tiene el arnes de la base.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.arbol import anidar, rutas_por_padres
from src.modules.catalog import barcode


class TestArbol:
    """El armado de árboles: categorías y ubicaciones."""

    def test_ruta_de_una_categoria_sin_padre(self):
        filas = [{"id": 1, "name": "Notebook", "parent_id": None}]
        assert rutas_por_padres(filas) == {1: "Notebook"}

    def test_ruta_de_una_subcategoria(self):
        filas = [
            {"id": 1, "name": "Notebook", "parent_id": None},
            {"id": 2, "name": "Notebooks 14", "parent_id": 1},
        ]
        assert rutas_por_padres(filas)[2] == "Notebook > Notebooks 14"

    def test_ruta_de_tres_niveles(self):
        filas = [
            {"id": 1, "name": "Bodega", "parent_id": None},
            {"id": 2, "name": "Pasillo 3", "parent_id": 1},
            {"id": 3, "name": "Estante A", "parent_id": 2},
        ]
        assert rutas_por_padres(filas)[3] == "Bodega > Pasillo 3 > Estante A"

    def test_un_padre_inexistente_no_rompe(self):
        """
        Un padre que no está en la lista es un dato roto, no una excepción.

        Si la ruta no se pudiera armar, el nodo desapareceria de la pantalla: el
        operador no veria el producto y no sabria por que.
        """
        filas = [{"id": 5, "name": "Huerfano", "parent_id": 999}]
        assert rutas_por_padres(filas)[5] == "Huerfano"

    def test_una_categoria_no_puede_colgar_de_si_misma(self):
        filas = [{"id": 1, "name": "A", "parent_id": 1}]
        # Con un ciclo, el recorrido no termina: el límite de profundidad lo
        # corta, pero la ruta no es infinita.
        ruta = rutas_por_padres(filas)[1]
        assert isinstance(ruta, str)
        assert len(ruta) < 500

    def test_anidar_coloca_las_hijas_en_su_padre(self):
        arbol = anidar(
            [
                {"id": 1, "name": "Bodega", "parent_id": None},
                {"id": 2, "name": "Pasillo", "parent_id": 1},
            ],
            nombre="name",
        )
        assert len(arbol) == 1
        assert len(arbol[0]["hijas"]) == 1
        assert arbol[0]["hijas"][0]["name"] == "Pasillo"

    def test_anidar_deja_en_la_raiz_a_un_huerfano(self):
        arbol = anidar([{"id": 7, "name": "Huerfano", "parent_id": 99}], nombre="name")
        assert len(arbol) == 1
        assert arbol[0]["name"] == "Huerfano"


class TestCode128:
    """
    El código de barras de la etiqueta del producto.

    La tabla de amplitudes es la del estándar (ISO/IEC 15417). Estos tests
    fijan los valores de la tabla y el algoritmo de checksum, porque un error
    ahí genera un código que PARECE válido y no lo es: el lector no falla, no
    lee nada, y el operador cree que la etiqueta está rota.
    """

    def test_la_tabla_tiene_107_simbolos(self):
        # 0..102 datos, 103/104/105 arranques, 106 fin.
        assert len(barcode._AMPLITUDES) == 107

    @pytest.mark.parametrize(
        "valor, esperado",
        [
            (0, "212222"),    # espacio
            (16, "123122"),   # '0'
            (33, "111323"),   # 'A'
            (39, "211313"),   # 'G'
            (48, "313121"),   # 'P'
            (58, "312311"),   # 'Z'
            (103, "211412"),  # arranque A
            (104, "211214"),  # arranque B
            (106, "233111"),  # fin
        ],
    )
    def test_amplitudes_del_estandar(self, valor, esperado):
        assert barcode._AMPLITUDES[valor] == esperado

    def test_el_texto_se_codifica_en_el_set_b(self):
        # En el set B, el valor del símbolo es el ASCII menos 32.
        simbolos = barcode._simbolos("AB")
        assert simbolos[0] == 104  # arranque B
        assert simbolos[1] == 33   # 'A'
        assert simbolos[2] == 34   # 'B'

    def test_el_checksum_es_el_del_estandar(self):
        """
        El checksum del Code 128 es una suma ponderada módulo 103.

        Con los valores de la tabla se puede calcular a mano, que es lo que
        hace el test: si el algoritmo cambia, el checksum cambia y el lector
        rechaza el código.
        """
        simbolos = barcode._simbolos("DEMO-1")
        datos = simbolos[1:-1]
        esperado = (104 + sum(i * v for i, v in enumerate(datos, 1))) % 103
        assert simbolos[-1] == esperado

    def test_el_svg_dice_svg_y_no_falla(self):
        svg = barcode.codigo128_como_svg("PRD-123").decode("utf-8")
        assert svg.startswith("<svg")
        assert "</svg>" in svg

    def test_un_caracter_invalido_no_revienta(self):
        """
        Un caracter que el set B no codifica (un emoji, por ejemplo) tiene que
        devolver un cartel, no una excepción: un endpoint que rompe la etiqueta
        es peor que uno que muestra un mensaje.
        """
        svg = barcode.codigo128_como_svg("PEDIDO \U0001F600").decode("utf-8")
        assert "No se pudo generar" in svg


class TestValidacionDeAtributos:
    """
    La validación de los atributos contra la definición de la categoría.

    Estás funciones hablan con la base, así que lo que se prueba acá es la
    parte pura: los helpers de `core.árbol` y la forma del error. La
    validación completa se prueba contra la base en `test_scan_e2e.py`.
    """

    def test_la_clave_de_un_atributo_se_normaliza(self):
        """
        La clave es la que viaja en el JSONB de valores: tiene que ser
        estable, sin espacios y en minúscula.
        """
        from src.modules.catalog.service import _normalizar_clave_atributo

        assert _normalizar_clave_atributo("Fecha de Caducidad") == "fecha_de_caducidad"
        assert _normalizar_clave_atributo("  LOTE  ") == "lote"
