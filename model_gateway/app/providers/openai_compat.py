"""OpenAI 兼容适配器:httpx AsyncClient 直连各厂商的 /chat/completions。

一套实现同时覆盖 OpenAI、DeepSeek、Qwen(兼容模式)、Moonshot 等
所有 OpenAI 协议兼容的 provider;支持按 provider 配置代理。
"""
from __future__ import annotations

import os
import time

import httpx

from app.errors import UpstreamError, UpstreamTimeout
from app.providers.base import BaseProvider, ProviderResult
from app.providers.mock import estimate_tokens
from app.schemas import Message
from app.settings import ModelConfig, ProviderConfig


class OpenAICompatProvider(BaseProvider):
    def __init__(
        self,
        name: str,
        cfg: ProviderConfig,
        api_key: str,
        upstream_timeout: float,
    ):
        super().__init__(name)
        proxy = os.environ.get(cfg.proxy_env) if cfg.proxy_env else None
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/"),
            timeout=upstream_timeout,
            proxy=proxy or None,
        )
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def call(
        self,
        model: ModelConfig,
        messages: list[Message],
        max_tokens: int | None,
        temperature: float | None,
    ) -> ProviderResult:
        payload: dict = {
            "model": model.name,
            "messages": [m.model_dump() for m in messages],
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        started = time.monotonic()
        try:
            resp = await self._client.post(
                "/chat/completions", json=payload, headers=self._headers
            )
        except httpx.TimeoutException as e:
            raise UpstreamTimeout(f"upstream '{self.name}' timed out: {e}") from e
        except httpx.HTTPError as e:
            raise UpstreamError(f"cannot reach upstream '{self.name}': {e}") from e

        if resp.status_code >= 400:
            raise UpstreamError(
                f"upstream '{self.name}' returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError) as e:
            raise UpstreamError(
                f"upstream '{self.name}' returned malformed payload: {e}"
            ) from e

        prompt_tokens = usage.get("prompt_tokens") or sum(
            estimate_tokens(m.content) for m in messages
        )
        completion_tokens = usage.get("completion_tokens") or estimate_tokens(content)
        return ProviderResult(
            content=content,
            model=model.name,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": usage.get("total_tokens")
                or (prompt_tokens + completion_tokens),
            },
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
