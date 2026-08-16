"""拼接器:首轮消息顺序、插值、RAG 注入。"""
from app.protocol import Message, WorkflowEnvelope
from app.splicer import splice_messages


def _envelope_with_history():
    return WorkflowEnvelope.model_validate({
        "workflow": {"name": "t", "max_steps": 5},
        "request": {"model": "mock-smart"},
        "context": {"vars": {"u": "W", "lang": "zh"}, "retrieved": []},
        "inputs": {
            "system_prompt": "助你好 {{u}} ({{lang}})",
            "history": [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回复"},
            ],
            "user_input": "新问题",
        },
    })


def test_splice_order_system_history_user():
    env = _envelope_with_history()
    msgs = splice_messages(env)
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[0].content.startswith("助你好 W (zh)")
    assert msgs[1].content == "旧问题"
    assert msgs[2].content == "旧回复"
    assert msgs[3].content == "新问题"


def test_splice_no_user_input():
    env = _envelope_with_history()
    env.inputs.user_input = ""
    msgs = splice_messages(env)
    assert [m.role for m in msgs] == ["system", "user", "assistant"]


def test_splice_missing_var_kept_as_is():
    env = WorkflowEnvelope.model_validate({
        "workflow": {"name": "t", "max_steps": 5},
        "context": {"vars": {}, "retrieved": []},
        "inputs": {"system_prompt": "hello {{whoami}}", "user_input": "hi"},
    })
    msgs = splice_messages(env)
    assert msgs[0].content == "hello {{whoami}}"


def test_splice_deep_copies_history():
    env = _envelope_with_history()
    msgs = splice_messages(env)
    msgs[1].content = "modified"
    assert env.inputs.history[0].content == "旧问题"
