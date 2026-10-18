"""
Tests de la geodesia del escaneo.

POR QUE HAY UN TEST POR CANTIDAD
--------------------------------
Porque la mayoria de los errores de un calculo de distancia no son errores de
formula: son errores de ENTRADA. Un `NaN` de un GPS roto, una latitud de 200
de un cliente mal configurado, un radio `NULL` de una sede creada antes de que
la columna existiera. Cada uno de esos casos tiene su test porque cada uno
produce un modo de fallo distinto.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.geo import (
    TIERRA_RADIO_METROS,
    CoordenadaInvalida,
    distancia_metros,
    dentro_del_radio,
    normalizar_coordenada,
)

# Buenos Aires. La referencia no es arbitraria: es la ciudad donde corre el
# sistema, asi que un error de hemisferio aca se ve antes que en otro lado.
BA_LAT, BA_LON = -34.6037, -58.3816


class TestNormalizarCoordenada:
    def test_coordenada_valida_pasa(self):
        assert normalizar_coordenada(BA_LAT, BA_LON) == (BA_LAT, BA_LON)

    def test_acepta_extremos_validos(self):
        # Los polos y el meridiano de Greenwich son puntos reales del planeta.
        assert normalizar_coordenada(90, 180) == (90.0, 180.0)
        assert normalizar_coordenada(-90, -180) == (-90.0, -180.0)

    def test_rechaza_latitud_fuera_de_rango(self):
        with pytest.raises(CoordenadaInvalida):
            normalizar_coordenada(91, 0)

    def test_rechaza_longitud_fuera_de_rango(self):
        # 200 no es "el otro lado del mundo": es una coordenada rota.
        with pytest.raises(CoordenadaInvalida):
            normalizar_coordenada(0, 200)

    def test_rechaza_nan(self):
        """
        NaN es el caso peligroso.

        `if lat < -90 or lat > 90` deja pasar NaN sin quejarse, porque toda
        comparacion con NaN es False. Y un NaN guardado en la columna se
        propaga a todos los calculos de distancia como si fuera cero.
        """
        with pytest.raises(CoordenadaInvalida):
            normalizar_coordenada(math.nan, 0)

    def test_rechaza_infinito(self):
        with pytest.raises(CoordenadaInvalida):
            normalizar_coordenada(0, math.inf)

    def test_rechaza_no_numerico(self):
        with pytest.raises(CoordenadaInvalida):
            normalizar_coordenada("treinta", 0)

    def test_acepta_string_numerico(self):
        # El GPS de un celular puede mandar " -34.60 " como texto.
        assert normalizar_coordenada("-34.6037", "-58.3816") == (BA_LAT, BA_LON)


class TestDistancia:
    def test_mismo_punto_cero(self):
        assert distancia_metros(BA_LAT, BA_LON, BA_LAT, BA_LON) == pytest.approx(0, abs=1e-6)

    def test_buenos_aires_a_rosario_aproximado(self):
        """
        ~278 km por el rio. Dos ciudades del mismo pais a unas 300 km.

        El numero de referencia es el geodesic real, no uno inventado: lo que
        importa es que un error de metros no se convierta en uno de kilometros.
        """
        metros = distancia_metros(BA_LAT, BA_LON, -32.9442, -60.6505)
        assert 260_000 < metros < 300_000

    def test_un_grado_de_latitud_es_about_111km(self):
        """Verifica la escala contra un hecho conocido del geodesia."""
        metros = distancia_metros(0, 0, 1, 0)
        assert metros == pytest.approx(111_195, rel=0.01)

    def test_este_oeste_cerca_del_ecuador_es_mayor_que_norte_sur(self):
        """
        Este es el error que hace la version "plana" del calculo.

        A un grado del ecuador, un grado de longitud son 111 km y uno de
        latitud tambien. A 60 grados de latitud, un grado de longitud son solo
        55 km. Una distancia euclidiana trataria los dos ejes como iguales y
        se equivocaria en un factor de dos, que para un radio de 50 m de
       Tolerance significa decidir al azar.
        """
        cerca_ecuador = distancia_metros(0, 0, 0, 1)
        cerca_polo = distancia_metros(60, 0, 60, 1)

        assert cerca_ecuador > cerca_polo * 1.8

    def test_simetria(self):
        a = distancia_metros(BA_LAT, BA_LON, -34.5, -58.2)
        b = distancia_metros(-34.5, -58.2, BA_LAT, BA_LON)
        assert a == pytest.approx(b, rel=1e-9)

    def test_no_revienta_con_extremos(self):
        """
        `sin^2` puede devolver 1.0000000000000002 por precision de punto
        flotante, y `sqrt(1.0000000000000002)` es NaN. Aparece justo en el
        borde (dos puntos casi opuestos), que es el caso que uno testeando
        encuentra ultimo.
        """
        metros = distancia_metros(0, 0, 0, 180)
        assert math.isfinite(metros)
        assert metros == pytest.approx(math.pi * TIERRA_RADIO_METROS, rel=1e-6)


class TestDentroDelRadio:
    def test_dentro_si_esta_en_el_centro(self):
        dentro, metros = dentro_del_radio(BA_LAT, BA_LON, BA_LAT, BA_LON, 50)
        assert dentro is True
        assert metros == pytest.approx(0, abs=0.5)

    def test_fuera_si_supera_el_radio(self):
        # ~1 km al norte.
        dentro, metros = dentro_del_radio(-34.5937, BA_LON, BA_LAT, BA_LON, 50)
        assert dentro is False
        assert metros > 900

    def test_limite_exacto_es_dentro(self):
        """
        El borde cuenta como dentro.

        No es matematica fina: es que un operador parado en el porton del
        deposito, con el GPS con 5 metros de error, tiene que poder escanear. Si
        el borde fuera excluyente, el mismo operador fallaria segun el dia.
        """
        # Un grado de latitud son ~111.195 m, asi que 50 m son 0.00045 grados.
        # Se calcula en vez de escribirlo a mano para que el test siga siendo
        # correcto si alguien cambia el radio terrestre.
        medio_grado = 50 / 111_195.0

        dentro, metros = dentro_del_radio(
            BA_LAT + medio_grado, BA_LON, BA_LAT, BA_LON, 50
        )
        assert dentro is True
        assert metros == pytest.approx(50.0, abs=0.5)

    def test_un_metro_mas_alla_del_borde_queda_afuera(self):
        """El borde exacto entra, un metro mas no: el radio no es un conozco."""
        medio_grado = 50 / 111_195.0
        fuera = medio_grado * 1.05  # ~52.5 m

        dentro, metros = dentro_del_radio(BA_LAT + fuera, BA_LON, BA_LAT, BA_LON, 50)
        assert dentro is False
        assert metros == pytest.approx(52.5, abs=0.5)

    def test_radio_none_significa_sin_limite(self):
        """
        Una sede creada antes de que existiera `tolerance_radius_meters` tiene
        NULL. NULL significaba "sin limite", y por eso se traduce a infinito.

        Con 0 en su lugar, ninguna sede vieja aceptaria un escaneo, y el
        operador recibiria "fuera de radio" en un deposito donde esta parado.
        """
        dentro, metros = dentro_del_radio(-34.5937, BA_LON, BA_LAT, BA_LON, None)
        assert dentro is True
        assert metros > 0

    def test_radio_cero_solo_acepta_el_centro_exacto(self):
        dentro, _ = dentro_del_radio(BA_LAT, BA_LON, BA_LAT, BA_LON, 0)
        assert dentro is True

    def test_devuelve_la_distancia_para_poder_explicar(self):
        """
        El operador necesita saber CUANTO le falta, no solo que fallo.

        "a 340 m, probably otra sede" es accionable; "escaneo invalido" no.
        """
        _, metros = dentro_del_radio(-34.5937, BA_LON, BA_LAT, BA_LON, 50)
        assert 900 < metros < 1200
        assert metros == round(metros, 1)
