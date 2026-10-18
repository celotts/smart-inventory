"""
Configuracion de la aplicacion. Toda la configuracion entra por variables de
entorno y se lee UNA vez, aqui.

POR QUE ESTO ESTA AISLADO
-------------------------
El objetivo explicito es que cambiar de proveedor de IA —Ollama local hoy, algo
gestionado manana— no toque una sola linea de codigo de negocio. Eso se
consigue con dos reglas:

  1. NADA lee `os.environ` fuera de este archivo. Un `os.getenv("OPENAI_API_KEY")`
     escondido en un router es exactamente lo que obliga a tocar codigo cuando
     el proveedor cambia.
  2. El proveedor se elige con UNA variable, `LLM_PROVIDER`, y se resuelve en
     `core/llm/factory.py`. El resto del codigo solo conoce la clase base.

COMO SE USA EN CADA AMBIENTE
-----------------------------
   local      LLM_PROVIDER=ollama, OLLAMA_BASE_URL=http://ollama:11434
   produccion LLM_PROVIDER=openai, OPENAI_API_KEY=..., OLLAMA_BASE_URL=/dev/null

En produccion las variables vienen del secret manager o del orquestador, no de
un .env. El `.env.example` de la raiz documenta los nombres.
"""

from functools import lru_cache
from typing import ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Valores por defecto pensados para el compose local. Si se cambia el
# proveedor hay que cambiar esto tambien; es el unico acoplamiento con Ollama.
DEFAULT_OLLAMA_URL = "http://ollama:11434"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---------------------------------------------------------
    app_name: str = "Smart Inventory B2B API"
    app_env: str = "production"
    debug: bool = False

    # --- Base de datos ---------------------------------------------------
    database_url: str = "postgresql://postgres:secretpassword@db:5432/smart_inventory"

    # --- Proveedor de LLM ------------------------------------------------
    # `ollama` u `openai`. Ver core/llm/factory.py.
    llm_provider: str = "ollama"

    # Prompts system que se usan como base. Se pueden sobreescribir por
    # entorno sin tocar codigo, que es util para ajustar el tono del negocio
    # sin un deploy.
    llm_system_prompt_path: str = "prompts/system.txt"

    # --- Ollama ----------------------------------------------------------
    ollama_base_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = "llama3.1:8b"
    # Si el modelo principal falla o contesta vacio, se reintenta con este.
    # En una laptop sin GPU es la diferencia entre "responde lento" y "no
    # responde": un 3b cabe en cualquier lado y un 8b no siempre.
    ollama_fallback_model: str = "llama3.2:3b"
    ollama_timeout_seconds: float = 120.0
    # Ventana de contexto. 8192 es el default de llama3.1 y alcanza para el
    # esquema completo del inventario; bajarlo ahorra RAM en la maquina que
    # corre el modelo, que suele ser la mas Justa.
    ollama_num_ctx: int = 8192
    ollama_temperature: float = 0.1

    # --- OpenAI ----------------------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # None = endpoint publico
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 60.0

    # --- Autenticacion --------------------------------------------------
    # El secreto con el que se firman los JWT. Sin default utilizable: se
    # pone UN valor de ejemplo para que `make up` funcione en el primer
    # arranque, y `core/security.py` RECHAZA ese valor si corre en produccion.
    #
    # La alternativa —sin default— hace que el primer `make up` de alguien
    # falle y termine copiando una key de ejemplo al .env sin mirarla. Con
    # este default, el error sale a los 5 segundos de arrancar.
    SECRET_KEY_DE_EJEMPLO: ClassVar[str] = (
        "CAMBIAME-esta-clave-por-una-real-de-48-caracteres"
    )

    secret_key: str = SECRET_KEY_DE_EJEMPLO

    # Un access token vive 15 minutos. Corto a proposito: como NO se guarda en
    # la base, la unica forma de dejar de aceptarlo es que expire. La
    # revocacion inmediata se hace con sessions.revoked_before, que obliga a
    # ir a la base — y eso tiene que valer la pena, no pasar en cada request.
    access_token_expire_minutes: int = 15

    # Un refresh token vive 30 dias y SI se guarda (hasheado). Es la sesion:
    # si al usuario le robaron la notebook, se le cierra la sesion desde el
    # panel y no tiene que esperar 30 dias.
    refresh_token_expire_days: int = 30

    # Tope de sesiones activas por usuario. Sin este, un login loop deja 5000
    # filas y cada refresh las recorre. Al superar el tope se corta la mas
    # vieja.
    max_sessions_per_user: int = 5

    # --- Params de generacion compartidos --------------------------------
    llm_max_tokens: int = 1024
    # Un `.format()` con datos de la BD sobre un modelo debil puede terminar en
    # KeyError. Se formatea con `.format_map` sobre un dict con defaults en
    # `_safe_format`, nunca con un dict vacio.
    llm_timeout_seconds: float = 120.0

    # --- Tope de filas para consultas generadas por el modelo ------------
    # El LLM escribe el SQL; este es el unico limite que no puede saltarse.
    ai_max_rows: int = 500
    # Segundos maximos de una consulta generada. Un modelo que responde
    # "SELECT ... FROM movement_history" sin filtro no debe tumbar la API.
    ai_query_timeout_seconds: float = 10.0

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
    Instancia unica. `lru_cache` porque leer y validar el entorno en cada
    request es trabajo tira-despues-tira, y porque mutar la configuracion a
    mitad de una request seria peor que tener una foto fija.
    """
    return Settings()
