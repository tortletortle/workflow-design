"""生命周期钩子:协议 envelope.hooks 声明,引擎按事件回调。

事件:on_start / pre_model_call / post_model_call / pre_dispatch /
post_dispatch / on_finish / on_error。钩子可同步或 async;
返回值被忽略(只做观察、埋点、审计,不改动数据流)。
"""
from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

HOOK_EVENTS = (
    "on_start",
    "pre_model_call",
    "post_model_call",
    "pre_dispatch",
    "post_dispatch",
    "on_finish",
    "on_error",
)

HookFn = Callable[..., Any]


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, list[HookFn]] = {e: [] for e in HOOK_EVENTS}

    def on(self, event: str, fn: HookFn) -> None:
        if event not in self._hooks:
            raise ValueError(f"unknown hook event '{event}'; valid: {HOOK_EVENTS}")
        self._hooks[event].append(fn)

    def events(self) -> dict[str, list[HookFn]]:
        return self._hooks

    async def fire(self, event: str, **payload: Any) -> list[Any]:
        """触发事件;记录每个钩子的返回值(便于测试断言)。"""
        results = []
        for fn in self._hooks.get(event, []):
            out = fn(**payload)
            if inspect.isawaitable(out):
                out = await out
            results.append(out)
        return results
