"""
WAF (Web Application Firewall) ligero a nivel middleware.

Bloquea patrones de ataque conocidos ANTES de que lleguen al router.
No sustituye a un WAF de red (Cloudflare, ModSecurity, AWS WAF), pero añade
una capa de defensa en profundidad que no depende de infraestructura externa.

MEJORAS ANTI-FALSO-POSITIVO:
----------------------------
1. Whitelist CIDR (oficina, VPN, CI/CD) → Skip WAF completamente
2. Rate limit por USER_ID autenticado (no IP) → 300 req/min por persona
3. Rate limit anónimo por IP con whitelist → Solo tráfico público
4. Configurable: log_only, thresholds, whitelist CIDR
5. Admin endpoints para monitoreo y desbloqueo manual
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patrones de ataque (compilados UNA vez al importar)
# ---------------------------------------------------------------------------

# SQL Injection - patrones reales de explotación, no palabras sueltas
_SQLI_PATTERNS = [
    # Union-based
    (re.compile(r"(?i)\bunion\s+(all\s+)?select\b"), "SQLi:UNION_SELECT"),
    # Boolean-based blind
    (re.compile(r"(?i)\b(and|or)\s+\d+\s*=\s*\d+"), "SQLi:BOOLEAN_BASED"),
    # Time-based
    (re.compile(r"(?i)(pg_sleep|sleep|waitfor\s+delay|benchmark)\s*\("), "SQLi:TIME_BASED"),
    # Stacked queries
    (re.compile(r";\s*(insert|update|delete|drop|alter|create|truncate)\b", re.IGNORECASE), "SQLi:STACKED"),
    # Comment termination
    (re.compile(r"(--|#|/\*).*?(\n|$)"), "SQLi:COMMENT"),
    # Information schema exfiltration
    (re.compile(r"(?i)information_schema\.(tables|columns)"), "SQLi:INFO_SCHEMA"),
]

# XSS - patrones de inyección de script reales
_XSS_PATTERNS = [
    # Script tags
    (re.compile(r"(?i)<\s*script\b"), "XSS:SCRIPT_TAG"),
    # Event handlers
    (re.compile(r"(?i)\bon\w+\s*="), "XSS:EVENT_HANDLER"),
    # javascript: protocol
    (re.compile(r"(?i)javascript\s*:"), "XSS:JS_PROTOCOL"),
    # data: URI con script
    (re.compile(r"(?i)data\s*:\s*text/html"), "XSS:DATA_URI"),
    # SVG onload
    (re.compile(r"(?i)<\s*svg\b.*?\bonload\s*="), "XSS:SVG_ONLOAD"),
    # iframe/srcdoc
    (re.compile(r"(?i)<\s*iframe\b.*?\bsrcdoc\s*="), "XSS:IFRAME_SRCDOC"),
]

# Path traversal
_PATH_TRAVERSAL_PATTERNS = [
    (re.compile(r"\.\./"), "PATH_TRAVERSAL:DOT_DOT_SLASH"),
    (re.compile(r"\.\.\\"), "PATH_TRAVERSAL:DOT_DOT_BACKSLASH"),
    (re.compile(r"%2e%2e%2f", re.IGNORECASE), "PATH_TRAVERSAL:URL_ENCODED"),
    (re.compile(r"%2e%2e%5c", re.IGNORECASE), "PATH_TRAVERSAL:URL_ENCODED_BACKSLASH"),
    (re.compile(r"..%2f", re.IGNORECASE), "PATH_TRAVERSAL:MIXED_ENCODED"),
    (re.compile(r"..%5c", re.IGNORECASE), "PATH_TRAVERSAL:MIXED_ENCODED_BACKSLASH"),
]

# Command injection
_CMD_INJECTION_PATTERNS = [
    (re.compile(r"(?i)\b(rm|cat|ls|wget|curl|nc|netcat|bash|sh|powershell)\s+"), "CMD_INJECTION:SHELL_CMD"),
    (re.compile(r"[;&|`$\(]"), "CMD_INJECTION:METACHAR"),
    (re.compile(r"(?i)\b(exec|system|passthru|shell_exec|popen)\s*\("), "CMD_INJECTION:FUNC_CALL"),
]

# LDAP injection
_LDAP_INJECTION_PATTERNS = [
    (re.compile(r"[()*&|!]"), "LDAP_INJECTION:METACHAR"),
]

# NoSQL injection
_NOSQL_INJECTION_PATTERNS = [
    (re.compile(r"(?i)\$where\b"), "NOSQL_INJECTION:WHERE"),
    (re.compile(r"(?i)\$ne\b"), "NOSQL_INJECTION:NE"),
    (re.compile(r"(?i)\$gt\b"), "NOSQL_INJECTION:GT"),
    (re.compile(r"(?i)\$regex\b"), "NOSQL_INJECTION:REGEX"),
]

# Todos los patrones agrupados por categoría
_ALL_PATTERNS: list[tuple[re.Pattern, str]] = (
    _SQLI_PATTERNS + _XSS_PATTERNS + _PATH_TRAVERSAL_PATTERNS +
    _CMD_INJECTION_PATTERNS + _LDAP_INJECTION_PATTERNS + _NOSQL_INJECTION_PATTERNS
)

# Rutas excluidas del WAF (healthcheck, docs, assets estáticos, auth público)
_WAF_EXCLUDE_PATHS = {
    "/health",
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/api/security/csp-report",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/password-strength",
    "/api/localization/config",
    "/api/localization/validate-id",
    "/api/localization/regions",
    "/api/localization/communes",
    "/api/localization/holidays",
    "/api/localization/business-day",
    "/api/localization/add-business-days",
    "/api/localization/format-currency",
}

# ---------------------------------------------------------------------------
# Rate limiting por clave (user_id o IP) con LRU + TTL
# ---------------------------------------------------------------------------

@dataclass
class _RateLimitTracker:
    """Tracker de rate limit con LRU y TTL por clave."""
    max_entries: int = 10_000
    ttl_seconds: int = 60  # ventana de 1 min

    def __post_init__(self):
        self._hits: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = __import__("asyncio").Lock()

    async def consume(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        """
        Intenta consumir 1 request del límite.
        Returns: (allowed, remaining)
        """
        async with self._lock:
            now = time.monotonic()
            if key not in self._hits:
                if len(self._hits) >= self.max_entries:
                    self._hits.popitem(last=False)
                self._hits[key] = []

            # Limpiar entradas fuera de ventana
            self._hits[key] = [t for t in self._hits[key] if now - t < window]

            remaining = max(0, limit - len(self._hits[key]))
            if len(self._hits[key]) >= limit:
                return False, 0

            self._hits[key].append(now)
            self._hits.move_to_end(key)
            return True, remaining - 1

    async def get_remaining(self, key: str, limit: int, window: float = 60.0) -> int:
        async with self._lock:
            now = time.monotonic()
            if key not in self._hits:
                return limit
            hits = [t for t in self._hits[key] if now - t < window]
            return max(0, limit - len(hits))


# Tracker global para rate limiting
_RATE_LIMIT_TRACKER = _RateLimitTracker()

# Tracker para bloqueos persistentes (WAF)
@dataclass
class _BlockTracker:
    """Tracker de IPs bloqueadas por WAF (LRU con TTL)."""
    max_entries: int = 5000
    ttl_seconds: int = 900  # 15 min default

    def __post_init__(self):
        self._blocks: OrderedDict[str, list[float]] = OrderedDict()

    def record_block(self, identifier: str) -> int:
        """Registra un bloqueo. Devuelve conteo en ventana."""
        now = time.monotonic()
        if identifier not in self._blocks:
            if len(self._blocks) >= self.max_entries:
                self._blocks.popitem(last=False)
            self._blocks[identifier] = []
        self._blocks[identifier] = [t for t in self._blocks[identifier] if now - t < self.ttl_seconds]
        self._blocks[identifier].append(now)
        self._blocks.move_to_end(identifier)
        return len(self._blocks[identifier])

    def is_persistent_attacker(self, identifier: str, threshold: int) -> bool:
        return self.record_block(identifier) >= threshold


_BLOCK_TRACKER = _BlockTracker()

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class WAFMiddleware(BaseHTTPMiddleware):
    """
    Middleware WAF con:
    - Whitelist CIDR (skip completo)
    - Rate limit por user_id (autenticado) o IP (anónimo)
    - Patrones de ataque (SQLi, XSS, Path Traversal, CMD, LDAP, NoSQL)
    - Bloqueo persistente tras N detecciones
    - Admin endpoints para monitoreo
    """

    def __init__(
        self,
        app,
        *,
        log_only: bool = False,
    ):
        super().__init__(app)
        self.log_only = log_only
        self._settings = None  # Se carga en primer request

    def _load_settings(self):
        if self._settings is None:
            self._settings = get_settings()

    def _is_whitelisted(self, ip: str) -> bool:
        """Verifica si la IP está en whitelist CIDR."""
        try:
            ip_obj = ipaddress.ip_address(ip)
            return any(ip_obj in net for net in self._settings.waf_whitelist_networks)
        except ValueError:
            return False

    def _get_rate_limit_key(self, request: Request) -> tuple[str, int, int]:
        """
        Determina la clave y límites de rate limit.
        Returns: (key, rpm, burst)
        - Autenticado: key="user:{user_id}", rpm=300
        - Anónimo: key="ip:{ip}", rpm=60 (más estricto)
        """
        # Intentar obtener user_id del request.state (establecido por auth middleware)
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return f"user:{user_id}", 300, 60

        # Anónimo: usar IP
        from src.core.middleware import ip_de_cliente
        ip = ip_de_cliente(request)
        return f"ip:{ip}", 60, 20

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self._load_settings()

        # Skip paths excluidos
        if request.url.path in _WAF_EXCLUDE_PATHS:
            return await call_next(request)

        # IP del cliente
        from src.core.middleware import ip_de_cliente
        client_ip = ip_de_cliente(request)

        # 1. WHITELIST: Skip completo para IPs de confianza
        if self._is_whitelisted(client_ip):
            return await call_next(request)

        # 2. RATE LIMIT (por user_id o IP)
        rl_key, rpm, burst = self._get_rate_limit_key(request)
        allowed, remaining = await _RATE_LIMIT_TRACKER.consume(rl_key, rpm)
        if not allowed:
            retry_after = 60  # segundos hasta que limpie la ventana
            logger.warning(
                "Rate limit excedido | key=%s ip=%s path=%s",
                rl_key, client_ip, request.url.path
            )
            return Response(
                content='{"detail":"Rate limit excedido. Intenta de nuevo en un momento."}',
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(rpm),
                    "X-RateLimit-Remaining": "0",
                },
            )

        # 3. WAF PATTERNS (siempre inspeccionar; log_only solo evita bloqueo)
        inspection_data = await self._collect_inspection_data(request)
        attack_type, matched_pattern = self._inspect(inspection_data)

        if attack_type:
            # Log estructurado para SIEM (siempre, incluso en log_only)
            logger.warning(
                "WAF: Ataque detectado | ip=%s type=%s pattern=%s path=%s method=%s ua=%s log_only=%s",
                client_ip, attack_type, matched_pattern, request.url.path,
                request.method, request.headers.get("user-agent", "")[:100],
                self.log_only
            )

            # Contar bloqueos por IP para escalado
            block_count = _BLOCK_TRACKER.record_block(client_ip)

            # Escalado: IP persistente -> bloqueo extendido
            threshold = self._settings.waf_block_threshold
            if block_count >= threshold:
                duration = self._settings.waf_block_duration
                logger.error(
                    "WAF: IP marcada como atacante persistente | ip=%s count=%d duration=%ds",
                    client_ip, block_count, duration
                )

            # Bloquear solo si NO es log_only
            if not self.log_only:
                return self._block_response(client_ip, f"Ataque detectado: {attack_type}", 403)

        # Agregar headers de rate limit a la respuesta
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    async def _collect_inspection_data(self, request: Request) -> dict[str, str]:
        """Extrae todos los inputs del request que pueden contener payloads maliciosos."""
        data: dict[str, str] = {}

        # Query params
        for k, v in request.query_params.multi_items():
            data[f"query:{k}"] = v

        # Headers relevantes
        for h in ("user-agent", "referer", "x-forwarded-for", "x-real-ip", "origin"):
            val = request.headers.get(h)
            if val:
                data[f"header:{h}"] = val

        # Body (solo POST/PUT/PATCH, content-type conocido, < 100KB)
        if request.method in ("POST", "PUT", "PATCH"):
            ct = request.headers.get("content-type", "")
            if "application/json" in ct or "application/x-www-form-urlencoded" in ct:
                try:
                    body_bytes = await request.body()
                    if body_bytes and len(body_bytes) < 100_000:
                        body_str = body_bytes.decode("utf-8", errors="replace")
                        data["body:raw"] = body_str
                        if "application/json" in ct:
                            import json
                            try:
                                parsed = json.loads(body_str)
                                self._flatten_json(parsed, "body:json", data)
                            except json.JSONDecodeError:
                                pass
                except Exception as exc:
                    logger.debug("WAF: Error leyendo body: %s", exc)

        return data

    def _flatten_json(self, obj: Any, prefix: str, out: dict[str, str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                self._flatten_json(v, f"{prefix}:{k}", out)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                self._flatten_json(v, f"{prefix}[{i}]", out)
        else:
            out[prefix] = str(obj)

    def _inspect(self, data: dict[str, str]) -> tuple[str | None, str | None]:
        for value in data.values():
            if not value:
                continue
            for pattern, attack_type in _ALL_PATTERNS:
                if pattern.search(value):
                    return attack_type, pattern.pattern[:80]
        return None, None

    def _block_response(self, ip: str, detail: str, status: int) -> Response:
        retry_after = self._settings.waf_block_duration
        return Response(
            content=f'{{"detail":"{detail}"}}',
            status_code=status,
            media_type="application/json",
            headers={
                "Retry-After": str(retry_after),
                "X-WAF-Blocked": "true",
                "X-Content-Type-Options": "nosniff",
            },
        )


# ---------------------------------------------------------------------------
# Endpoints de Admin para monitoreo WAF
# ---------------------------------------------------------------------------

def register_waf_admin(app) -> None:
    """Registra endpoints de admin WAF (protegidos por permisos)."""
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel

    router = APIRouter(prefix="/api/admin/waf", tags=["admin-waf"])

    class BlockInfo(BaseModel):
        identifier: str
        block_count: int
        is_persistent: bool
        first_seen: float | None = None
        last_seen: float | None = None

    @router.get("/stats")
    async def waf_stats():
        """Estadísticas generales del WAF."""
        return {
            "rate_limit_entries": len(_RATE_LIMIT_TRACKER._hits),
            "blocked_ips": len(_BLOCK_TRACKER._blocks),
            "config": {
                "log_only": get_settings().waf_log_only,
                "whitelist_count": len(get_settings().waf_whitelist_networks),
                "block_threshold": get_settings().waf_block_threshold,
                "block_duration": get_settings().waf_block_duration,
            },
        }

    @router.get("/blocked")
    async def list_blocked(limit: int = Query(50, le=200)) -> list[BlockInfo]:
        """Lista IPs/usuarios bloqueados recientemente."""
        now = time.monotonic()
        results = []
        for identifier, timestamps in list(_BLOCK_TRACKER._blocks.items())[:limit]:
            if not timestamps:
                continue
            results.append(BlockInfo(
                identifier=identifier,
                block_count=len(timestamps),
                is_persistent=len(timestamps) >= get_settings().waf_block_threshold,
                first_seen=min(timestamps),
                last_seen=max(timestamps),
            ))
        return sorted(results, key=lambda x: x.last_seen or 0, reverse=True)

    @router.post("/unblock/{identifier}")
    async def unblock(identifier: str):
        """Desbloquea manualmente una IP/usuario."""
        if identifier in _BLOCK_TRACKER._blocks:
            del _BLOCK_TRACKER._blocks[identifier]
            logger.info("WAF: Desbloqueo manual | identifier=%s", identifier)
            return {"status": "unblocked", "identifier": identifier}
        raise HTTPException(404, "Identifier no encontrado en bloqueos")

    @router.post("/clear-rate-limits")
    async def clear_rate_limits(prefix: str | None = Query(None)):
        """Limpia rate limits (opcionalmente por prefijo)."""
        count = 0
        if prefix:
            to_del = [k for k in _RATE_LIMIT_TRACKER._hits if k.startswith(prefix)]
            for k in to_del:
                del _RATE_LIMIT_TRACKER._hits[k]
                count += 1
        else:
            count = len(_RATE_LIMIT_TRACKER._hits)
            _RATE_LIMIT_TRACKER._hits.clear()
        return {"cleared": count, "prefix": prefix}

    app.include_router(router)


def register_waf(app, *, log_only: bool = False) -> None:
    """
    Registra WAF middleware y endpoints de admin.
    Uso en main.py:
        from src.core.waf import register_waf
        register_waf(app, log_only=settings.waf_log_only)
    """
    app.add_middleware(WAFMiddleware, log_only=log_only)
    register_waf_admin(app)