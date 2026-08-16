"""demo 入口:离线 Agent 循环 / 长期记忆(MCP memory)/ 网关模式。

用法:
  cd workflow_template/实现
  uv run python -m app.main            # 离线 demo(脚本化模型 + Mock MCP + 本地工具)
  uv run python -m app.main --memory   # 长期记忆 demo:官方 MCP memory 服务,
                                       #   两次 run 共享记忆文件,验证跨会话记住事实
  uv run python -m app.main --gateway http://127.0.0.1:8000 --model mock-smart
                                       # 接 model_gateway 跑(mock 模型零成本)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.engine import AgentEngine
from app.hooks import HookRegistry
from app.mcp_provider import MockMCPProvider
from app.model_client import GatewayModelClient, ScriptedModelClient
from app.protocol import WorkflowEnvelope
from app.registry import ToolRegistry, tool


@tool
def query_order(order_id: str, include_items: bool = False) -> dict:
    """按订单号查询物流状态与预计送达时间。"""
    return {
        "order_id": order_id,
        "status": "已发货,预计明日送达",
        "eta": "2026-08-16",
        "items": ["商品A x1"] if include_items else None,
    }


@tool
def refund_policy() -> dict:
    """查询订单退货政策。"""
    return {"policy": "订单签收后 7 天内可无理由退货,需保留商品完好。"}


def build_demo_mcp() -> MockMCPProvider:
    return MockMCPProvider(
        tools=[
            {
                "name": "kb_search",
                "description": "在知识库中检索相关片段",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ],
        handlers={"kb_search": lambda query: {"hits": [{"source": "kb", "text": f"关于 {query} 的检索片段"}]}},
    )


def build_demo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_local(query_order)
    registry.register_local(refund_policy)
    registry.register_mcp("kb", build_demo_mcp())
    return registry


def build_demo_envelope() -> WorkflowEnvelope:
    return WorkflowEnvelope.model_validate({
        "workflow": {"name": "客服问答 demo", "version": "1.0", "max_steps": 8},
        "request": {"model": "mock-smart", "temperature": 0.2},
        "context": {
            "user_id": "u-10086",
            "vars": {"user_name": "张三", "lang": "zh-CN", "today": "2026-08-15"},
            "retrieved": [{"source": "kb/order-policy.md", "text": "订单签收后 7 天内可无理由退货……"}],
        },
        "inputs": {
            "system_prompt": "你是 {{user_name}} 的专属客服助手,使用 {{lang}} 回答,今天是 {{today}}。可调用工具查订单和退缩政策。",
            "user_input": "订单号 A-1234 什么时候到?顺便问下退货政策。"
        },
        "hooks": ["on_start", "pre_model_call", "post_model_call", "pre_dispatch", "post_dispatch", "on_finish", "on_error"]
    })


def build_printing_hooks() -> HookRegistry:
    h = HookRegistry()

    async def on_start(envelope):
        print(f"\n[on_start] workflow={envelope.workflow.name} run_id={envelope.workflow.run_id[:8]}")

    def pre_model_call(envelope, request):
        print(f"[pre_model_call] step={envelope.workflow.step} msgs={len(request.messages)} tools={len(request.tools)}")

    def post_model_call(envelope, response):
        finish = response.get("choices", [{}])[0].get("finish_reason")
        print(f"[post_model_call] finish_reason={finish}")

    def pre_dispatch(envelope, tool_calls):
        names = ", ".join(f"{c.name}({str(c.arguments)[:120]})" for c in tool_calls)
        print(f"[pre_dispatch] tool_calls=[{names}]")

    def post_dispatch(envelope, tool_results):
        for r in tool_results:
            mark = "OK" if r.ok else "FAIL"
            print(f"[post_dispatch] {mark} {r.name} source={r.source} latency={r.latency_ms}ms:\n      {r.content[:200]}")

    def on_finish(envelope, result, reason):
        print(f"[on_finish] reason={reason} status={result.status} steps={len(result.steps)}")

    def on_error(envelope, error):
        print(f"[on_error] {error}", file=sys.stderr)

    h.on("on_start", on_start)
    h.on("pre_model_call", pre_model_call)
    h.on("post_model_call", post_model_call)
    h.on("pre_dispatch", pre_dispatch)
    h.on("post_dispatch", post_dispatch)
    h.on("on_finish", on_finish)
    h.on("on_error", on_error)
    return h


# 离线脚本:第 1 轮要工具,第 2 轮再要工具,第 3 轮终答
def offline_scripts():
    return [
        {"tool_calls": [{"id": "c1", "name": "query_order", "arguments": {"order_id": "A-1234"}}]},
        {"tool_calls": [{"id": "c2", "name": "refund_policy", "arguments": {}}]},
        {
            "text": (
                "张三您好,订单 A-1234 预计 2026-08-16 送达;退货政策为:"
                "签收后 7 天内可无理由退货,需保留商品完好。"
            )
        },
    ]


def _mcp_library_config() -> dict:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent.parent / "templates" / "mcp_servers.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _memory_env(user_input: str, system: str) -> WorkflowEnvelope:
    return WorkflowEnvelope.model_validate({
        "workflow": {"name": "长期记忆 demo", "version": "1.0", "max_steps": 6},
        "request": {"model": "mock-smart"},
        "context": {"vars": {}, "retrieved": []},
        "inputs": {"system_prompt": system, "user_input": user_input},
    })


async def run_memory_demo():
    """官方 MCP memory 服务长期记忆演示。

    会话 A:把用户事实写入知识图谱(create_entities);
    随后彻底关闭 memory 子进程;
    会话 B:用全新 provider/信封(无任何历史)重新连接,
    search_nodes 检索到会话 A 写入的事实 —— 跨进程持久长期记忆。
    """
    from app.permissions import PermissionPolicy
    from app.registry import ToolRegistry

    print("=" * 60)
    print("长期记忆演示:官方 MCP memory(知识图谱,JSONL 持久化)")
    print("=" * 60)

    policy = PermissionPolicy(allowed_permissions={"default", "memory"}, allowed_categories={"read"})

    # ---- 会话 A:写入记忆 ----
    print("\n--- 会话 A:告诉它一个事实(写入记忆) ---")
    registry_a = ToolRegistry()
    print(f"挂载 MCP 服务: {registry_a.attach_mcp_config(_mcp_library_config())}")

    scripts_a = [
        {"tool_calls": [{"id": "m1", "name": "mcp__memory__create_entities", "arguments": {
            "entities": [{"name": "用户", "entityType": "person",
                          "observations": ["名字是张三", "喜欢简洁的回答"]}]
        }}]},
        {"text": "记住了,张三。以后我会回答得更简洁。"},
    ]
    result_a = await AgentEngine(
        ScriptedModelClient(scripts_a), registry_a, hooks=build_printing_hooks(), policy=policy
    ).run(_memory_env(
        "我叫张三,我喜欢简洁的回答",
        "你是助手。把用户告诉你的个人事实写入记忆工具,然后简短确认。",
    ))
    print(f"会话 A 结果: {result_a.status} | {result_a.final_text}")

    # 彻底关闭 memory 子进程——会话 B 必须从磁盘文件重新加载
    await registry_a._mcp["memory"].close()
    print("\n(已关闭 memory 服务子进程;会话 B 将重新连接,从 JSONL 文件加载记忆)")

    # ---- 会话 B:全新会话,凭持久化记忆回答 ----
    print("\n--- 会话 B:全新会话,问它记不记得(读取记忆) ---")
    registry_b = ToolRegistry()
    registry_b.attach_mcp_config(_mcp_library_config())

    scripts_b = [
        {"tool_calls": [{"id": "m1", "name": "mcp__memory__search_nodes", "arguments": {"query": "张三"}}]},
        {"text": "记得。你是张三,喜欢简洁的回答。"},
    ]
    result_b = await AgentEngine(
        ScriptedModelClient(scripts_b), registry_b, hooks=build_printing_hooks(), policy=policy
    ).run(_memory_env(
        "还记得我是谁吗?",
        "你是助手。先用记忆工具检索用户信息,再回答。",
    ))
    await registry_b._mcp["memory"].close()

    print("-" * 60)
    retrieved = result_b.steps[0].tool_results[0].content if result_b.steps else ""
    print(f"会话 B 结果: {result_b.status} | {result_b.final_text}")
    print(f"会话 B 检索到的记忆片段: {retrieved[:300]}")
    print("✓ 跨会话长期记忆生效(两次 run 无共享状态,记忆来自 MEMORY_FILE_PATH 指向的文件)")


async def run_offline():
    print("=" * 60)
    print("离线演示:ScriptedModelClient + Mock MCP + 本地工具")
    print("=" * 60)
    engine = AgentEngine(
        model=ScriptedModelClient(offline_scripts()),
        registry=build_demo_registry(),
        hooks=build_printing_hooks(),
    )
    result = await engine.run(build_demo_envelope())
    print("-" * 60)
    print(f"最终状态: {result.status}\n最终回答: {result.final_text}")
    print(f"留痕轮数: {len(result.steps)};对话条数: {len(result.transcript)}")


async def run_gateway(base_url: str, model: str):
    print("=" * 60)
    print(f"网关演示:GatewayModelClient → {base_url} (model={model})")
    print("注意:model_gateway 的 mock 模型不会发起 tool_calls,此模式仅验证链路联通。")
    print("=" * 60)
    envelope = build_demo_envelope()
    envelope.request.model = model
    gateway = GatewayModelClient(base_url)
    engine = AgentEngine(
        model=gateway,
        registry=build_demo_registry(),
        hooks=build_printing_hooks(),
    )
    result = await engine.run(envelope)
    await gateway.aclose()
    print("-" * 60)
    print(f"最终状态: {result.status}\n最终回答: {result.final_text}")
    print("\n(transcript JSON 留痕前两条)")
    from app.engine import transcript_to_json
    print(transcript_to_json(result.transcript[:2]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", help="model_gateway 地址,如 http://127.0.0.1:8000")
    parser.add_argument("--model", default="mock-smart", help="传给网关的 model 名")
    parser.add_argument("--memory", action="store_true", help="长期记忆 demo(官方 MCP memory 服务)")
    args = parser.parse_args()

    if args.memory:
        asyncio.run(run_memory_demo())
    elif args.gateway:
        asyncio.run(run_gateway(args.gateway, args.model))
    else:
        asyncio.run(run_offline())


if __name__ == "__main__":
    main()
