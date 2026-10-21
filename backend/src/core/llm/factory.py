"""
Fabrica de proveedores: el único punto del código donde se decide que LLM se
usa.

Si el resto de la aplicación llamara a `OllamaProvider()` directamente,
cambiar a OpenAI seria una busqueda y reemplazos en todos los routers. Con
esto, es `LLM_PROVIDER=openai` en el entorno y nada más.
"""

from src.core.config import Settings, get_settings
from src.core.llm.base import LLMError, LLMProvider
from src.core.llm.ollama import OllamaProvider
from src.core.llm.openai import OpenAIProvider

# Cacheda del provider. Se construye una vez por proceso.
#
# NO se cachea cuando se pasa `settings` explicitamente: los tests pasan
# configuraciones distintas y cachearlas por identidad haria que el segundo
# test recibiera el provider del primero, que es una forma de test que pasa
# solo la primera vez.
_proveedor: LLMProvider | None = None


def construir_proveedor(settings: Settings) -> LLMProvider:
    """
    Crea el provider según `settings.llm_provider`.

    La validación del valor ya la hizo el field_validator de Settings, así que
    el `else` de abajo es practicamente inalcanzable. Está a propósito: si
    alguien agrega "anthropic" a la config y olvida está función, que reviente
    con un error claro en el arranque y no con un KeyError en el primer
    request.
    """
    if settings.llm_provider == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            fallback_model=settings.ollama_fallback_model,
            timeout=settings.ollama_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
            temperature=settings.ollama_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    if settings.llm_provider == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_seconds,
            temperature=settings.ollama_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    raise LLMError(f"Proveedor no soportado: {settings.llm_provider!r}")


def get_llm() -> LLMProvider:
    """El provider del proceso. Es lo que usan routers, analyzer y reportes."""
    global _proveedor
    if _proveedor is None:
        _proveedor = construir_proveedor(get_settings())
    return _proveedor
