"""分发器:翻译产物里的 tool_calls → 逐条路由到本地工具 / MCP 服务执行。

设计约束:
- 权限审核:policy 非空时,先过 PermissionPolicy(工具级权限 + 命令种类),
  拒绝 → ToolResult(ok=False, error="permission denied: ...")回填给模型
- 单条工具失败不中断整个 run:结果记 ok=False,错误文本回填给模型自行纠错
- 每条结果带 source(local / mcp:<server>)与 latency_ms,便于观测
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.mcp_provider import content_to_text
from app.permissions import PermissionPolicy
from app.protocol import Message, ToolResult, TranslatedToolCall
from app.registry import ToolRegistry, ToolNotFound


def _permission_of(source: str, target: Any) -> str:
    """从执行体取权限声明:本地 LocalTool.permission / MCP provider.permission。"""
    if source == "local":
        return getattr(target, "permission", "default")
    provider = target[0] if isinstance(target, tuple) else target
    return getattr(provider, "permission", "mcp")


async def dispatch_call(
    registry: ToolRegistry, call: TranslatedToolCall, policy: PermissionPolicy | None = None
) -> ToolResult:
    started = time.monotonic()
    try:
        source, target = await registry.resolve(call.name)
    except ToolNotFound as e:
        return ToolResult(
            tool_call_id=call.id, name=call.name, ok=False,
            error=f"tool not found: {e}", content=str(e), source=call.source,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    if policy is not None:
        decision = policy.check(call.name, _permission_of(source, target), call.arguments)
        if not decision.allowed:
            reason = f"permission denied: {decision.reason}"
            return ToolResult(
                tool_call_id=call.id, name=call.name, ok=False,
                error=reason, content=reason, source=source,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    try:
        if source == "local":
            out = await target.invoke(call.arguments)
            content = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
        else:
            provider, tool_name = target
            content = content_to_text(await provider.call_tool(tool_name, call.arguments))
        return ToolResult(
            tool_call_id=call.id, name=call.name, ok=True,
            content=content, source=source,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as e:  # 工具内部错误 → 回填错误文本,模型可自行重试/换路
        return ToolResult(
            tool_call_id=call.id, name=call.name, ok=False,
            error=str(e), content=f"tool error: {e}", source=source,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


async def dispatch(
    registry: ToolRegistry,
    calls: list[TranslatedToolCall],
    policy: PermissionPolicy | None = None,
) -> list[ToolResult]:
    """并发分发全部 tool_calls;policy 非空时先逐条权限审核。"""
    return list(
        await asyncio.gather(*(dispatch_call(registry, c, policy) for c in calls))
    )


def results_to_messages(results: list[ToolResult]) -> list[Message]:
    """执行结果 → 回填给模型的 tool 消息(下一轮拼接素材)。"""
    return [
        Message(role="tool", tool_call_id=r.tool_call_id, name=r.name, content=r.content)
        for r in results
    ]
