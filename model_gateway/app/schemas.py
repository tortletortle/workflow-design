"""对外请求/响应模型:OpenAI 兼容 + 网关扩展字段。"""
from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

Capability = Literal["low_cost", "low_latency", "high_quality", "long_context", "vision"]


class Message(BaseModel):
    role: str
    content: str | None = None


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: str | None = None
    capability: Capability | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    # stream 仅接收不透传:网关当前只返回非流式响应(见接口文档「已知限制」)
    stream: bool = False
    request_id: str | None = None
    idempotency_key: str | None = None


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ResponseMetadata(BaseModel):
    routed_model: str
    routed_provider: str
    mode: str
    latency_ms: int
    request_id: str


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage
    metadata: ResponseMetadata


def new_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"
