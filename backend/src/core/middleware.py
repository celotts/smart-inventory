"""
Middleware de seguridad y observabilidad para la API de Gestor de Activos.

Capa transversal: se aplica a TODAS las rutas, incluyendo las que no requieren
auth, porque el objetivo es defender el perimetro antes de que llegue al router.

LO QUE NO HACIA ESTE ARCHIVO Y AHORA SI
----------------------------------------
1. `Permissions-Policy: camera=(), geolocation=()` — IMPEDIA USAR EL ESCANER.
   El header dice "ninguna página de este origen puede abrir la camara ni leer
   la ubicacion". La API manda ese header, y el navegador lo aplica a TODO el
   origen, no a una página: con el, el `getUserMedia()` del lector de QR falla
   con NotAllowedError y la geolocalizacion del escaneo no existe. La politica
   de permisos era correcta para una API sin camara y por eso rompio el
   escaner. Ahora declara lo que el sistema SI usa y sigue negando el resto.

2. El rate limit creaba una entrada por IP para siempre. `self._buckets[ip]` es
   un defaultdict: cada IP nueva agrega una key, y la key solo se limpia cuando
   ESA IP vuelve a pedir. Un atacante que rota IPs —que es lo que hace cualquiera
   con una botnet— no genera entradas grandes, genera entradas INFINITAS y
   chicas. El proceso se comes la memoria hasta que lo matan, sin un solo error
   en los logs. Ahora hay un tope de IPs seguidas y las que no se usan se van.

3. `X-Forwarded-For` se ignoraba, lo cual hacia que el límite por IP contara a
   todo el mundo como si fuera la IP del proxy (una sola) o, si se fixaba a
   mano, lo hiciera confiado en un header que cualquiera puede mandar. Ahora se
   lee Solo si el peer es un proxy de confianza, y si no, se usa el peer.

4. El login no tenia límite propio. Ver la nota de `login_attempts_per_minute`
   en `core/config.py`.
"""

from __future__ import annotations

import ipaddress
import time
import uuid
from collections import OrderedDict, deque
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
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # `X-XSS-Protection` se elimino a propósito. Está cabecera estaba en el
    # header original y hoy es contraproducente: los navegadores que la
    # soportan la ignoran, y en los que no, activa un filtro que puede
    # MANGAR páginas legitimas. La defensa real contra XSS es la CSP de abajo,
    # no un filtro heuristico que ya no existe.
    #
    # ------------------------------------------------------------------------
    # PERMISSIONS-POLICY: POR QUE CAMERA Y GEOLOCATION Están HABILITADAS
    # ------------------------------------------------------------------------
    # Está es una API de inventario escaneado. El flujo de trabajo del
    # operador es: abrir la app, apuntar la camara a una etiqueta, y la app
    # confirma la ubicación con el GPS para validar que el escaneo ocurrio en
    # la sede. Las dos capacidades son REQUISITOS, no un lujo.
    #
    # Bloquearlas con `camera=()` no es "defensa": es romper la aplicación. Y lo
    # que se debe negar es lo que el sistema no usa, que es lo que sigue.
    "Permissions-Policy": (
        "camera=(self), "
        "geolocation=(self), "
        "microphone=(), "
        "payment=(), "
        "usb=(), "
        "midi=(), "
        "interest-cohort=()"
    ),
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
}

# ---------------------------------------------------------------------------
# CSP
# ---------------------------------------------------------------------------
#
# POR QUE UNA CSP Y NO NADA
# -------------------------
# Los headers de arriba son defensa en profundidad: cada uno tapa una clase de
# ataque. La CSP es la que tapa la que importa acá, que es XSS: si una
# inyeccion de HTML se colara en una respuesta, `script-src 'none'` la deja sin
# poder ejecutarse aunque haya entrado.
#
# `style-src 'unsafe-inline' https://fonts.googleapis.com`: NO es ideal y es un precio consciente. Sin eso,
# los estilos de los componentes con estilos inline no funcionan. Es lo único
# que se cede, y es lo único que puede ejecutar CSS, no JS.
#
# `connect-src 'self'`: la API no puede ser llamada desde otro origen por el
# navegador, sin importar que diga CORS.
#
# `font-src 'self' https://fonts.gstatic.com`: permite cargar fuentes de Google Fonts.
# `style-src 'unsafe-inline' https://fonts.googleapis.com`: permite cargar CSS de Google Fonts.
# `script-src 'nonce-{nonce}' 'strict-dynamic'`: permite scripts con nonce y módulos que confían en el nonce.
CSP_API = (
    "default-src 'none'; "
    "script-src 'nonce-{nonce}' 'strict-dynamic'; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data:; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generar nonce para CSP
        nonce = uuid.uuid4().hex
        request.state.csp_nonce = nonce

        response = await call_next(request)

        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)

        # HSTS con preload + Expect-CT para producción HTTPS
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
            response.headers.setdefault(
                "Expect-CT",
                'max-age=86400, enforce, report-uri="/api/security/expect-ct-report"',
            )

        # Headers de seguridad adicionales para TODAS las respuestas (API + Frontend)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Embedder-Policy", "require-corp")

        # Rutas que son API (necesitan CSP estricta sin scripts)
        es_api = request.url.path.startswith(("/api/", "/health", "/api/"))

        # La CSP va Solo en las respuestas de la API. En `/docs` no, porque
        # Swagger UI carga sus scripts de un CDN y una CSP de API lo dejaria en
        # blanco. El reason es el mismo por el que no se sirve `/docs` en
        # producción: es una superficie, no un endpoint de negocio.
        if not request.url.path.startswith(("/docs", "/redoc")):
            if request.url.path.startswith(("/api/", "/health")):
                # API: CSP estricta sin scripts
                csp = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data:; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            else:
                # Frontend: CSP con nonce para permitir scripts con nonce
                nonce = uuid.uuid4().hex
                request.state.csp_nonce = nonce
                csp = CSP_API.format(nonce=nonce)
            response.headers.setdefault("Content-Security-Policy", csp)
            # Pasar nonce a los templates si se renderizan
            request.state.csp_nonce = getattr(request.state, "csp_nonce", None)

        # HSTS con preload + Expect-CT para producción HTTPS
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
            response.headers.setdefault(
                "Expect-CT",
                'max-age=86400, enforce, report-uri="/api/security/expect-ct-report"',
            )

        # Headers de seguridad adicionales para TODAS las respuestas (API + Frontend)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Embedder-Policy", "require-corp")

        return response


# ---------------------------------------------------------------------------
# Confianza en el proxy
# ---------------------------------------------------------------------------
def _parse_redes_confiadas(redes: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """
    Parsea la lista de redes de confianza una sola vez, al construir el
    middleware.

    Parsear en cada request es un trabajo tira-después-tira sobre una lista que
    no cambia nunca, y el peor caso de ese error no es performance: es que un
    texto mal escrito en la config se convierta en una excepción por request
    que nadie ve.
    """
    redes_ok = []
    for texto in redes:
        try:
            redes_ok.append(ipaddress.ip_network(texto, strict=False))
        except ValueError:
            # Una red mal escrita se IGNORA y se avisa por log, en vez de tirar
            # la exception. El default (no confiar en nadie) es el seguro, así
            # que un typo no abre la puerta: la deja cerrada.
            import logging

            logging.getLogger(__name__).warning(
                "TRUSTED_PROXIES: '%s' no es una red valida y se ignora", texto
            )
    return redes_ok


def ip_de_cliente(request: Request) -> str:
    """
    La IP real del cliente, o la del peer si no hay proxy de confianza.

    POR QUE NO SE LEE `X-Forwarded-For` A SECAS
    -------------------------------------------
    Ese header lo pone CUALQUIERA. Si una API lee `X-Forwarded-For` para decidir
    a quien limitar, un atacante manda `X-Forwarded-For: 1.2.3.4` en cada
    request y el límite por IP no frena nada.

    Solo se cree cuando el peer (la IP REAL de la conexión) pertenece a una red
    de confianza. Detras de un nginx propio, el peer SI es el nginx, y ahí el
    header es legitimo. Sin proxy configurado, el peer es el cliente y el
    header se ignora.
    """
    peer = request.client.host if request.client else None

    redes = getattr(request.app.state, "redes_confiadas", None)
    if not redes or not peer:
        return peer or "unknown"

    try:
        ip_peer = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    if not any(ip_peer in red for red in redes):
        return peer

    # `X-Real-IP` es el que pone un nginx de un solo salto. `X-Forwarded-For`
    # es una CADENA de proxies, y el CLIENTE REAL es el último. Se toma el
    # último porque el primero lo pone el proxy de borde, que es el menos de
    # fiar: es el único que ve el atacante.
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return _validar_ip(real_ip) or peer

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        candidatos = [p.strip() for p in xff.split(",") if p.strip()]
        if candidatos:
            return _validar_ip(candidatos[-1]) or peer

    return peer


def _validar_ip(valor: str) -> str | None:
    """Devuelve la IP si es una IP válida, None si no. Nunca lanza."""
    try:
        ipaddress.ip_address(valor)
        return valor
    except ValueError:
        # Un X-Forwarded-For con basura no es un error 500 esperando pasar: es
        # un dato que se descarta y se sigue con el peer.
        return None


# ---------------------------------------------------------------------------
# Rate limiting por IP (token bucket en memoria, por proceso)
# ---------------------------------------------------------------------------
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Límite simple por IP, con ventana deslizante.

    POR QUE SIGUE SIENDO EN MEMORIA Y NO REDIS
    --------------------------------------------
    Porque el compose corre UNA instancia de uvicorn. Con varias replicas hay
    que pasar a Redis. La limitacion de esto: cada replica tiene su propio
    contador, así que el límite efectivo es N veces el configurado. Documentado
    en el docstring de `get_settings().rate_limit_rpm`.

    POR QUE UN LRU CON TOPE Y NO UN defaultdict INFINITO
    ---------------------------------------------------
    El `defaultdict(deque)` original nunca olvidar una IP: la key se creaba al
    primer request y se limpiaba solo cuando ESA IP volvia a pedir. Con IPs
    rotativas (una botnet genera 1000 por minuto), eso es 1000 claves por
    minuto que nunca se van. No es un problema de performance: es un OOM
    esperando, y el sintoma (el contenedor muere) no dice nada de la causa.

    Aquí, el `_buckets` es un LRU con tope. Cuando llega una IP nueva y el
    dict está lleno, se expulsa la Más VIEJA (la que hace más que no pide). En
    el caso de un ataque de IPs rotativas eso es lo correcto: las IPs viejas ya
    no atacan.
    """

    # Endpoints que los probes del orquestador consultan constantly. Limitar el
    # healthcheck hace que un reinicio lento se convierta en un reinicio en
    # loop: el healthcheck da 429, el orquestador marca unhealthy, reinicia, y
    # el proceso que arrancaba consume su propio límite.
    SIN_LIMITE = ("/health", "/", "/docs", "/redoc", "/openapi.json")

    def __init__(self, app, requests_per_minute: int | None = None, burst: int | None = None):
        super().__init__(app)
        settings = get_settings()
        self.rpm = requests_per_minute or settings.rate_limit_rpm
        self.burst = burst or settings.rate_limit_burst
        self._buckets: OrderedDict[str, deque] = OrderedDict()
        self._max_ips = 10_000
        self._ventana = 60.0
        self._lock = __import__("asyncio").Lock()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.SIN_LIMITE:
            return await call_next(request)

        ip = ip_de_cliente(request)
        ahora = time.monotonic()

        async with self._lock:
            permitidos = self._consumir(ip, ahora)

        if not permitidos:
            # `Retry-After` con lo que falta de la ventana, para que el cliente
            # sepa cuando reintentar en vez de hammer en loop.
            ventana_restante = self._segundos_hasta_ventana(ip, ahora)
            return Response(
                content='{"detail":"Demasiadas solicitudes. Intenta de nuevo en un momento."}',
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(ventana_restante),
                    "X-RateLimit-Limit": str(self.rpm),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        async with self._lock:
            restantes = max(0, self.rpm - len(self._buckets.get(ip, ())))
            response.headers["X-RateLimit-Limit"] = str(self.rpm)
            response.headers["X-RateLimit-Remaining"] = str(restantes)
        return response

    def _consumir(self, ip: str, ahora: float) -> bool:
        """Registra un request. Devuelve False si excede el límite."""
        if ip not in self._buckets:
            # Expulsar la IP menos reciente cuando se llega al tope.
            if len(self._buckets) >= self._max_ips:
                self._buckets.popitem(last=False)
            self._buckets[ip] = deque()

        bucket = self._buckets[ip]
        # Limpiar lo que ya salió de la ventana.
        while bucket and bucket[0] < ahora - self._ventana:
            bucket.popleft()

        if len(bucket) >= self.rpm:
            return False

        bucket.append(ahora)
        # Marcar como reciente para que el LRU expulse la que hace más tiempo
        # que no pide.
        self._buckets.move_to_end(ip)
        return True

    def _segundos_hasta_ventana(self, ip: str, ahora: float) -> int:
        bucket = self._buckets.get(ip)
        if not bucket:
            return 1
        return max(1, int(self._ventana - (ahora - bucket[0])) + 1)


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Límite DEDICADO para login y refresh.

    Por que no alcanza con `RateLimitMiddleware`: el límite general es
    120/min, y un ataque de fuerza bruta a 120/min contra cada cuenta de la
    base no lo frena, porque cada intento individual es una request normal.
    Este middleware cuenta solo intentos CONTRA EL LOGIN y baja el tope a 10/min.

    Cuenta Solo los fallos, no los exitosos: si contara todos, un operador que
    entra y sale del panel veinte veces en una hora (algo normal) se encontraria
    con un 429 sin saber por que.
    """

    def __init__(self, app):
        super().__init__(app)
        settings = get_settings()
        self.max_attempts = settings.login_attempts_per_minute
        self._ventana = 60.0
        self._fallos: OrderedDict[str, deque] = OrderedDict()
        self._lock = __import__("asyncio").Lock()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path not in ("/api/auth/login", "/api/auth/refresh"):
            return await call_next(request)

        ip = ip_de_cliente(request)
        ahora = time.monotonic()

        async with self._lock:
            # Limpiar los registros de este IP que ya no cuentan.
            if ip in self._fallos:
                self._fallos[ip] = deque(
                    t for t in self._fallos[ip] if t > ahora - self._ventana
                )

            if len(self._fallos.get(ip, ())) >= self.max_attempts:
                espera = max(
                    1, int(self._ventana - (ahora - self._fallos[ip][0])) + 1
                )
                return Response(
                    content='{"detail":"Demasiados intentos de acceso. Espera un minuto e intenta de nuevo."}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": str(espera)},
                )

        response = await call_next(request)

        # 401 = credenciales malas. Solo ese caso cuenta como intento fallido.
        if response.status_code == 401:
            async with self._lock:
                self._fallos.setdefault(ip, deque()).append(ahora)
                if len(self._fallos) > self._max_ips():
                    self._fallos.popitem(last=False)

        return response

    def _max_ips(self) -> int:
        return 10_000


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Correlacion de request.

    `X-Request-ID` del cliente se ACEPTA y se devuelve tal cual, para que un
    trace del frontend y un log del backend se cruzen por el mismo id. No es un
    dato confiable: va como parámetro a los logs, nunca a una consulta SQL.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# En desarrollo, si no se declara CORS_ORIGINS, se permiten los puertos de vite
# y los de los probadores de API. Es una comodidad de desarrollo y NO un
# default seguro: por eso no aplica en producción.
ORIGENES_DE_DESARROLLO = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:8080",
    "http://127.0.0.1:5173",
]


def configurar_cors(app) -> None:
    settings = get_settings()

    origenes = settings.cors_origins_list
    es_produccion = settings.app_env.lower() in ("production", "prod")

    if not origenes and not es_produccion:
        # Desarrollo sin CORS_ORIGINS: los puertos de desarrollo de vite.
        origenes = ORIGENES_DE_DESARROLLO

    if es_produccion and not origenes:
        # Sin CORS_ORIGINS en producción NO se permite ningún origen cruzado.
        # Es el default correcto para un front servido en el mismo dominio que
        # la API (lo que hace el nginx del compose): ahí no hace falta CORS
        # porque no hay origen cruzado.
        #
        # Lo que NO se hace es inventar un origen (el bug original ponia
        # `https://smart-inventory.local`, que no existe) ni abrir `*` con
        # credenciales, que es la combinacion que permite que cualquier página
        # lea las respuestas de una API autenticada.
        origenes = []

    app.state.redes_confiadas = _parse_redes_confiadas(settings.trusted_proxies_list)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes,
        allow_credentials=bool(origenes),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
        max_age=600,
    )
