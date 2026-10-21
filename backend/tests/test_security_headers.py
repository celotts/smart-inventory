"""
Tests de los headers de seguridad y del rate limit.

POR QUE ESTOS TESTS IMPORTAN Más DE LO QUE PARECEN
---------------------------------------------------
La API manda `Permissions-Policy: camera=(), geolocation=()`. Ese header
IMPEDIA USAR EL ESCANER: el navegador aplica la politica de permisos a todo el
origen, no a una página, asi que el `getUserMedia()` del lector de QR fallaba
con NotAllowedError y el escaneo no existia. La politica era "correcta para una
API sin camara" y por eso rompio el producto.

Un test que verifica que los headers Estan no alcanza: habria pasado igual con
`camera=()`. Lo que hay que fijar es QUE DICEN, porque el valor es lo que
importa.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SECRET_KEY", "x" * 48)
os.environ.setdefault("APP_ENV", "development")

from fastapi.testclient import TestClient  # noqa: E402

from src.core.middleware import (  # noqa: E402
    CSP_API,
    ORIGENES_DE_DESARROLLO,
    RateLimitMiddleware,
    _parse_redes_confiadas,
    _validar_ip,
    ip_de_cliente,
)
from src.main import app  # noqa: E402

client = TestClient(app)


class TestPermissionsPolicy:
    def test_permite_camara_para_el_escaner(self):
        """
        EL REGRESO DE ESTE TEST.

        Si la política vuelve a decir `camera=()`, el lector de QR deja de
        funcionar en TODOS los navegadores, sin ningún error visible en la API:
        el `getUserMedia()` falla en el navegador y el frontend muestra un error
        que no tiene nada que ver con el motivo.
        """
        r = client.get("/health")
        politica = r.headers.get("Permissions-Policy", "")

        assert "camera=(self)" in politica, (
            "La politica de permisos tiene que habilitar la camara para el "
            f"escaneo. Valor actual: {politica!r}"
        )

    def test_permite_geolocalizacion_para_el_geo_scaneo(self):
        """
        La geo-validación del escaneo necesita el GPS del dispositivo.

        Sin esto, `navigator.geolocation` falla y todos los escaneos quedan con
        `is_valid_location = NULL`: el sistema funciona pero pierde la mitad de
        la senal que justificaba tener coordenadas en la tabla.
        """
        r = client.get("/health")
        politica = r.headers.get("Permissions-Policy", "")

        assert "geolocation=(self)" in politica

    def test_sigue_negando_lo_que_no_se_usa(self):
        """
        Habilitar camara y GPS no habilita el microfono.

        La política tiene que seguir siendo una restriccion: si todo dice
        `=(self)`, no es una política.
        """
        r = client.get("/health")
        politica = r.headers.get("Permissions-Policy", "")

        for negado in ("microphone=()", "payment=()", "usb=()"):
            assert negado in politica


class TestCSP:
    def test_no_hay_scripts_en_la_api(self):
        """
        `script-src 'none'`: si una inyeccion de HTML se colara en una
        respuesta de la API, no se ejecutaria.

        La API devuelve JSON, así que no necesita ejecutar nada. Es la capa que
        hace que un XSS en el FRONT no escale a las respuestas de la API.
        """
        r = client.get("/health")
        csp = r.headers.get("Content-Security-Policy", "")

        assert "script-src 'none'" in csp

    def test_la_api_no_puede_ser_llamada_desde_otro_origen(self):
        assert "connect-src 'self'" in csp_de(client)

    def test_no_hay_framing(self):
        csp = csp_de(client)
        assert "frame-ancestors 'none'" in csp
        assert "frame-ancestors" in CSP_API


def csp_de(c: TestClient) -> str:
    return c.get("/health").headers.get("Content-Security-Policy", "")


class TestHeadersExistentes:
    def test_nosniff(self):
        assert client.get("/health").headers.get("X-Content-Type-Options") == "nosniff"

    def test_no_framing_legacy(self):
        assert client.get("/health").headers.get("X-Frame-Options") == "DENY"

    def test_no_se_cachean_respuestas_de_api(self):
        """
        `no-store` en las respuestas de API.

        Una respuesta de la API cacheada en un proxy compartido puede leaky
        hacia otro usuario. El access token va en el header, pero la respuesta
        trae datos de inventario que no.
        """
        assert "no-store" in client.get("/health").headers.get("Cache-Control", "")

    def test_x_xss_protection_no_se_manda(self):
        """
        La cabecera se ELIMINO, y que siga sin mandandose es una decisión.

        Los navegadores que la soportan la ignoran; en los que no, activa un
        filtro que puede mangar páginas legitimas. La defensa real es la CSP.
        """
        assert client.get("/health").headers.get("X-XSS-Protection") is None


class TestIPConfiable:
    def test_ignora_x_forwarded_for_sin_proxy_configurado(self):
        """
        SIN proxy de confianza, el header se ignora SIEMPRE.

        `X-Forwarded-For` lo pone cualquiera. Si una API lo creyera para
        decidir a quien limitar, un atacante manda uno distinto por request y el
        límite por IP no limita nada.
        """
        from starlette.datastructures import Headers
        from starlette.requests import Request

        # `request.app` se lee de `scope["app"]`: no tiene setter, así que la
        # app se inyecta por el scope y no asignando el atributo.
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": Headers({"x-forwarded-for": "1.2.3.4"}).raw,
            "client": ("9.9.9.9", 1234),
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "app": type("A", (), {"state": type("S", (), {"redes_confiadas": []})()})(),
        }
        request = Request(scope)

        assert ip_de_cliente(request) == "9.9.9.9"

    def test_acepta_header_de_un_proxy_de_confianza(self):
        """Con el peer en una red de confianza, el header es creible."""
        from starlette.datastructures import Headers
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": Headers({"x-real-ip": "1.2.3.4"}).raw,
            "client": ("10.0.0.5", 1234),
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "app": type(
                "A",
                (),
                {"state": type("S", (), {"redes_confiadas": _parse_redes_confiadas(["10.0.0.0/8"])})()},
            )(),
        }
        request = Request(scope)

        assert ip_de_cliente(request) == "1.2.3.4"

    def test_red_mal_escrita_se_ignora_en_vez_de_reventar(self):
        """
        Una red inválida se descarta con un warning, no lanza.

        El default (no confiar en nadie) es el seguro, así que un typo en la
        config deja la puerta CERRADA en vez de abierta.
        """
        assert _parse_redes_confiadas(["no-es-una-red", "10.0.0.0/8"]) != []
        assert _parse_redes_confiadas(["no-es-una-red"]) == []

    def test_ip_invalida_se_descarta(self):
        assert _validar_ip("no-es-una-ip") is None
        assert _validar_ip("8.8.8.8") == "8.8.8.8"


class TestRateLimit:
    def test_429_con_retry_after(self):
        """
        Agota el límite y verifica el header `Retry-After`.

        Sin `Retry-After`, el cliente reintenta a ciegas y el problema se
        agrava; con el, sabe cuando volver.
        """
        c = TestClient(app)
        codigos = []
        for _ in range(200):
            r = c.get("/api/devices")
            codigos.append(r.status_code)
            if r.status_code == 429:
                assert int(r.headers["Retry-After"]) >= 1
                break

        assert 429 in codigos

    def test_health_no_se_limita(self):
        """
        El healthcheck no cuenta.

        Si se limitara, un arranque lento consume el límite del proceso, el
        orquestador ve un 429, marca unhealthy y reinicia: un reinicio en loop
        causado por el propio mecanismo de salud.
        """
        c = TestClient(app)
        for _ in range(50):
            codigo = c.get("/health").status_code
            # 503 significa "la base no está disponible" (que es lo que pasa
            # cuando los tests corren en el host, sin base). Lo que NO puede
            # ser es 429: el healthcheck no se rate-limitea.
            assert codigo in (200, 503), f"/health devolvio {codigo}"

    def test_el_bucket_esta_acotado(self):
        """
        EL QUE EVITA EL OOM.

        El `defaultdict` original guardaba una IP para siempre: con IPs
        rotativas eso es memoria infinita y el contenedor muere sin dejar
        rastro. El `_buckets` es un LRU con tope, así que 20.000 IPs distintas
        siguen ocupando 10.000 entradas.
        """
        rl = RateLimitMiddleware(app)
        ahora = 1000.0

        for i in range(20_000):
            rl._consumir(f"10.0.{(i // 256) % 256}.{i % 256}", ahora)

        assert len(rl._buckets) <= rl._max_ips

    def test_ips_que_siguen_pediendo_no_se_expulsan(self):
        """
        El LRU expulsa la que hace más que no pide.

        En el caso de un ataque de IPs rotativas eso es lo correcto: las viejas
        ya no atacan.
        """
        rl = RateLimitMiddleware(app)

        rl._consumir("1.1.1.1", 1000.0)
        rl._consumir("2.2.2.2", 1000.0)
        # 1.1.1.1 vuelve a pedir más tarde: pasa al final del OrderedDict.
        rl._consumir("1.1.1.1", 1001.0)

        assert list(rl._buckets.keys())[-1] == "1.1.1.1"


class TestCORS:
    def test_desarrollo_permite_vite(self):
        """En desarrollo, sin configurar, se permiten los puertos de vite."""
        r = client.options(
            "/health",
            headers={
                "Origin": ORIGENES_DE_DESARROLLO[0],
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code in (200, 204)

    def test_origen_desconocido_no_recibe_permiso(self):
        """
        Un origen que no está en la lista NO recibe
        `Access-Control-Allow-Origin`.

        Es la garantía de que una página cualquiera no puede leer las respuestas
        de la API desde el navegador, por más que tenga un token.
        """
        r = client.options(
            "/health",
            headers={
                "Origin": "https://sitio-malicioso.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "Access-Control-Allow-Origin" not in r.headers
