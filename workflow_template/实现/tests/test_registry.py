"""工具注册表:本地/MCP 命名冲突、resolve 路由、tool manifest 聚合。"""
import asyncio

import pytest

from app.mcp_provider import MockMCPProvider
from app.registry import ToolNotFound, ToolRegistry, tool


@tool
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


@tool(description="自定义描述")
def named(x: str) -> str:
    """doc。"""
    return x


def _mcp_with_tools(tools):
    return MockMCPProvider(tools=tools)


def _setup_registry_with_mcp():
    reg = ToolRegistry()
    reg.register_local(add)
    reg.register_mcp("kb", _mcp_with_tools([{
        "name": "search", "description": "search", "inputSchema": {"type": "object"}
    }]))
    asyncio.run(reg.tools_manifest())
    return reg


def test_register_local_and_definition():
    reg = ToolRegistry()
    reg.register_local(add)
    td = reg.local_tools()["add"].definition()
    assert td.function.name == "add"
    params = td.function.parameters
    assert set(params["properties"]) == {"a", "b"}
    assert params["required"] == ["a", "b"]
    assert params["properties"]["a"]["type"] == "integer"


def test_local_default_description_from_docstring():
    reg = ToolRegistry()
    reg.register_local(add)
    assert reg.local_tools()["add"].definition().function.description == "两数相加。"


def test_register_local_name_conflict_raises():
    reg = ToolRegistry()
    reg.register_local(add)
    with pytest.raises(ValueError):
        reg.register_local(add)


def test_resolve_local():
    reg = _setup_registry_with_mcp()
    source, target = asyncio.run(reg.resolve("add"))
    assert source == "local"


def test_resolve_unknown_raises():
    reg = _setup_registry_with_mcp()
    with pytest.raises(ToolNotFound):
        asyncio.run(reg.resolve("not_there"))


def test_register_mcp_and_resolve_with_prefix():
    reg = _setup_registry_with_mcp()
    source, target = asyncio.run(reg.resolve("mcp__kb__search"))
    assert source == "mcp:kb"
    assert isinstance(target, tuple)
    assert target[1] == "search"


def test_manifest_aggregates_local_and_mcp():
    reg = _setup_registry_with_mcp()
    defs = asyncio.run(reg.tools_manifest())
    names = {d.function.name for d in defs}
    assert "add" in names
    assert "mcp__kb__search" in names
