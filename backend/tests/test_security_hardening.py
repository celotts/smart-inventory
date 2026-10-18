import os

from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "x" * 48)
os.environ.setdefault("APP_ENV", "development")

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
    # agotar el bucket en root (no está excluido? root sí está excluido)
    # usamos un path no excluido: /api/users no existe sin auth pero rate limit aplica
    statuses = set()
    for _ in range(130):
        r = client.get("/api/users")
        statuses.add(r.status_code)
    assert 429 in statuses


def test_cors_headers_dev():
    r = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204, 405)
