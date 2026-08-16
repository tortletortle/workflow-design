"""权限审核:命令分类器 + 两层策略(工具级 permission / 命令级 category)+ 分发/引擎集成。"""
import asyncio
import json

import pytest

from app.builtin_tools import powershell, register_shell_tools, run_command, terminal
from app.dispatcher import dispatch
from app.engine import AgentEngine
from app.model_client import ScriptedModelClient
from app.permissions import (
    CATEGORY_RANK,
    PermissionPolicy,
    classify_command,
    register_rule,
)
from app.protocol import TranslatedToolCall, WorkflowEnvelope
from app.registry import ToolRegistry, tool


# ---- 分类器 ----


@pytest.mark.parametrize("command,expected", [
    ("echo hello", "read"),
    ("dir", "read"),
    ("Get-Service", "read"),
    ("Write-Output 'x'", "read"),
    ("git status", "read"),
    ("git diff HEAD~1", "read"),
    ("Get-ChildItem -Recurse", "read"),
    ("echo x > out.txt", "write"),          # 重定向升 write
    ("New-Item -Path a.txt", "write"),
    ("npm install", "write"),
    ("git commit -m x", "write"),
    ("python -c print(1)", "execute"),
    ("node server.js", "execute"),
    ("bash run.sh", "execute"),
    ("git push origin main", "execute"),
    ("rm -rf /tmp/x", "destructive"),
    ("del a.txt", "destructive"),
    ("Remove-Item a.txt", "destructive"),
    ("Stop-Process -Name notepad", "destructive"),
    ("format D:", "destructive"),
    ("Get-Process | Stop-Process", "destructive"),   # 管道取最高危段
    ("echo a && del b", "destructive"),              # 链式取最高危段
    ("cmd /c echo inner", "read"),                   # 包装器内命令递归分类
    ("cmd /c rm x", "destructive"),
    ("powershell -Command Get-Date", "read"),
    ("totally-unknown-cmd --flag", "unknown"),
    ("", "unknown"),
])
def test_classify_command(command, expected):
    assert classify_command(command) == expected


def test_register_rule_extends_table():
    register_rule("git mv", "write")
    assert classify_command("git mv a b") == "write"
    with pytest.raises(ValueError):
        register_rule("x y", "not-a-category")


# ---- 策略:工具级 ----


def test_policy_denies_ungranted_permission():
    policy = PermissionPolicy()  # 默认只授 default
    decision = policy.check("terminal", "shell", {"command": "echo hi"})
    assert decision.allowed is False
    assert "requires permission 'shell'" in decision.reason


def test_policy_grants_declared_permission():
    policy = PermissionPolicy(allowed_permissions={"default", "shell"})
    decision = policy.check("terminal", "shell", {"command": "echo hi"})
    assert decision.allowed is True
    assert decision.category == "read"


def test_policy_blocked_tools():
    policy = PermissionPolicy(
        allowed_permissions={"default", "shell"}, blocked_tools={"powershell"}
    )
    assert policy.check("powershell", "shell", {"command": "Get-Date"}).allowed is False
    assert policy.check("terminal", "shell", {"command": "echo hi"}).allowed is True


# ---- 策略:命令级 ----


def test_policy_category_limit():
    policy = PermissionPolicy(
        allowed_permissions={"shell"}, allowed_categories={"read", "write"}
    )
    assert policy.check("terminal", "shell", {"command": "echo hi"}).allowed is True
    assert policy.check("terminal", "shell", {"command": "New-Item a"}).allowed is True
    # execute 超档
    d = policy.check("terminal", "shell", {"command": "python -c 1"})
    assert d.allowed is False and d.category == "execute"
    # destructive 超档
    d = policy.check("terminal", "shell", {"command": "del x"})
    assert d.allowed is False and d.category == "destructive"


def test_policy_unknown_command_conservative():
    policy = PermissionPolicy(allowed_permissions={"shell"}, allowed_categories={"read", "write"})
    assert policy.check("terminal", "shell", {"command": "strange-cmd"}).allowed is False
    loose = PermissionPolicy(
        allowed_permissions={"shell"}, allowed_categories={"read"}, allow_unknown=True
    )
    assert loose.check("terminal", "shell", {"command": "strange-cmd"}).allowed is True


def test_policy_run_command_assembled():
    policy = PermissionPolicy(allowed_permissions={"shell"}, allowed_categories={"read"})
    # program+arguments 组装后审核:cmd /c echo → read → 放行
    ok = policy.check("run_command", "shell", {"program": "cmd", "arguments": ["/c", "echo", "hi"]})
    assert ok.allowed is True
    bad = policy.check("run_command", "shell", {"program": "python", "arguments": ["-c", "1"]})
    assert bad.allowed is False


def test_policy_non_command_tool_skips_category_check():
    policy = PermissionPolicy()  # 只授 default,只读
    assert policy.check("query_order", "default", {"order_id": "A1"}).allowed is True


# ---- 分发器集成 ----


def _registry():
    reg = ToolRegistry()
    register_shell_tools(reg)
    return reg


def test_dispatch_denied_returns_permission_error():
    reg = _registry()
    policy = PermissionPolicy(allowed_permissions={"default"})  # 不授 shell
    results = asyncio.run(dispatch(reg, [
        TranslatedToolCall(id="c1", name="terminal", arguments={"command": "echo hi"}),
    ], policy=policy))
    r = results[0]
    assert r.ok is False
    assert r.error.startswith("permission denied:")
    assert "requires permission 'shell'" in r.error


def test_dispatch_category_denied_but_process_not_started():
    reg = _registry()
    policy = PermissionPolicy(allowed_permissions={"shell"}, allowed_categories={"read"})
    results = asyncio.run(dispatch(reg, [
        TranslatedToolCall(id="c1", name="terminal", arguments={"command": "del x.txt"}),
    ], policy=policy))
    assert results[0].ok is False
    assert "destructive" in results[0].error
    # 命令没有被执行:error 是审核拒绝而非命令输出


def test_dispatch_allowed_readonly_command_executes():
    reg = _registry()
    policy = PermissionPolicy(allowed_permissions={"shell"}, allowed_categories={"read"})
    results = asyncio.run(dispatch(reg, [
        TranslatedToolCall(id="c1", name="terminal", arguments={"command": "echo policy_ok"}),
    ], policy=policy))
    r = results[0]
    assert r.ok is True
    assert "policy_ok" in r.content


def test_dispatch_no_policy_backward_compatible():
    reg = _registry()
    results = asyncio.run(dispatch(reg, [
        TranslatedToolCall(id="c1", name="terminal", arguments={"command": "echo free"}),
    ]))
    assert results[0].ok is True  # 不传 policy = 不审核(向后兼容)


def test_dispatch_mcp_permission_default_denied():
    from app.mcp_provider import MockMCPProvider

    reg = ToolRegistry()
    reg.register_mcp("kb", MockMCPProvider(tools=[{
        "name": "search", "description": "", "inputSchema": {"type": "object"}
    }]))
    asyncio.run(reg.tools_manifest())
    policy = PermissionPolicy()  # 只授 default,mcp 未授 → 拒
    results = asyncio.run(dispatch(reg, [
        TranslatedToolCall(id="c1", name="mcp__kb__search", arguments={"q": "x"}),
    ], policy=policy))
    assert results[0].ok is False
    assert "requires permission 'mcp'" in results[0].error


def test_dispatch_mcp_permission_granted_via_policy():
    from app.mcp_provider import MockMCPProvider

    reg = ToolRegistry()
    reg.register_mcp("kb", MockMCPProvider(tools=[{
        "name": "search", "description": "", "inputSchema": {"type": "object"}
    }]))
    asyncio.run(reg.tools_manifest())
    policy = PermissionPolicy(allowed_permissions={"mcp"})
    results = asyncio.run(dispatch(reg, [
        TranslatedToolCall(id="c1", name="mcp__kb__search", arguments={"q": "x"}),
    ], policy=policy))
    assert results[0].ok is True


# ---- 引擎集成:拒绝文本回填,模型可换路 ----


def test_engine_policy_denial_feeds_back_to_model():
    @tool
    def add(a: int, b: int) -> int:
        """两数相加。"""
        return a + b

    reg = ToolRegistry()
    register_shell_tools(reg)
    reg.register_local(add)

    envelope = WorkflowEnvelope.model_validate({
        "workflow": {"name": "policy-demo", "max_steps": 6},
        "request": {"model": "mock-smart"},
        "context": {"vars": {}, "retrieved": []},
        "inputs": {"system_prompt": "助手", "user_input": "删掉那个文件"},
    })
    scripts = [
        {"tool_calls": [{"id": "c1", "name": "terminal", "arguments": {"command": "del a.txt"}}]},
        {"text": "抱歉,当前策略不允许执行删除类命令。"},
    ]
    policy = PermissionPolicy(allowed_permissions={"default", "shell"}, allowed_categories={"read"})
    engine = AgentEngine(ScriptedModelClient(scripts), reg, policy=policy)
    result = asyncio.run(engine.run(envelope))

    assert result.status == "final"
    denied = result.steps[0].tool_results[0]
    assert denied.ok is False
    assert "permission denied" in denied.error
    # 拒绝文本作为 tool 消息回填,第二轮模型给出终答
    tool_msg = next(m for m in result.transcript if m.role == "tool")
    assert "destructive" in tool_msg.content or "permission denied" in tool_msg.content
    assert result.final_text


def test_category_rank_ordering():
    assert CATEGORY_RANK["read"] < CATEGORY_RANK["write"] < CATEGORY_RANK["execute"] < CATEGORY_RANK["destructive"]
