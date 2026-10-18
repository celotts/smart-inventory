"""
Proveedor OpenAI.

Existe para el caso de despliegue, no para el de desarrollo. La misma API que
habla con Ollama local en la laptop habla con OpenAI en produccion cambiando
`LLM_PROVIDER=openai` y `OPENAI_API_KEY`. Ningun router, analyzer ni reporte
cambia una linea.

Escribe el request a mano con httpx en vez de usar el SDK oficial. El SDK son
~3 MB y sus modelos de pydantic propios; como ya se habla HTTP para Ollama,
reusar el mismo cliente hace que la clase base sea de verdad la unica
abstraccion. Si en el futuro hacen falta tools/streaming/multimodal, ahi si
conviene el SDK.
"""

import time
from typing import Any

import httpx

from src.core.llm.base import LLMError, LLMProvider, LLMResponse

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        if not api_key:
            # Falla en la construccion y no en el primer request. Un error en
            # el arranque se ve en el deploy; uno en el request lo ve el
            # primer usuario que pregunta algo, que es peor.
            raise LLMError(
                "LLM_PROVIDER=openai pero OPENAI_API_KEY no esta definida. "
                "Definela en el entorno o vuelve a LLM_PROVIDER=ollama."
            )

        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens

        # base_urlcustom es lo que hace que esto tambien sirva para Azure
        # OpenAI y para cualquier gateway compatible (vLLM, LiteLLM): es un
        # endpoint OpenAI-compatible, no un cliente atado a OpenAI.
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResponse:
        mensajes: list[dict[str, str]] = []
        if system:
            mensajes.append({"role": "system", "content": system})
        mensajes.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": mensajes,
            "temperature": temperature
            if temperature is not None
            else self._temperature,
            "max_tokens": self._max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        inicio = time.monotonic()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                respuesta = client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise LLMError(f"OpenAI no respondio en {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Fallo la llamada a {self._base_url}: {exc}") from exc

        if respuesta.status_code >= 400:
            # El 401 es el caso que mas se va a ver y el que peor mensaje trae:
            # se sube la key equivocada o se subio con espacios de mas.
            pista = ""
            if respuesta.status_code == 401:
                pista = (
                    " (revisa OPENAI_API_KEY: suele ser una key vencida o con espacios)"
                )
            raise LLMError(
                f"OpenAI devolvio {respuesta.status_code}{pista}: {respuesta.text[:400]}"
            )

        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise LLMError("OpenAI devolvio una respuesta que no es JSON") from exc

        try:
            texto = datos["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(
                f"Respuesta de OpenAI con forma inesperada: {str(datos)[:400]}"
            ) from exc

        texto = texto.strip()
        if not texto:
            raise LLMError(f"El modelo {self._model} devolvio una respuesta vacia")

        return LLMResponse(
            text=texto,
            model=self._model,
            provider=self.name,
            latency_ms=int((time.monotonic() - inicio) * 1000),
            raw={"usage": datos.get("usage")},
        )

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10.0) as client:
                respuesta = client.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                respuesta.raise_for_status()
                datos = respuesta.json()
        except httpx.HTTPError:
            return []
        return [m.get("id", "") for m in datos.get("data", [])]
