"""Agent 引擎:拼接 → 请求 → 翻译 → 分发 → 回填 → 再请求,直到终答或达上限。

核心循环(每步留痕到 RunResult.steps):
  step += 1
  拼接首轮 messages / 后续轮沿用 transcript + 回填
  pre_model_call → model.request → post_model_call
  translate_reply →
    type=text/empty → 收尾(final)
    type=tool_calls → pre_dispatch → dispatch → post_dispatch → 回填 → 下一轮
  超过 max_steps → status=max_steps(可观测,不静默截断)
  任何异常 → on_error + status=error
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.dispatcher import dispatch, results_to_messages
from app.hooks import HookRegistry
from app.model_client import ModelClient
from app.permissions import PermissionPolicy
from app.protocol import (
    RunResult,
    StepRecord,
    ToolResult,
    WorkflowEnvelope,
)
from app.registry import ToolRegistry
from app.splicer import build_model_request, splice_messages
from app.translator import assistant_message, translate_reply

log = logging.getLogger("workflow.engine")


class AgentEngine:
    def __init__(
        self,
        model: ModelClient,
        registry: ToolRegistry,
        hooks: HookRegistry | None = None,
        policy: PermissionPolicy | None = None,
    ):
        self.model = model
        self.registry = registry
        self.hooks = hooks or HookRegistry()
        self.policy = policy

    async def run(self, envelope: WorkflowEnvelope) -> RunResult:
        result = RunResult(status="final", workflow=envelope.workflow)
        transcript = await self._kickoff(envelope)
        try:
            while True:
                envelope.workflow.step += 1
                if envelope.workflow.step > envelope.workflow.max_steps:
                    result.status = "max_steps"
                    result.transcript = transcript
                    await self.hooks.fire("on_finish", envelope=envelope, result=result, reason="max_steps")
                    return result

                msgs = transcript  # 首轮已由 _kickoff 拼好,后续轮沿用 transcript + 工具回填
                req = build_model_request(envelope, msgs, await self.registry.tools_manifest())
                await self.hooks.fire("pre_model_call", envelope=envelope, request=req)

                response = await self.model.request(req, req.tools)
                await self.hooks.fire("post_model_call", envelope=envelope, response=response)

                reply = translate_reply(response, self.registry)
                transcript.append(assistant_message(reply))
                step_record = StepRecord(step=envelope.workflow.step, request_messages=msgs, reply_type=reply.type)

                if reply.next_action == "final_answer":
                    step_record.final_text = reply.text or ""
                    result.steps.append(step_record)
                    result.status = "final"
                    result.final_text = reply.text or ""
                    result.transcript = transcript
                    await self.hooks.fire("on_finish", envelope=envelope, result=result, reason="final")
                    return result

                # tool_calls 分支
                step_record.tool_calls = reply.tool_calls
                await self.hooks.fire("pre_dispatch", envelope=envelope, tool_calls=reply.tool_calls)
                tool_results = await dispatch(self.registry, reply.tool_calls, policy=self.policy)
                step_record.tool_results = tool_results
                await self.hooks.fire("post_dispatch", envelope=envelope, tool_results=tool_results)
                transcript.extend(results_to_messages(tool_results))
                result.steps.append(step_record)

        except Exception as e:
            log.exception("workflow run failed")
            await self.hooks.fire("on_error", envelope=envelope, error=e)
            result.status = "error"
            result.error = f"{type(e).__name__}: {e}"
            result.transcript = transcript
            return result

    async def _kickoff(self, envelope: WorkflowEnvelope) -> list:
        await self.hooks.fire("on_start", envelope=envelope)
        return splice_messages(envelope)


def transcript_to_json(transcript: list) -> str:
    """调试用:打印完整对话留痕。"""
    return json.dumps([m.model_dump() for m in transcript], ensure_ascii=False, indent=2, default=str)
