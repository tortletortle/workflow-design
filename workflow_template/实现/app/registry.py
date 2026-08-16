"""工具注册表:本地函数 + MCP 服务 → 统一工具视图(OpenAI function 格式)。

公开命名规则:
- 本地工具:注册名原样
- MCP 工具:mcp__{server}__{tool}(跨服务重名不冲突,前缀即路由信息)
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

from app.mcp_provider import BaseMCPProvider, provider_from_config
from app.protocol import FunctionDefinition, ToolDefinition


class ToolNotFound(Exception):
    pass


class LocalTool:
    """@tool 装饰器注册的本地工具:定义由签名 + docstring 自动生成。

    permission:创建时声明的所需权限(默认 "default");
    由 PermissionPolicy.allowed_permissions 决定是否授予(见 app/permissions.py)。
    """

    def __init__(
        self,
        fn: Callable,
        name: str | None = None,
        description: str | None = None,
        permission: str | None = None,
    ):
        self.fn = fn
        self.name = name or fn.__name__
        self.description = description or inspect.getdoc(fn) or ""
        self.permission = permission or "default"

    def definition(self) -> ToolDefinition:
        properties: dict[str, Any] = {}
        required: list[str] = []
        sig = inspect.signature(self.fn)
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            properties[pname] = {"type": self._json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        return ToolDefinition(
            function=FunctionDefinition(
                name=self.name,
                description=self.description,
                parameters={"type": "object", "properties": properties, "required": required},
            )
        )

    @staticmethod
    def _json_type(annotation: Any) -> str:
        """注解 → JSON Schema 类型;兼容 `from __future__ import annotations` 的字符串注解。"""
        type_map = {
            str: "string", int: "integer", float: "number", bool: "boolean",
            dict: "object", list: "array",
        }
        name_map = {
            "str": "string", "int": "integer", "float": "number", "bool": "boolean",
            "dict": "object", "list": "array", "list[str]": "array", "list[int]": "array",
        }
        if annotation is inspect.Parameter.empty:
            return "string"
        if isinstance(annotation, type):
            return type_map.get(annotation, "string")
        # 字符串注解:list[str] 取基础名 list;未知类型按 string
        base = str(annotation).split("[")[0].strip()
        return name_map.get(base, "string")

    async def invoke(self, arguments: dict) -> Any:
        out = self.fn(**arguments)
        if inspect.isawaitable(out):
            out = await out
        return out


def tool(fn=None, *, name: str | None = None, description: str | None = None, permission: str | None = None):
    """装饰器:@tool 或 @tool(permission="shell", ...) 注册本地工具。

    permission:权限声明,分发时由 PermissionPolicy 审核;不声明视为 "default"。
    """
    def wrap(f):
        t = LocalTool(f, name=name, description=description, permission=permission)
        t.definition()  # 注册期即校验签名可转换
        return t

    return wrap(fn) if fn is not None else wrap


class ToolRegistry:
    def __init__(self) -> None:
        self._local: dict[str, LocalTool] = {}
        self._mcp: dict[str, BaseMCPProvider] = {}

    # ---- 注册 ----

    def register_local(self, t: LocalTool) -> str:
        if t.name in self._local or self._find_mcp_tool(t.name)[0]:
            raise ValueError(f"tool name conflict: '{t.name}'")
        self._local[t.name] = t
        return t.name

    def register_mcp(self, server_name: str, provider: BaseMCPProvider) -> None:
        self._mcp[server_name] = provider

    def attach_mcp_config(self, config: dict, source_path: str | None = None) -> list[str]:
        """按 mcp_servers.json 装配:只注册 enabled=true 的服务。

        返回已注册的服务名列表。库中真实开源服务默认全部 enabled=false,
        需要哪个就把它改成 true(并按 needs 配好 env/args)。
        source_path 仅用于报错提示。
        """
        registered: list[str] = []
        for cfg in config.get("servers", []):
            if not cfg.get("enabled", False):
                continue
            name = cfg.get("name")
            if not name:
                raise ValueError(f"MCP config entry missing 'name' ({source_path or 'inline'})")
            provider = provider_from_config(cfg)
            provider.permission = cfg.get("permission", provider.permission)
            self.register_mcp(name, provider)
            registered.append(name)
        return registered

    # ---- 查询 ----

    def _find_mcp_tool(self, public_name: str) -> tuple[str | None, str | None]:
        """mcp__{server}__{tool} → (server, tool);不匹配前缀返回 (None, None)。"""
        if not public_name.startswith("mcp__"):
            return None, None
        parts = public_name.split("__", 2)
        if len(parts) != 3:
            return None, None
        return parts[1], parts[2]

    async def resolve(self, public_name: str) -> tuple[str, Any]:
        """公开名 → (source, 执行体)。source: 'local' 或 'mcp:<server>'。

        mcp 分支返回 (provider, 原始工具名);调用方需先 await 过
        tools_manifest(引擎启动即做)以填充 provider 工具缓存。
        """
        if public_name in self._local:
            return "local", self._local[public_name]

        server, tool_name = self._find_mcp_tool(public_name)
        if server and server in self._mcp:
            provider = self._mcp[server]
            tools = await provider.tools()
            if any(t["name"] == tool_name for t in tools):
                return f"mcp:{server}", (provider, tool_name)

        raise ToolNotFound(f"tool '{public_name}' is not registered")

    async def tools_manifest(self) -> list[ToolDefinition]:
        """聚合全部工具定义(发给模型的 tools 数组)。"""
        defs = [t.definition() for t in self._local.values()]
        for server, provider in self._mcp.items():
            defs.extend(await provider.tool_definitions(server))
        return defs

    def local_tools(self) -> dict[str, LocalTool]:
        return dict(self._local)
