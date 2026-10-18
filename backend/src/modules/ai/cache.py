"""Caché en memoria de respuestas de IA con TTL."""

from __future__ import annotations

import hashlib
import time
from typing import Any


class AICache:
    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}

    def _key(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def get(self, prompt: str) -> Any | None:
        k = self._key(prompt)
        entry = self._store.get(k)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > self.ttl:
            del self._store[k]
            return None
        return value

    def set(self, prompt: str, value: Any) -> None:
        if len(self._store) >= self.max_entries:
            # evict más antiguo
            oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
            del self._store[oldest]
        self._store[self._key(prompt)] = (time.time(), value)
