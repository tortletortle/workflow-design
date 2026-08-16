"""简单内存并发限流:在途请求数超过上限立即拒绝(429),不排队。"""
from __future__ import annotations

from app.errors import RateLimitedError


class ConcurrencyLimiter:
    def __init__(self, limit: int):
        self.limit = limit
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def acquire(self) -> None:
        if self._active >= self.limit:
            raise RateLimitedError(
                f"concurrency limit reached ({self._active}/{self.limit} in flight)"
            )
        self._active += 1

    def release(self) -> None:
        self._active = max(0, self._active - 1)
