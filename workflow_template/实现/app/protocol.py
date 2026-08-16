"""工作流 JSON 协议:信封 / 消息 / 工具 / 翻译产物 / 运行结果。

所有模型 extra="allow" —— 业务可在任意层级挂 x- 前缀自定义字段,
拼接、翻译、分发全程透传不丢弃。字段即文档,详版见 ../协议.md。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    """全协议基类:开放扩展字段。"""

    model_config = ConfigDict(extra="allow")


# ---- 信封(拼接接口的容器)----


class WorkflowMeta(_Open):
    name: str = "default"
    version: str = "1.0"
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    step: int = 0
    max_steps: int = 8


class ModelRequest(_Open):
    """拼接产物:OpenAI 兼容请求体(model/capability 二选一,兼容 model_gateway)。"""

    model: str | None = None
    capability: str | None = None
    messages: list["Message"] = Field(default_factory=list)
    tools: list["ToolDefinition"] = Field(default_factory=list)
    tool_choice: str | dict | None = "auto"
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict | None = None
    stream: bool = False


class RunContext(_Open):
    """业务上下文注入区:vars 供 system 模板 {{var}} 插值;retrieved 为 RAG 注入位。"""

    user_id: str | None = None
    session: dict[str, Any] = Field(default_factory=dict)
    vars: dict[str, Any] = Field(default_factory=dict)
    retrieved: list[dict[str, Any]] = Field(default_factory=list)


class RunInputs(_Open):
    """本次运行的原始输入,由拼接器消费。"""

    system_prompt: str = ""
    history: list["Message"] = Field(default_factory=list)
    user_input: str = ""


class WorkflowEnvelope(_Open):
    """工作流运行信封:一次 run 的完整描述(请求模板见 templates/workflow_request.json)。"""

    workflow: WorkflowMeta = Field(default_factory=WorkflowMeta)
    request: ModelRequest = Field(default_factory=ModelRequest)
    context: RunContext = Field(default_factory=RunContext)
    inputs: RunInputs = Field(default_factory=RunInputs)
    hooks: list[str] = Field(default_factory=list)


# ---- 消息(OpenAI 兼容 + 扩展)----


class FunctionCall(_Open):
    name: str
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class AssistantToolCall(_Open):
    """模型回复里的工具调用(OpenAI tool_calls 条目,arguments 已解析为 dict)。"""

    id: str
    type: str = "function"
    function: FunctionCall


class Message(_Open):
    """统一消息:role ∈ system | user | assistant | tool。

    - assistant 消息可带 tool_calls(模型发起的调用)
    - tool 消息带 tool_call_id + name(分发回填的结果)
    """

    role: str
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[AssistantToolCall] | None = None


# ---- 工具定义(OpenAI function 格式;MCP inputSchema 直接映射)----


class FunctionDefinition(_Open):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(_Open):
    type: str = "function"
    function: FunctionDefinition


# ---- 翻译器产物:模型回复 → 结构化动作 ----


class TranslatedToolCall(_Open):
    """翻译后的单条工具调用:附加 source(local / mcp:<server>),分发器据此路由。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None


class TranslatedReply(_Open):
    """翻译器输出。

    type:
    - text          纯文本回复 → next_action=final_answer
    - tool_calls    发起工具调用 → next_action=dispatch_tools
    - empty         空回复(异常防御)→ next_action=final_answer
    """

    type: Literal["text", "tool_calls", "empty"]
    text: str | None = None
    tool_calls: list[TranslatedToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    next_action: Literal["final_answer", "dispatch_tools"]
    raw: dict[str, Any] | None = None
    x_meta: dict[str, Any] = Field(default_factory=dict)


# ---- 分发产物:工具执行结果 ----


class ToolResult(_Open):
    """单条工具调用的执行结果;ok=False 时 content 携带错误说明(不中断整体 run)。"""

    tool_call_id: str
    name: str
    ok: bool = True
    content: str = ""
    error: str | None = None
    source: str | None = None
    latency_ms: int = 0


# ---- 运行结果 ----


class StepRecord(_Open):
    """一轮循环的留痕:拼接 → 模型 → 翻译 → 分发,便于调试与观测。"""

    step: int
    request_messages: list[Message] = Field(default_factory=list)
    reply_type: str | None = None
    tool_calls: list[TranslatedToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    final_text: str | None = None
    created_at: int = Field(default_factory=lambda: int(time.time()))


class RunResult(_Open):
    """整个 run 的交付物:status ∈ final | max_steps | error。"""

    status: Literal["final", "max_steps", "error"]
    final_text: str | None = None
    error: str | None = None
    steps: list[StepRecord] = Field(default_factory=list)
    transcript: list[Message] = Field(default_factory=list)
    workflow: WorkflowMeta = Field(default_factory=WorkflowMeta)
