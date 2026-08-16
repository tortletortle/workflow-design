"""路由与编排:显式 model 直连;capability 智能选型;含幂等/限流/配额/超时全链路。"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from app.capabilities import rank_models
from app.errors import (
    ClientTotalTimeout,
    GatewayError,
    ModelNotFound,
    NoModelForCapability,
    NoModelSpecified,
    ProviderDisabled,
    QuotaExceeded,
)
from app.idempotency import IdempotencyCache
from app.providers.base import BaseProvider, ProviderResult
from app.providers.mock import MockProvider, estimate_tokens
from app.providers.openai_compat import OpenAICompatProvider
from app.ratelimit import ConcurrencyLimiter
from app.schemas import ChatRequest
from app.settings import GatewayConfig, ModelConfig


class Gateway:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self.models: dict[str, ModelConfig] = {m.name: m for m in config.models}
        self.providers: dict[str, BaseProvider] = self._build_providers(config)
        self.limits = config.limits
        self.idempotency = IdempotencyCache(config.idempotency_ttl)
        self.limiter = ConcurrencyLimiter(config.limits.max_concurrency)

    @staticmethod
    def _build_providers(config: GatewayConfig) -> dict[str, BaseProvider]:
        providers: dict[str, BaseProvider] = {}
        for name, cfg in config.providers.items():
            if cfg.api_key_env is None:
                providers[name] = MockProvider(name)
            else:
                api_key = os.environ.get(cfg.api_key_env, "")
                providers[name] = OpenAICompatProvider(
                    name, cfg, api_key, config.limits.upstream_timeout
                )
        return providers

    def provider_enabled(self, provider_name: str) -> bool:
        """mock provider 恒可用;真实 provider 需 real 模式且配了 api key。"""
        cfg = self.config.providers[provider_name]
        if cfg.api_key_env is None:
            return True
        if self.config.mode == "mock":
            return False
        return bool(os.environ.get(cfg.api_key_env))

    # ---- 路由 ----

    def resolve(self, model_name: str | None, capability: str | None) -> ModelConfig:
        if model_name:
            model = self.models.get(model_name)
            if model is None:
                raise ModelNotFound(f"model '{model_name}' is not configured")
            if not self.provider_enabled(model.provider):
                raise ProviderDisabled(
                    f"provider '{model.provider}' for model '{model_name}' is not enabled "
                    f"(mode={self.config.mode}; missing api key or switch GW_MODE=real)"
                )
            return model

        if capability:
            candidates = [
                m
                for m in self.models.values()
                if capability in m.capabilities and self.provider_enabled(m.provider)
            ]
            if not candidates:
                raise NoModelForCapability(
                    f"no enabled model matches capability '{capability}'"
                )
            return rank_models(candidates, capability)[0]

        raise NoModelSpecified(
            "request must carry either 'model' (explicit routing) "
            "or 'capability' (smart routing)"
        )

    # ---- 配额 ----

    def check_quota(self, model: ModelConfig, req: ChatRequest) -> None:
        requested_output = req.max_tokens or 0
        if requested_output > model.max_output:
            raise QuotaExceeded(
                f"max_tokens={requested_output} exceeds model '{model.name}' "
                f"output limit {model.max_output}"
            )
        input_tokens = sum(estimate_tokens(m.content) for m in req.messages)
        if input_tokens + requested_output > model.max_context:
            raise QuotaExceeded(
                f"estimated {input_tokens} input tokens (+{requested_output} output) "
                f"exceeds model '{model.name}' context limit {model.max_context}"
            )

    # ---- 主链路 ----

    async def handle_chat(self, req: ChatRequest) -> dict:
        request_id = req.request_id or uuid.uuid4().hex

        if req.idempotency_key:
            cached = self.idempotency.get(req.idempotency_key)
            if cached is not None:
                return cached

        self.limiter.acquire()
        try:
            model = self.resolve(req.model, req.capability)
            self.check_quota(model, req)
            provider = self.providers[model.provider]
            try:
                result = await asyncio.wait_for(
                    provider.call(model, req.messages, req.max_tokens, req.temperature),
                    timeout=self.limits.request_timeout,
                )
            except asyncio.TimeoutError as e:
                raise ClientTotalTimeout(
                    f"request exceeded total timeout of "
                    f"{self.limits.request_timeout}s"
                ) from e
            response = self._build_response(result, model, request_id)
            if req.idempotency_key:
                self.idempotency.put(req.idempotency_key, response)
            return response
        finally:
            self.limiter.release()

    def _build_response(
        self, result: ProviderResult, model: ModelConfig, request_id: str
    ) -> dict:
        from app.schemas import new_completion_id

        return {
            "id": new_completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model.name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": result.usage,
            "metadata": {
                "routed_model": model.name,
                "routed_provider": model.provider,
                "mode": self.config.mode,
                "latency_ms": result.latency_ms,
                "request_id": request_id,
            },
        }

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()
