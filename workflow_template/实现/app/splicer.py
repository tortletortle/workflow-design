"""拼接器:把信封里的原始素材组装成模型请求的 messages(拼接信息的接口)。

顺序固定为:system(插值 + RAG 注入)→ history → user_input。
{{var}} 插值只作用于 system_prompt;缺失变量保留原样并告警在返回值中。
"""
from __future__ import annotations

import re
import uuid

from app.protocol import (
    Message,
    ModelRequest,
    RunContext,
    RunInputs,
    WorkflowEnvelope,
)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def interpolate(text: str, vars: dict) -> tuple[str, list[str]]:
    """system 模板 {{var}} 插值;返回 (插值结果, 缺失变量名列表)。"""
    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key in vars:
            return str(vars[key])
        missing.append(key)
        return m.group(0)

    return _VAR_RE.sub(_sub, text), missing


def build_system_message(inputs: RunInputs, context: RunContext) -> Message:
    """system = 模板插值 + retrieved(RAG)注入。"""
    system_text, _ = interpolate(inputs.system_prompt, context.vars)
    if context.retrieved:
        chunks = "\n\n".join(
            f"[{i + 1}] {c.get('source', 'unknown')}: {c.get('text', '')}"
            for i, c in enumerate(context.retrieved)
        )
        system_text += "\n\n以下是可供参考的知识片段:\n" + chunks
    return Message(role="system", content=system_text)


def splice_messages(envelope: WorkflowEnvelope) -> list[Message]:
    """拼出首轮 messages:system(插值+RAG)→ history → user_input。

    分发回填后的后续轮次由引擎在 transcript 上追加 assistant/tool 消息,
    本函数保持纯拼接职责、不做状态维护。
    """
    msgs: list[Message] = [build_system_message(envelope.inputs, envelope.context)]
    msgs.extend(m.model_copy(deep=True) for m in envelope.inputs.history)
    if envelope.inputs.user_input:
        msgs.append(Message(role="user", content=envelope.inputs.user_input))
    return msgs


def build_model_request(envelope: WorkflowEnvelope, messages: list[Message], tools: list) -> ModelRequest:
    """把拼接产物写回信封的 request 区(保留业务在 request 上已设的采样参数)。"""
    req = envelope.request
    req.messages = messages
    req.tools = tools
    return req


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:20]}"
