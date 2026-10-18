"""Chat conversacional con historial en memoria."""

from __future__ import annotations

from src.core.llm.base import LLMClient
from src.core.llm.factory import get_llm_client


class ChatSession:
    def __init__(self, system_prompt: str = ""):
        self.messages: list[dict[str, str]] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    def ask(self, user_msg: str, client: LLMClient | None = None) -> str:
        client = client or get_llm_client()
        self.messages.append({"role": "user", "content": user_msg})
        reply = client.chat(self.messages)
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def reset(self):
        self.messages = [m for m in self.messages if m["role"] == "system"]
