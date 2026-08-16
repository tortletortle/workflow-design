"""引擎端到端:工具→回填→终答;多轮;max_steps 截断;错误收尾;钩子触发。"""
import asyncio
import pytest

from app.engine import AgentEngine
from app.hooks import HookRegistry
from app.mcp_provider import MockMCPProvider
from app.model_client import ScriptedModelClient
from app.protocol import WorkflowEnvelope
from app.registry import ToolRegistry, tool


@tool
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


def _registry_with_add():
    reg = ToolRegistry()
    reg.register_local(add)
    reg.register_mcp("kb", MockMCPProvider(tools=[{
        "name": "search", "description": "", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}
    }], handlers={"search": lambda q: {"hit": q}}))
    asyncio.run(reg.tools_manifest())
    return reg


def _envelope(user_input="算 3+5 是多少"):
    return WorkflowEnvelope.model_validate({
        "workflow": {"name": "calc", "version": "1.0", "max_steps": 8},
        "request": {"model": "mock-smart"},
        "context": {"vars": {}, "retrieved": []},
        "inputs": {"system_prompt": "你是计算助手,可用 add 工具。", "user_input": user_input},
    })


def test_engine_tool_then_final_two_steps():
    scripts = [
        {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 3, "b": 5}}]},
        {"text": "3+5 等于 8"},
    ]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add())
    result = asyncio.run(engine.run(_envelope()))
    assert result.status == "final"
    assert result.final_text == "3+5 等于 8"
    assert len(result.steps) == 2
    assert result.steps[0].reply_type == "tool_calls"
    assert len(result.steps[0].tool_results) == 1
    assert result.steps[0].tool_results[0].content == "8"
    assert result.steps[1].reply_type == "text"


def test_engine_transcript_contains_tool_roundtrip():
    scripts = [
        {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 1, "b": 2}}]},
        {"text": "3"},
    ]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add())
    result = asyncio.run(engine.run(_envelope()))
    roles = [m.role for m in result.transcript]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    tool_msg = next(m for m in result.transcript if m.role == "tool")
    assert tool_msg.tool_call_id == "c1"
    assert tool_msg.content == "3"


def test_engine_direct_text_no_tool_calls():
    scripts = [{"text": "直接回答"}]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add())
    result = asyncio.run(engine.run(_envelope()))
    assert result.status == "final"
    assert len(result.steps) == 1
    assert result.steps[0].reply_type == "text"
    assert len(result.transcript) == 3  # system + user + assistant


def test_engine_max_steps_truncation():
    # 每轮都要工具,永不收敛
    env = _envelope()
    env.workflow.max_steps = 2
    scripts = [
        {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 1, "b": 1}}]},
        {"tool_calls": [{"id": "c2", "name": "add", "arguments": {"a": 1, "b": 1}}]},
        {"text": "should not reach"},
    ]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add())
    result = asyncio.run(engine.run(env))
    assert result.status == "max_steps"
    assert len(result.steps) >= 2


def test_engine_error_status_on_model_failure():
    class BoomModel:
        async def request(self, req, tools):
            raise RuntimeError("upstream down")
        async def aclose(self):
            pass
    engine = AgentEngine(BoomModel(), _registry_with_add())
    result = asyncio.run(engine.run(_envelope()))
    assert result.status == "error"
    assert "upstream down" in (result.error or "")


def test_engine_error_status_on_unknown_tool():
    scripts = [{"tool_calls": [{"id": "c1", "name": "missing_tool", "arguments": {}}]}]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add())
    result = asyncio.run(engine.run(_envelope()))
    # 未知工具不抛异常,只回填错误文本;脚本随后耗尽 → RuntimeError → error 状态
    assert result.status == "error"


def test_hooks_fire_in_order():
    events = []
    h2 = HookRegistry()
    h2.on("on_start", lambda envelope: events.append("on_start"))
    h2.on("pre_model_call", lambda envelope, request: events.append(f"pre_model_call:{envelope.workflow.step}"))
    h2.on("post_model_call", lambda envelope, response: events.append("post_model_call"))
    h2.on("pre_dispatch", lambda envelope, tool_calls: events.append("pre_dispatch"))
    h2.on("post_dispatch", lambda envelope, tool_results: events.append("post_dispatch"))
    h2.on("on_finish", lambda envelope, result, reason: events.append(f"on_finish:{reason}"))

    scripts = [
        {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 1, "b": 2}}]},
        {"text": "done"},
    ]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add(), hooks=h2)
    asyncio.run(engine.run(_envelope()))
    assert events == [
        "on_start",
        "pre_model_call:1", "post_model_call", "pre_dispatch", "post_dispatch",
        "pre_model_call:2", "post_model_call",
        "on_finish:final",
    ]


def test_engine_mcp_tool_in_loop():
    scripts = [
        {"tool_calls": [{"id": "c1", "name": "mcp__kb__search", "arguments": {"q": "x"}}]},
        {"text": "查到了相关片段。"},
    ]
    engine = AgentEngine(ScriptedModelClient(scripts), _registry_with_add())
    result = asyncio.run(engine.run(_envelope()))
    assert result.status == "final"
    assert result.steps[0].tool_results[0].source == "mcp:kb"
    assert "x" in result.steps[0].tool_results[0].content
