"""
Contrato comun de todos los proveedores de LLM.

El resto de la aplicacion —el agente de SQL, el analizador, el generador de
reportes— solo importa de este archivo. Jamas importa de `ollama.py` ni de
`openai.py`. Ese es el mecanismo que hace que cambiar de proveedor no toque
codigo de negocio.

Que NO se mete en esta clase, a proposito
-----------------------------------------
El streaming, los embeddings y el conteo de tokens de cada proveedor. Meterlos
aqui obliga a que toda implementacion los soporte; lo que se necesita hoy es
`chat()` y ya.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """
    Error del proveedor: caido, sin modelo, timeout, respuesta vacia.

    Deliberadamente NO es un `HTTPException` de FastAPI: esta capa no depende
    de la web. El router traduce esto a un 502, que es lo correcto — si el
    modelo esta caido, el error no es del cliente que pregunto.
    """


@dataclass
class LLMResponse:
    text: str
    model: str
    # Que modelo respondio REALMENTE. Puede diferir del pedido si hubo fallback
    # a uno mas chico: un cliente que ve "llama3.1:8b" en el response y en
    # realidad recibio respuesta de qwen2.5:3b no puede depurar nada.
    model_used: str | None = None
    used_fallback: bool = False
    provider: str = ""
    latency_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.model_used is None:
            self.model_used = self.model


class LLMProvider(ABC):
    """
    Interfaz de proveedor. `chat()` es sincrona a proposito.

    Que sea sincrona no es un descuido: la API es CRUD con PostgreSQL
    bloqueante (SQLAlchemy sync), y las consultas del agente son cortas. Meter
    asyncio obligaria a cambiar el pool de conexiones entero para que el unico
    cliente async —una llamada HTTP— no lo justifique. Si algun dia el LLM pasa
    a ser streaming, ahi si tiene sentido el overhaul.
    """

    #: Identificador corto, para logs y respuestas. Lo implementa cada subclase.
    name: str = ""

    @abstractmethod
    def chat(
        self,
        prompt: str,
        system: str | None = None,
        # `json_mode` no es universal: llama3.1 y qwen2.5 lo soportan via
        # "format": "json" en Ollama, pero no todos los modelos ni todas las
        # versiones. Por eso es una intencion, no una garantia — el que llama
        # tiene que seguir siendo capaz de parsear JSON de texto plano.
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Genera una respuesta para un unico turno. Lanza `LLMError` si falla."""

    def ping(self) -> bool:
        """
        El proveedor responde. NO lanza: devuelve False.

        Lo usa `/api/ai/health` para reportar el estado sin propagar el fallo.
        """
        try:
            self.chat("ping", temperature=0.0)
            return True
        except LLMError:
            return False

    def list_models(self) -> list[str]:
        """Modelos disponibles. Vacio en proveedores que no lo exponen."""
        return []
