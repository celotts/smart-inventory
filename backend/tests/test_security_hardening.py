import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "x" * 48)
os.environ.setdefault("APP_ENV", "development")

from src.core.middleware import RateLimitMiddleware
from src.main import app

client = TestClient(app)


def test_security_headers_present():
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "X-Request-ID" in r.headers


def test_request_id_propagated():
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers.get("X-Request-ID") == "abc-123"


def test_rate_limit_returns_429():
    """
    El rate limit tiene que cortar, no avisar.

    EL LIMITE LO MANDA LA CONFIGURACION
    -----------------------------------
    El test pedia 130 peticiones y esperaba un 429, pero el limite paso de
    120 a 300 peticiones por minuto: con 130 pedidos nunca se llegaba al
    corte, y el test fallaba sin que hubiera ningun bug de produccion.

    Por eso el limite lo pone el TEST (una app aparte con un limite bajo) y
    no la configuracion: asi el test no depende del valor de `RATE_LIMIT_RPM`
    y, sobre todo, no deja el bucket lleno para los tests que vienen
    despues (que comparten la misma IP).
    """
    app_de_prueba = FastAPI()
    app_de_prueba.add_middleware(RateLimitMiddleware, requests_per_minute=10)
    cliente = TestClient(app_de_prueba)

    estados = set()
    for _ in range(15):
        r = cliente.get("/api/users")
        estados.add(r.status_code)

    assert 429 in estados


def test_cors_headers_dev():
    r = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204, 405)
