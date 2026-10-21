"""
Proveedor Ollama.

Como es el camino por defecto, este archivo resuelve una pregunta importante:
que pasa en producción, donde casi siempre no hay una GPU en cada nodo.

La respuesta es que `ollama_base_url` es una URL más. Apunta a `ollama:11434`
en local, y a `http://nodo-gpu.interno:11434` o a un servicio gestionado que
exponga la misma API desde producción. El código no cambia. Ese es el punto de
toda la indireccion en core/llm/.

Se usa /api/chat y no /api/generate porque /api/chat acepta un `system` como
primer mensaje de forma nativa; con /api/generate habria que concatenar el
system al prompt a mano y el modelo pierde la separacion.
"""

import time
from typing import Any

import httpx

from src.core.llm.base import LLMError, LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        fallback_model: str | None = None,
        timeout: float = 120.0,
        num_ctx: int = 8192,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        # Sin barra final: si alguien pone `http://ollama:11434/` el
        # doble slash rompe la URL en algunas rutas.
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._fallback_model = fallback_model
        self._timeout = timeout
        self._num_ctx = num_ctx
        self._temperature = temperature
        self._max_tokens = max_tokens

    # -- API interna ----------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                respuesta = client.post(f"{self._base_url}{path}", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama no respondio en {self._timeout}s. Si el modelo no esta "
                f"cargado en memoria, el primer request lo descarga: "
                f"docker compose exec ollama ollama pull {self._model}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"No se pudo conectar con Ollama en {self._base_url}: {exc}"
            ) from exc

        if respuesta.status_code >= 400:
            raise LLMError(
                f"Ollama devolvio {respuesta.status_code}: {respuesta.text[:400]}"
            )

        try:
            return respuesta.json()
        except ValueError as exc:
            raise LLMError("Ollama devolvio una respuesta que no es JSON") from exc

    def _generar(
        self, prompt: str, system: str | None, model: str, json_mode: bool
    ) -> LLMResponse:
        mensajes: list[dict[str, str]] = []
        if system:
            mensajes.append({"role": "system", "content": system})
        mensajes.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": mensajes,
            "stream": False,  # sin esto Ollama devuelve NDJSON y hay que acumularlo a mano
            "options": {
                # num_ctx acotado a propósito: es el mayor consumidor de RAM del
                # host que corre el modelo, y en una laptop sin GPU la diferencia
                # entre 8192 y 2048 es la diferencia entre que corra o no.
                "num_ctx": self._num_ctx,
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        inicio = time.monotonic()
        datos = self._post("/api/chat", payload)
        latencia_ms = int((time.monotonic() - inicio) * 1000)

        texto = (datos.get("message") or {}).get("content", "").strip()
        if not texto:
            # No es un error de red: el modelo contesto y dijo nada. Un modelo
            # chico con la ventana de contexto llena suele terminar así.
            raise LLMError(
                f"El modelo {model} devolvio una respuesta vacia. Suele ser "
                f"contexto insuficiente: proba con un modelo mas chico "
                f"(ollama pull {self._fallback_model}) o subi OLLAMA_NUM_CTX."
            )

        return LLMResponse(
            text=texto,
            model=self._model,
            model_used=model,
            provider=self.name,
            latency_ms=latencia_ms,
            raw={
                "eval_count": datos.get("eval_count"),
                "total_duration": datos.get("total_duration"),
            },
        )

    # -- Interfaz -------------------------------------------------------

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResponse:
        original = self._temperature
        if temperature is not None:
            self._temperature = temperature
        try:
            return self._generar(prompt, system, self._model, json_mode)
        except LLMError as exc:
            # EL FALLBACK. Un modelo 8b que no cabe en RAM es la causa más
            # comun de fallo en una laptop, y sin esto la API de IA está
            # caida aunque haya un 3b Pulled y listo en la misma maquina.
            #
            # Solo cuando hay fallback Y es distinto del principal: reintentar
            # con el mismo modelo solo gasta el doble de tiempo.
            if not self._fallback_model or self._fallback_model == self._model:
                raise

            try:
                respuesta = self._generar(
                    prompt, system, self._fallback_model, json_mode
                )
            except LLMError:
                # Si el fallback también falla, se levanta el error ORIGINAL.
                # El del fallback ("modelo 3b no encontrado") suele ser más
                # confuso que el del modelo grande ("out of memory").
                raise exc

            respuesta.used_fallback = True
            return respuesta
        finally:
            self._temperature = original

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10.0) as client:
                respuesta = client.get(f"{self._base_url}/api/tags")
                respuesta.raise_for_status()
                datos = respuesta.json()
        except httpx.HTTPError:
            # No es un error: un proveedor sin listar no rompe la app. El
            # endpoint de health lo reporta como vacío.
            return []
        return [m.get("name", "") for m in datos.get("models", [])]
