"""翻译器:把模型回复(OpenAI 兼容格式)翻译成结构化动作,供引擎决策。

- 有 tool_calls → TranslatedReply(type=tool_calls, next_action=dispatch_tools),
  并根据工具注册表给每条调用标注 source(local / mcp:<server>)
- 纯文本 → TranslatedReply(type=text, next_action=final_answer)
- 空回复 → type=empty(防御,按 final 处理)
"""
from __future__ import annotations

import json
from typing import Any

from app.protocol import (
    AssistantToolCall,
    Message,
    TranslatedReply,
    TranslatedToolCall,
)
from app.registry import ToolRegistry


def _parse_arguments(raw: Any) -> dict:
    """OpenAI 的 arguments 是 JSON 字符串;兼容已解析的 dict。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": str(raw)}


def translate_reply(
    response: dict[str, Any] | None,
    registry: ToolRegistry | None = None,
) -> TranslatedReply:
    """模型原始响应 → TranslatedReply。response 为 /v1/chat/completions 的 JSON 体。"""
    if not response:
        return TranslatedReply(type="empty", next_action="final_answer")

    choices = response.get("choices") or []
    if not choices:
        return TranslatedReply(type="empty", next_action="final_answer", raw=response)

    choice = choices[0]
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason")
    raw_calls = message.get("tool_calls") or []

    if raw_calls:
        calls: list[TranslatedToolCall] = []
        for rc in raw_calls:
            fn = rc.get("function") or {}
            name = fn.get("name", "")
            calls.append(
                TranslatedToolCall(
                    id=rc.get("id") or f"call_{len(calls)}",
                    name=name,
                    arguments=_parse_arguments(fn.get("arguments")),
                )
            )
        if registry is not None:
            for c in calls:
                c.source = _resolve_source_sync(c.name, registry)
        return TranslatedReply(
            type="tool_calls",
            text=message.get("content"),
            tool_calls=calls,
            finish_reason=finish_reason,
            next_action="dispatch_tools",
            raw=response,
        )

    text = message.get("content")
    if text:
        return TranslatedReply(
            type="text",
            text=text,
            finish_reason=finish_reason,
            next_action="final_answer",
            raw=response,
        )
    return TranslatedReply(
        type="empty", finish_reason=finish_reason, next_action="final_answer", raw=response
    )


def _resolve_source_sync(name: str, registry: ToolRegistry) -> str | None:
    """给 tool_call 标注来源;MCP 前缀可直接同步判别,本地名查注册表。"""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        return f"mcp:{parts[1]}" if len(parts) == 3 else None
    if name in registry.local_tools():
        return "local"
    return None


def assistant_message(reply: TranslatedReply) -> Message:
    """翻译产物 → 待追加进 transcript 的 assistant 消息(保留 tool_calls 原样)。

    OpenAI 协议要求 tool_calls.function.arguments 为 JSON 字符串,
    这里统一把 dict 序列化,回填到下一轮请求时模型能正确解析。
    """
    tool_calls = None
    if reply.type == "tool_calls":
        tool_calls = [
            AssistantToolCall(
                id=c.id,
                function={
                    "name": c.name,
                    "arguments": json.dumps(c.arguments, ensure_ascii=False),  # type: ignore[arg-type]
                },
            )
            for c in reply.tool_calls
        ]
    return Message(role="assistant", content=reply.text, tool_calls=tool_calls)
