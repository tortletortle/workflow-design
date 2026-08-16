"""Mock 适配器:按模型 behavior 模拟正常响应与各类上游错误,网络无关。"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from app.errors import QuotaExceeded, UpstreamError, UpstreamTimeout
from app.providers.base import BaseProvider, ProviderResult
from app.schemas import Message
from app.settings import ModelConfig


def estimate_tokens(text: str | None) -> int:
    """粗略估算 tokens:约 2 字符 = 1 token,外加每条消息 4 token 开销。"""
    return max(1, len(text or "")) // 2 + 4


class MockProvider(BaseProvider):
    def __init__(self, name: str = "mock"):
        super().__init__(name)
        # 每个模型被真实调用的次数(幂等测试用它验证"上游只被调一次")
        self.call_counts: dict[str, int] = defaultdict(int)

    async def call(
        self,
        model: ModelConfig,
        messages: list[Message],
        max_tokens: int | None,
        temperature: float | None,
    ) -> ProviderResult:
        self.call_counts[model.name] += 1
        behavior = model.behavior
        if behavior == "timeout":
            # 模拟上游已超过自身超时阈值后报超时(不等真实时间,保证测试秒级)
            raise UpstreamTimeout(f"mock provider timed out on model '{model.name}'")
        if behavior == "overflow":
            raise QuotaExceeded(
                f"mock model '{model.name}' reports context length exceeded"
            )
        if behavior == "http_502":
            raise UpstreamError(f"mock upstream for '{model.name}' returned HTTP 502")
        if behavior == "network":
            raise UpstreamError(f"connect to mock upstream for '{model.name}' failed")

        started = time.monotonic()
        if model.delay > 0:
            await asyncio.sleep(model.delay)

        last_user = next(
            (m.content or "" for m in reversed(messages) if m.role == "user"), ""
        )
        prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        content = f"[mock:{model.name}] {last_user[:80]}".strip()
        completion_tokens = estimate_tokens(content)
        return ProviderResult(
            content=content,
            model=model.name,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            latency_ms=int((time.monotonic() - started) * 1000),
        )
