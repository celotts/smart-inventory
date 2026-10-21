"""
Tests de la superficie de la API.

POR QUE ESTE ARCHIVO
-------------------
`/docs` no es un endpoint de negocio: es Swagger UI, una página que lista toda
la superficie de la API con ejemplos ejecutables y arrastra el CDN de
fastapi.tiangolo.com.

Estos tests fijan que en producción no exista. No es que sea "malo tener
documentacion": es que publicarla en producción le entrega a cualquiera que
llegue a la API un mapa completo de endpoints con ejemplos, más una dependencia
de un tercero del que la página no se puede separar.

En desarrollo tiene que seguir funcionando: es la herramienta con la que se
trabaja a diario.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _cliente(app_env: str):
    """
    Levanta la app con un entorno limpio.

    Se recarga el módulo `src.main` porque `_ES_Producción` se lee al importar:
    cambiar la variable después no alcanzaria, y el test pasaria siempre en
    desarrollo sin probar nunca producción.
    """
    os.environ["APP_ENV"] = app_env
    os.environ.setdefault("SECRET_KEY", "clave-de-prueba-suficientemente-larga-1234567890")

    import src.main

    importlib.reload(src.main)
    from fastapi.testclient import TestClient

    return TestClient(src.main.app)


@pytest.fixture
def produccion():
    return _cliente("production")


@pytest.fixture
def desarrollo():
    return _cliente("development")


class TestDocumentacionEnProduccion:
    @pytest.mark.parametrize("ruta", ["/docs", "/redoc", "/openapi.json"])
    def test_no_existe_en_produccion(self, produccion, ruta):
        assert produccion.get(ruta).status_code == 404, (
            f"{ruta} responde en produccion. Swagger UI publica la superficie "
            "completa de la API y depende de un CDN de terceros."
        )

    def test_el_resto_sigue_andando(self, produccion):
        """
        Desactivar la documentacion no puede romper nada más.

        El 503 de `/health` significa "la base no está disponible": es lo que
        pasa cuando los tests corren en el host, sin base. Lo que NO puede ser
        es un 404 (la documentacion desactivada se llevo el endpoint de salud
        con ella) ni un 500.
        """
        assert produccion.get("/health").status_code in (200, 503)

    def test_los_endpoints_siguen_protegidos(self, produccion):
        """Que /docs disappears no puede haber abierto nada."""
        assert produccion.get("/api/devices").status_code == 401


class TestDocumentacionEnDesarrollo:
    @pytest.mark.parametrize("ruta", ["/docs", "/openapi.json"])
    def test_existe_en_desarrollo(self, desarrollo, ruta):
        assert desarrollo.get(ruta).status_code == 200, (
            f"{ruta} deberia estar disponible en desarrollo: es la herramienta "
            "con la que se integra."
        )
