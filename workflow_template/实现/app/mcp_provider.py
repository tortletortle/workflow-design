"""MCP 接入:统一 Provider 接口 + 官方 SDK(stdio)实现 + Mock(离线测试/演示)。

MCP tool 的 name/description/inputSchema 直接映射为 OpenAI function 定义,
模型侧无感知;分发器通过 call_tool(name, arguments) 执行。
真实接入需要 `uv sync --extra mcp` 安装官方 SDK。
新增传输方式(http/sse/自定义)实现 BaseMCPProvider 即可挂入注册表。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app.protocol import FunctionDefinition, ToolDefinition


class BaseMCPProvider(ABC):
    """MCP 服务 Provider 接口。子类实现 list_tools / call_tool;

    tools() 带内存缓存(条目少、运行期不变);has_tool 依赖缓存,
    引擎启动时聚合 tools_manifest 会先行填充。

    工具级权限:类属性 permission(默认 "mcp"),可被 mcp_servers.json
    条目的 "permission" 字段覆盖,由 PermissionPolicy 审核授予。
    """

    _tools_cache: list[dict[str, Any]] | None = None
    permission: str = "mcp"

    @abstractmethod
    async def list_tools(self) -> list[dict[str, Any]]:
        """返回 [{name, description, inputSchema}],与 MCP tools/list 对齐。"""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """执行工具,返回 MCP call_tool 的 content 结构(list[{type, text}])或等价值。"""

    async def tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is None:
            self._tools_cache = await self.list_tools()
        return self._tools_cache

    def has_tool(self, name: str) -> bool:
        return self._tools_cache is not None and any(
            t["name"] == name for t in self._tools_cache
        )

    async def tool_definitions(self, server_name: str) -> list[ToolDefinition]:
        """MCP 工具 → OpenAI function 定义,公开名加 mcp__{server}__ 前缀。"""
        return [
            ToolDefinition(
                function=FunctionDefinition(
                    name=f"mcp__{server_name}__{t['name']}",
                    description=t.get("description", ""),
                    parameters=t.get("inputSchema") or {"type": "object", "properties": {}},
                )
            )
            for t in await self.tools()
        ]


def content_to_text(content: Any) -> str:
    """MCP content 结构 → 纯文本,便于回填 tool 消息。

    兼容三种形态:dict(list[{type, text}])、str、官方 SDK 的
    TextContent 对象(带 .text 属性)。
    """
    if isinstance(content, str):
        return content
    items = content if isinstance(content, list) else [content]
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif hasattr(item, "text"):
            # 官方 SDK:mcp.types.TextContent
            parts.append(getattr(item, "text") or "")
        else:
            parts.append(json.dumps(item, ensure_ascii=False, default=str))
    return "\n".join(p for p in parts if p)


class MockMCPProvider(BaseMCPProvider):
    """离线 MCP:按配置声明工具,call_tool 调用本地 handler(默认 JSON 回显)。"""

    def __init__(self, tools: list[dict[str, Any]], handlers: dict[str, Any] | None = None):
        self._tools = tools
        self._handlers = handlers or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        import inspect

        handler = self._handlers.get(name)
        if handler is None:
            result: Any = {"echo": arguments}
        else:
            result = handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
        return [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]


def _windows_launch(argv: list[str]) -> list[str]:
    """Windows 上 npx/npm/uvx 等是 .cmd 脚本,create_subprocess_exec 找不到;
    自动包一层 cmd /c(配置里直接写 command=npx 也能跑)。"""
    import os
    import shutil

    if os.name != "nt" or not argv:
        return argv
    head = argv[0]
    resolved = shutil.which(head)
    if resolved and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved, *argv[1:]]
    if head.lower().endswith((".cmd", ".bat")) or head.lower() in ("npx", "npm", "uvx", "pnpm", "yarn"):
        return ["cmd", "/c", *argv]
    return argv


class StdioMCPProvider(BaseMCPProvider):
    """官方 mcp SDK 的 stdio 传输实现(依赖 optional extra `mcp`)。"""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        self._params = {"command": command, "args": args, "env": env or {}}
        self._session = None
        self._stack = None
    async def _ensure_session(self):
        if self._session is not None:
            return self._session
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:
            raise RuntimeError(
                "官方 MCP SDK 未安装:请执行 `uv sync --extra mcp` 后重试"
            ) from e

        from contextlib import AsyncExitStack

        # Windows 上 npx 等 .cmd 脚本需包一层 cmd /c;类 Unix 原样返回
        wrapped = _windows_launch([self._params["command"], *self._params["args"]])
        params = StdioServerParameters(
            command=wrapped[0], args=wrapped[1:], env=self._params["env"]
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self._session

    async def list_tools(self) -> list[dict[str, Any]]:
        session = await self._ensure_session()
        result = await session.list_tools()
        tools = []
        for t in result.tools:
            # 官方 SDK Tool 模型:snake_case 字段(input_schema/description),alias 是 inputSchema
            schema = getattr(t, "input_schema", None)
            if schema is None:
                schema = getattr(t, "inputSchema", None)
            tools.append({
                "name": t.name,
                "description": getattr(t, "description", None) or "",
                "inputSchema": schema or {"type": "object", "properties": {}},
            })
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = await self._ensure_session()
        result = await session.call_tool(name, arguments)
        return getattr(result, "content", result)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None


def provider_from_config(cfg: dict[str, Any]) -> BaseMCPProvider:
    """mcp_servers.json 的单条 server 配置 → Provider 实例。

    transport:
    - mock:本地声明式工具(离线测试/演示)
    - stdio:官方 SDK 子进程(需 `uv sync --extra mcp`;Windows 下 npx/uvx
      需包 cmd /c,见 templates/mcp_servers.json 顶部注释)
    """
    transport = cfg.get("transport")
    if transport == "mock":
        return MockMCPProvider(tools=cfg.get("tools", []))
    if transport == "stdio":
        return StdioMCPProvider(
            command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env")
        )
    raise ValueError(
        f"unknown MCP transport '{transport}' for server '{cfg.get('name')}' "
        f"(expected 'mock' or 'stdio')"
    )
