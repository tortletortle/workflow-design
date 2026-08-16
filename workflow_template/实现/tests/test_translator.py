"""翻译器:文本分流、工具调用分流、空回复、source 标注、arguments 解析。"""
from app.registry import ToolRegistry, tool
from app.translator import _parse_arguments, assistant_message, translate_reply


@tool
def local_fn(x: int) -> int:
    """本地工具。"""
    return x * 2


def _text_response(text="hello"):
    return {"choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}]}


def _tool_response(name="query_order", args_str='{"order_id":"A1"}', call_id="c1"):
    return {"choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": None,
                                     "tool_calls": [{"id": call_id, "type": "function",
                                                     "function": {"name": name, "arguments": args_str}}]}}]}


def test_translate_text_final():
    reply = translate_reply(_text_response("done"))
    assert reply.type == "text"
    assert reply.text == "done"
    assert reply.next_action == "final_answer"


def test_translate_tool_calls_dispatch():
    registry = ToolRegistry()
    registry.register_local(local_fn)
    reply = translate_reply(_tool_response())
    assert reply.type == "tool_calls"
    assert reply.next_action == "dispatch_tools"
    assert len(reply.tool_calls) == 1
    c = reply.tool_calls[0]
    assert c.id == "c1"
    assert c.name == "query_order"
    assert c.arguments == {"order_id": "A1"}


def test_translate_empty_response():
    assert translate_reply(None).type == "empty"
    assert translate_reply({}).type == "empty"
    assert translate_reply({"choices": []}).type == "empty"


def test_translate_source_local_marked():
    registry = ToolRegistry()
    registry.register_local(local_fn)
    reply = translate_reply(_tool_response(name="local_fn", args_str='{"x":3}'), registry=registry)
    assert reply.tool_calls[0].source == "local"


def test_translate_source_mcp_prefix_parsed():
    registry = ToolRegistry()
    reply = translate_reply(
        _tool_response(name="mcp__kb__search", args_str='{"query":"q"}'), registry=registry
    )
    assert reply.tool_calls[0].source == "mcp:kb"


def test_parse_arguments_fallback_on_bad_json():
    assert _parse_arguments(None) == {}
    assert _parse_arguments("") == {}
    assert _parse_arguments({"a": 1}) == {"a": 1}
    assert _parse_arguments('{"a": 1}') == {"a": 1}
    assert _parse_arguments("not json") == {"_raw": "not json"}


def test_assistant_message_arguments_is_json_string():
    import json

    from app.protocol import TranslatedReply, TranslatedToolCall

    reply = TranslatedReply(
        type="tool_calls",
        next_action="dispatch_tools",
        tool_calls=[TranslatedToolCall(id="c1", name="foo", arguments={"a": 1})],
    )
    msg = assistant_message(reply)
    assert msg.role == "assistant"
    assert msg.tool_calls is not None
    assert msg.tool_calls[0].function.name == "foo"
    # arguments 在 OpenAI 协议里必须是 JSON 字符串
    assert isinstance(msg.tool_calls[0].function.arguments, str)
    assert json.loads(msg.tool_calls[0].function.arguments) == {"a": 1}


def test_assistant_text_message_no_tool_calls():
    from app.protocol import TranslatedReply

    msg = assistant_message(TranslatedReply(type="text", text="hi", next_action="final_answer"))
    assert msg.tool_calls is None
    assert msg.content == "hi"
