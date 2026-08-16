"""模型客户端接口:统一模型调用出口,便于切换后端。

- GatewayModelClient:HTTP 调 model_gateway(或任意 OpenAI 兼容端点)
- ScriptedModelClient:按脚本回放响应,纯离线测试/演示

两者都(异步)返回 OpenAI 兼容 ChatCompletion JSON dict。
"""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.protocol import ModelRequest, ToolDefinition


class ModelClient(ABC):
    """模型侧统一接口:new 一次,context 内多次 request();无需串行。"""

    @abstractmethod
    async def request(self, req: ModelRequest, tools: list[ToolDefinition]) -> dict:
        """组装并发送 /v1/chat/completions,返回模型回复 JSON dict。"""

    async def aclose(self) -> None:
        """释放底层资源;默认无操作。"""


class GatewayModelClient(ModelClient):
    """调 model_gateway / 任意 OpenAI 兼容端点(httpx AsyncClient,sleep_mode 稳定)。"""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "any"
        self._transport_factory = None  # 测试侧可注入 MockTransport
        self._client: httpx.AsyncClient | None = None

    def set_transport(self, transport) -> None:
        """测试用:注入 httpx.MockTransport / 自定义 transport,全程离线。"""
        self._transport_factory = transport
        self._client = httpx.AsyncClient(base_url=self.base_url, transport=transport)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout_adv(),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    def _timeout_adv(self) -> float:
        return getattr(self, "timeout", 60.0)

    async def request(self, req: ModelRequest, tools: list[ToolDefinition]) -> dict:
        payload: dict[str, Any] = {
            "messages": [m.model_dump() for m in req.messages],
        }
        if req.model:
            payload["model"] = req.model
        if req.capability:
            payload["capability"] = req.capability
        if tools:
            payload["tools"] = [{"type": "function", "function": t.function.model_dump()} for t in tools]
        if req.tool_choice is not None:
            payload["tool_choice"] = req.tool_choice
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.response_format is not None:
            payload["response_format"] = req.response_format
        for key, value in req.model_dump(exclude_unset=True, exclude={"model", "capability", "messages", "tools", "tool_choice", "temperature", "max_tokens", "response_format"}).items():
            if not key.startswith("_"):
                payload[key] = value

        client = self._ensure_client()
        resp = await client.post("/v1/chat/completions", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"model gateway error HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class ScriptedModelClient(ModelClient):
    """按脚本序列返回模型回复;用于离线测试与演示。

    脚本元素(逐条出队):
    - str:纯文本回复 → 普通文本 assistant 消息
    - {"text": "..."}:同上(显式)
    - {"tool_calls": [{"id": "c1", "name": "query_order", "arguments": {...}}]}:
      返回发起工具调用的 assistant 消息(OpenAI tool_calls 格式)
    - dict(其他):原样当作完整 OpenAI 响应返回
    """

    def __init__(self, scripts: list):
        self._scripts: list = list(scripts)
        if not self._scripts:
            raise ValueError("scripts must be a non-empty list")

    async def request(self, req: ModelRequest, tools: list[ToolDefinition]) -> dict:
        if not self._scripts:
            raise RuntimeError("ScriptedModelClient: scripts exhausted")
        raw = self._scripts.pop(0)
        return self._materialize(raw)

    def _materialize(self, raw) -> dict:
        if isinstance(raw, str):
            return self._text_response(raw)
        if isinstance(raw, dict):
            if "tool_calls" in raw:
                return self._tool_response(raw["tool_calls"])
            if "text" in raw:
                return self._text_response(raw["text"])
            return raw
        return self._text_response(json.dumps(raw, ensure_ascii=False))

    @staticmethod
    def _text_response(text: str) -> dict:
        return {
            "id": f"chatcmpl-script-{uuid.uuid4().hex[:8]}",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        }

    @staticmethod
    def _tool_response(calls: list[dict]) -> dict:
        tool_calls = [
            {
                "id": c.get("id") or f"call_{i}",
                "type": "function",
                "function": {
                    "name": c["name"],
                    "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False),
                },
            }
            for i, c in enumerate(calls)
        ]
        return {
            "id": f"chatcmpl-script-{uuid.uuid4().hex[:8]}",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
            }],
        }

    async def aclose(self) -> None:
        pass
