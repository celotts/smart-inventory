"""
Configuración de la aplicación. Toda la configuración entra por variables de
entorno y se lee UNA vez, aquí.

POR QUE ESTO Está AISLADO
-------------------------
El objetivo explicito es que cambiar de proveedor de IA —Ollama local hoy, algo
gestionado mañana— no toque una sola línea de código de negocio. Eso se
consigue con dos reglas:

  1. NADA lee `os.environ` fuera de este archivo. Un `os.getenv("OPENAI_API_KEY")`
     escondido en un router es exactamente lo que obliga a tocar código cuando
     el proveedor cambia.
  2. El proveedor se elige con UNA variable, `LLM_PROVIDER`, y se resuelve en
     `core/llm/factory.py`. El resto del código solo conoce la clase base.

COMO SE USA EN CADA AMBIENTE
-----------------------------
   local      LLM_PROVIDER=ollama, OLLAMA_BASE_URL=http://ollama:11434
   producción LLM_PROVIDER=openai, OPENAI_API_KEY=..., OLLAMA_BASE_URL=/dev/null

En producción las variables vienen del secret manager o del orquestador, no de
un .env. El `.env.example` de la raíz documenta los nombres.
"""

from functools import lru_cache
from typing import ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Valores por defecto pensados para el compose local. Si se cambia el
# proveedor hay que cambiar esto también; es el único acoplamiento con Ollama.
DEFAULT_OLLAMA_URL = "http://ollama:11434"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---------------------------------------------------------
    app_name: str = "Gestor de Activos — API"
    app_env: str = "production"
    debug: bool = False

    # --- Base de datos ---------------------------------------------------
    database_url: str = "postgresql://postgres:secretpassword@db:5432/smart_inventory"

    # --- Proveedor de LLM ------------------------------------------------
    # `ollama` u `openai`. Ver core/llm/factory.py.
    llm_provider: str = "ollama"

    # Prompts system que se usan como base. Se pueden sobreescribir por
    # entorno sin tocar código, que es útil para ajustar el tono del negocio
    # sin un deploy.
    llm_system_prompt_path: str = "prompts/system.txt"

    # --- Ollama ----------------------------------------------------------
    ollama_base_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = "llama3.1:8b"
    # Si el modelo principal falla o contesta vacío, se reintenta con este.
    # En una laptop sin GPU es la diferencia entre "responde lento" y "no
    # responde": un 3b cabe en cualquier lado y un 8b no siempre.
    ollama_fallback_model: str = "llama3.2:3b"
    ollama_timeout_seconds: float = 120.0
    # Ventana de contexto. 8192 es el default de llama3.1 y alcanza para el
    # esquema completo del inventario; bajarlo ahorra RAM en la maquina que
    # corre el modelo, que suele ser la más Justa.
    ollama_num_ctx: int = 8192
    ollama_temperature: float = 0.1

    # --- OpenAI ----------------------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # None = endpoint publico
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 60.0

    # --- Autenticación --------------------------------------------------
    # El secreto con el que se firman los JWT. Sin default utilizable: se
    # pone UN valor de ejemplo para que `make up` funcione en el primer
    # arranque, y `core/security.py` RECHAZA ese valor si corre en producción.
    #
    # La alternativa —sin default— hace que el primer `make up` de alguien
    # falle y termine copiando una key de ejemplo al .env sin mirarla. Con
    # este default, el error sale a los 5 segundos de arrancar.
    SECRET_KEY_DE_EJEMPLO: ClassVar[str] = (
        "CAMBIAME-esta-clave-por-una-real-de-48-caracteres"
    )

    secret_key: str = SECRET_KEY_DE_EJEMPLO

    # Un access token vive 15 minutos. Corto a propósito: como NO se guarda en
    # la base, la única forma de dejar de aceptarlo es que expire. La
    # revocacion inmediata se hace con sessions.revoked_before, que obliga a
    # ir a la base — y eso tiene que valer la pena, no pasar en cada request.
    access_token_expire_minutes: int = 15

    # Un refresh token vive 30 días y SI se guarda (hasheado). Es la sesión:
    # si al usuario le robaron la notebook, se le cierra la sesión desde el
    # panel y no tiene que esperar 30 días.
    refresh_token_expire_days: int = 30

    # Tope de sesiones activas por usuario. Sin este, un login loop deja 5000
    # filas y cada refresh las recorre. Al superar el tope se corta la más
    # vieja.
    max_sessions_per_user: int = 5

    # --- Params de generación compartidos --------------------------------
    llm_max_tokens: int = 1024
    # Un `.format()` con datos de la BD sobre un modelo debil puede terminar en
    # KeyError. Se formatea con `.format_map` sobre un dict con defaults en
    # `_safe_format`, nunca con un dict vacío.
    llm_timeout_seconds: float = 120.0

    # --- Tope de filas para consultas generadas por el modelo ------------
    # El LLM escribe el SQL; este es el único límite que no puede saltarse.
    ai_max_rows: int = 500
    # Segundos maximos de una consulta generada. Un modelo que responde
    # "SELECT ... FROM movement_history" sin filtro no debe tumbar la API.
    ai_query_timeout_seconds: float = 10.0

    # --- CORS ------------------------------------------------------------
    # Origenes permitidos, separados por coma.
    #
    # ESTO ESTABA HARDCODEADO y era un agujero: en producción el único origen
    # permitido era `https://smart-inventory.local`, un dominio que no existe.
    # Con esa lista, un frontend desplegado en el dominio real no podia llamar
    # a la API. Y el arreglo "mientras tanto, allow_origins=['*']" es
    # justamente el agujero que hace que cualquiera pueda leer la respuesta de
    # una API autenticada desde cualquier página.
    #
    # La lista correcta depende del despliegue, así que no se adivina: se
    # declara. Vacío significa "solo same-origin", que es lo correcto cuando el
    # nginx sirve el front y la API desde el mismo dominio.
    cors_origins: str = ""

    # Redes de confianza para el rate limit y los logs.
    #
    # `X-Forwarded-For` lo pone cualquiera. Si el rate limit lo leyera sin más,
    # un atacante cambia el header con un IP inventado por request y el límite
    # por IP no limita nada: queda como si no existiera.
    #
    # Solo se acepta el header si el peer es una IP de estás redes, que son las
    # del reverse proxy propio. Vacío = no se acepta nunca, que es el default
    # seguro para una API expuesta directo.
    trusted_proxies: str = ""

    # --- Rate limit ------------------------------------------------------
    # Requests por minuto, POR USUARIO (por IP cuando no hay sesion).
    #
    # EL NUMERO
    # ---------
    # 300, y no 120. Una pantalla de la app hace entre 8 y 15 peticiones
    # (el tablero, la lista, los filtros, la campana, el estado de la IA), y la
    # campana suma 2 por minuto todo el tiempo. Con 120/min, un operador que
    # abre y cierra pantallas se encuentra con un 429 sin saber por que.
    #
    # Y con el limite POR USUARIO, 300/min es por persona: una empresa con
    # veinte operadores a la vez puede consumir 6000/min sin que ninguno se
    # frene. Con el limite por IP (que era como estaba), los veinte
    # compartian un contador de 120 y se bloqueaban entre si.
    rate_limit_rpm: int = 300
    rate_limit_burst: int = 60

    # El login tiene su propio límite, más bajo, y es por IP.
    #
    # Por que aparte: el límite general es 120/min, y un ataque de fuerza bruta
    # hace 120 intentos por minuto contra CADA una de las 10.000 cuentas de una
    # base real. El límite general no lo frena porque cada intento individual
    # es una request perfectamente normal.
    #
    # 10/min es el punto: un humano que se equivoca la clave tres veces no nota
    # nada, y un atacante que quiere probar 100 passwords por cuenta necesita
    # 10 minutos por cuenta.
    login_attempts_per_minute: int = 10

    # --- Escaneo ---------------------------------------------------------
    # Longitud máxima de un código leído por el lector.
    #
    # El límite existe porque el código va a una consulta de base. Sin tope, un
    # "código" de 4 MB (que se puede mandar sin drama en un JSON) se convierte
    # en 4 MB de comparacion por índice. 128 caracteres cubre de sobra un QR
    # (alfanumericos, ~40 en la versión de mayor densidad), un EAN-13 y un
    # Code 39.
    scan_code_max_length: int = 128

    # Cuantos códigos acepta un escaneo por lote (conteo ciclico de depósito).
    # Con 500 se cubre una rack completo sin que un cliente pueda pedir 100.000
    # filas en un POST.
    scan_batch_max: int = 500

    # --- Localización ------------------------------------------------------
    # País activo para validaciones (RUT/RFC), comunas/estados, feriados, moneda.
    # ISO 3166-1 alpha-2: "CL", "MX", "CO", "PE", "US", ...
    localization_country: str = "CL"

    # --- WAF / Seguridad -------------------------------------------------
    # Habilitar WAF (patrones de ataque + rate limit anónimo)
    waf_enabled: bool = True
    # Solo loggear, no bloquear (para tuning en staging)
    waf_log_only: bool = False
    # CIDR de redes de confianza (oficina, VPN, CI/CD) - Skip WAF y rate limit
    # Ej: "200.50.10.0/24,192.168.1.0/24,10.0.0.0/8"
    waf_whitelist: str = ""
    # Bloqueos antes de ban persistente (15 min)
    waf_block_threshold: int = 10
    # Duración del ban persistente (segundos)
    waf_block_duration: int = 900

    @property
    def waf_whitelist_networks(self) -> list:
        """Redes CIDR de confianza parseadas."""
        import ipaddress
        redes = []
        for texto in self.waf_whitelist.split(","):
            texto = texto.strip()
            if not texto:
                continue
            try:
                redes.append(ipaddress.ip_network(texto, strict=False))
            except ValueError:
                import logging
                logging.getLogger(__name__).warning(
                    "WAF_WHITELIST: '%s' no es una red válida y se ignora", texto
                )
        return redes

    @property
    def cors_origins_list(self) -> list[str]:
        """
        Origenes parseados, sin vacíos.

        La lista vacía es una decisión, no un olvido: es lo que hace que
        CORSMiddleware no agregue el header `Access-Control-Allow-Origin`, y con
        el, que el navegador bloquee la respuesta. Es lo que corresponde a un
        front servido en el mismo origen que la API.
        """
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxies_list(self) -> list[str]:
        """Redes (no IPs) desde las que se acepta `X-Forwarded-For`."""
        return [r.strip() for r in self.trusted_proxies.split(",") if r.strip()]

    @field_validator("llm_provider")
    @classmethod
    def _provider_valido(cls, valor: str) -> str:
        conocidos = ("ollama", "openai")
        normalizado = valor.strip().lower()
        if normalizado not in conocidos:
            raise ValueError(
                f"LLM_PROVIDER invalido: {valor!r}. Admitidos: {', '.join(conocidos)}"
            )
        return normalizado

    @property
    def llm_is_local(self) -> bool:
        return self.llm_provider == "ollama"


@lru_cache
def get_settings() -> Settings:
    """
    Instancia única. `lru_cache` porque leer y validar el entorno en cada
    request es trabajo tira-después-tira, y porque mutar la configuración a
    mitad de una request seria peor que tener una foto fija.
    """
    return Settings()
