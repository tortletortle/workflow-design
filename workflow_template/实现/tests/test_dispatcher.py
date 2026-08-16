"""分发器:本地工具成功 / 工具内部错误 / 未知工具 / MCP 路由。"""
import asyncio

from app.dispatcher import dispatch, results_to_messages
from app.mcp_provider import MockMCPProvider
from app.protocol import TranslatedToolCall
from app.registry import ToolRegistry, tool


@tool
def echo(text: str) -> str:
    """回显。"""
    return text


@tool
def boom(wait: bool = True) -> str:
    """故意报错。"""
    raise RuntimeError("boom")


def _registry_with_tools():
    reg = ToolRegistry()
    reg.register_local(echo)
    reg.register_local(boom)
    reg.register_mcp("kb", MockMCPProvider(tools=[{
        "name": "search", "description": "", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    }], handlers={"search": lambda q: {"hit": q}}))
    asyncio.run(reg.tools_manifest())
    return reg


def test_dispatch_local_success():
    reg = _registry_with_tools()
    results = asyncio.run(dispatch(reg, [TranslatedToolCall(id="c1", name="echo", arguments={"text": "hi"})]))
    assert len(results) == 1
    r = results[0]
    assert r.ok is True
    assert r.content == "hi"
    assert r.source == "local"
    assert r.latency_ms >= 0


def test_dispatch_tool_error_is_captured_not_raised():
    reg = _registry_with_tools()
    results = asyncio.run(dispatch(reg, [TranslatedToolCall(id="c1", name="boom", arguments={})]))
    r = results[0]
    assert r.ok is False
    assert "boom" in (r.error or "")
    assert "tool error" in r.content


def test_dispatch_unknown_tool():
    reg = _registry_with_tools()
    results = asyncio.run(dispatch(reg, [TranslatedToolCall(id="c1", name="missing", arguments={})]))
    r = results[0]
    assert r.ok is False
    assert "tool not found" in (r.error or "")


def test_dispatch_mcp_routed():
    reg = _registry_with_tools()
    results = asyncio.run(dispatch(reg, [TranslatedToolCall(id="c1", name="mcp__kb__search", arguments={"q": "abc"})]))
    r = results[0]
    assert r.ok is True
    assert r.source == "mcp:kb"
    assert "abc" in r.content


def test_dispatch_parallel_multiple():
    reg = _registry_with_tools()
    calls = [
        TranslatedToolCall(id="c1", name="echo", arguments={"text": "a"}),
        TranslatedToolCall(id="c2", name="mcp__kb__search", arguments={"q": "b"}),
    ]
    results = asyncio.run(dispatch(reg, calls))
    assert {r.tool_call_id for r in results} == {"c1", "c2"}
    assert all(r.ok for r in results)


def test_results_to_messages_shape():
    from app.dispatcher import dispatch
    reg = _registry_with_tools()
    results = asyncio.run(dispatch(reg, [TranslatedToolCall(id="c1", name="echo", arguments={"text": "x"})]))
    msgs = results_to_messages(results)
    assert msgs[0].role == "tool"
    assert msgs[0].tool_call_id == "c1"
    assert msgs[0].content == "x"
