"""协议模型校验:字段、默认值、x- 扩展字段透传、嵌套类型。"""
from app.protocol import (
    AssistantToolCall,
    Message,
    RunInputs,
    ToolDefinition,
    ToolResult,
    TranslatedReply,
    WorkflowEnvelope,
)
from app.splicer import build_system_message, interpolate


def _envelope():
    return WorkflowEnvelope.model_validate({
        "workflow": {"name": "t", "version": "1.0", "max_steps": 5},
        "request": {"model": "mock-smart"},
        "context": {"vars": {"user_name": "李四", "lang": "zh-CN"}, "retrieved": []},
        "inputs": {"system_prompt": "hi {{user_name}}", "user_input": "你好"},
    })


def test_envelope_has_defaults():
    env = WorkflowEnvelope()
    assert env.workflow.name == "default"
    assert env.workflow.max_steps == 8
    assert env.workflow.run_id
    assert env.request.tool_choice == "auto"
    assert env.context.vars == {}
    assert env.context.retrieved == []


def test_envelope_accepts_x_prefixed_fields():
    env = _envelope()
    env.x_trace = {"span": "abc"}
    env.context.x_dept = "support"
    assert env.model_dump()["x_trace"] == {"span": "abc"}
    assert env.context.x_dept == "support"


def test_message_role_only_required():
    m = Message(role="user")
    assert m.content is None
    assert m.tool_calls is None
    assert m.tool_call_id is None


def test_assistant_tool_call_roundtrip():
    call = AssistantToolCall(id="c1", function={"name": "foo", "arguments": '{"a":1}'})
    assert call.function.name == "foo"
    assert call.function.arguments == '{"a":1}'


def test_tool_definition_mcp_style():
    td = ToolDefinition.model_validate({
        "type": "function",
        "function": {
            "name": "mcp__kb__search",
            "description": "search",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        },
    })
    assert td.function.name == "mcp__kb__search"


def test_translated_reply_types():
    text_reply = TranslatedReply(type="text", text="hi", next_action="final_answer")
    assert text_reply.next_action == "final_answer"
    tool_reply = TranslatedReply(type="tool_calls", next_action="dispatch_tools", tool_calls=[])
    assert tool_reply.tool_calls == []


def test_tool_result_error_path():
    r = ToolResult(tool_call_id="c1", name="missing", ok=False, error="not found", content="not found")
    assert r.ok is False
    assert r.error == "not found"


def test_interpolate_substitutes_and_reports_missing():
    text, missing = interpolate("hi {{name}}, today is {{today}}, {{unknown}}", {"name": "A", "today": "B"})
    assert "hi A, today is B" in text
    assert missing == ["unknown"]
    assert "{{unknown}}" in text


def test_build_system_message_injects_rag_and_vars():
    env = _envelope()
    env.inputs.system_prompt = "Hello {{user_name}} ({{lang}})"
    env.context.retrieved = [{"source": "kb", "text": "policy snippet"}]
    msg = build_system_message(env.inputs, env.context)
    assert msg.role == "system"
    assert "Hello 李四 (zh-CN)" in msg.content
    assert "policy snippet" in msg.content
    assert "知识片段" in msg.content


def test_run_inputs_history_default_empty():
    ri = RunInputs(system_prompt="x")
    assert ri.history == []
    assert ri.user_input == ""
