"""
Middleware de seguridad y observabilidad para la API de Smart Inventory.

Capa transversal: se aplica a TODAS las rutas, incluyendo las que no requieren
auth, porque el objetivo es defender el perímetro antes de que llegue al router.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import get_settings

# ---------------------------------------------------------------------------
# Headers de seguridad (OWASP Secure Headers)
# ---------------------------------------------------------------------------
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        # HSTS solo si viene por HTTPS (evita romper dev local)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


# ---------------------------------------------------------------------------
# Rate limiting por IP (token bucket en memoria, por proceso)
# ---------------------------------------------------------------------------
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Limite simple por IP. No distribuido: para producción con varias réplicas
    hay que pasar a Redis. Suficiente para una sola instancia uvicorn.
    """

    def __init__(self, app, requests_per_minute: int = 120, burst: int = 30):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.burst = burst
        self._buckets: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Endpoints de health no se limitan (probes del orquestador)
        if request.url.path in ("/health", "/", "/docs", "/openapi.json"):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = 60.0
        bucket = self._buckets[ip]

        # limpiar ventana
        while bucket and bucket[0] < now - window:
            bucket.popleft()

        if len(bucket) >= self.rpm:
            retry = int(window - (now - bucket[0])) + 1
            return Response(
                content='{"detail":"Demasiadas solicitudes. Intentá más tarde."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry)},
            )

        bucket.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rpm)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.rpm - len(bucket)))
        return response


# ---------------------------------------------------------------------------
# Request ID + logging mínimo
# ---------------------------------------------------------------------------
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def configurar_cors(app) -> None:
    settings = get_settings()
    # En producción restringir orígenes; en dev permite localhost
    if settings.app_env.lower() in ("production", "prod"):
        origins = ["https://smart-inventory.local"]
    else:
        origins = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:5174",
            "http://localhost:5175",
            "http://localhost:8080",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )
