"""基于请求 key 的 TTL 缓存:同一 idempotency_key 短期内重放直接返回上次响应。"""
from __future__ import annotations

import time


class IdempotencyCache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._store: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, response = entry
        if expires_at < time.monotonic():
            del self._store[key]
            return None
        return response

    def put(self, key: str, response: dict) -> None:
        self._store[key] = (time.monotonic() + self.ttl, response)

    def clear(self) -> None:
        self._store.clear()
