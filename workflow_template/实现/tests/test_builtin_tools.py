"""内置工具:terminal / run_command / powershell(Windows 实测)。"""
import asyncio
import json
import sys

import pytest

from app.builtin_tools import (
    _decode,
    powershell,
    register_shell_tools,
    run_command,
    terminal,
)
from app.registry import ToolRegistry

WIN = sys.platform.startswith("win")


async def test_terminal_echo():
    out = json.loads(await terminal.invoke({"command": "echo terminal_ok"}))
    assert out["ok"] is True
    assert "terminal_ok" in out["stdout"]
    assert out["exit_code"] == 0


async def test_terminal_nonzero_exit():
    out = json.loads(await terminal.invoke({"command": "exit 3"}))
    assert out["ok"] is False
    assert out["exit_code"] == 3


async def test_terminal_shell_features():
    # 管道 + 命令链:证明确实走了 shell
    out = json.loads(await terminal.invoke({"command": "echo a && echo b"}))
    assert out["ok"] is True
    assert "a" in out["stdout"] and "b" in out["stdout"]


@pytest.mark.skipif(not WIN, reason="cmd 仅 Windows")
async def test_run_command_direct_exec():
    out = json.loads(await run_command.invoke({
        "program": "cmd", "arguments": ["/c", "echo", "command_ok"],
    }))
    assert out["ok"] is True
    assert "command_ok" in out["stdout"]


async def test_run_command_missing_program():
    out = json.loads(await run_command.invoke({"program": "no-such-program-xyz"}))
    assert out["ok"] is False
    assert "failed to start" in out["stderr"]


@pytest.mark.skipif(not WIN, reason="依赖 Windows PowerShell")
async def test_powershell_write_output():
    out = json.loads(await powershell.invoke({"command": "Write-Output 'ps_ok'"}))
    assert out["ok"] is True
    assert "ps_ok" in out["stdout"].strip()


@pytest.mark.skipif(not WIN, reason="依赖 Windows PowerShell")
async def test_powershell_object_pipeline():
    out = json.loads(await powershell.invoke({
        "command": "1..3 | ForEach-Object { $_ * 2 } | Measure-Object -Sum | Select-Object -ExpandProperty Sum"
    }))
    assert out["ok"] is True
    assert out["stdout"].strip() == "12"


@pytest.mark.skipif(not WIN, reason="依赖 Windows PowerShell")
async def test_powershell_timeout_kills_process():
    out = json.loads(await powershell.invoke({"command": "Start-Sleep -Seconds 30", "timeout": 2}))
    assert out["ok"] is False
    assert out["timed_out"] is True
    assert "timed out" in out["stderr"]


@pytest.mark.skipif(not WIN, reason="依赖 Windows PowerShell")
async def test_output_truncation():
    out = json.loads(await powershell.invoke({"command": "'a' * 60000"}))
    assert out["ok"] is True
    assert len(out["stdout"]) < 21000
    assert "已截断" in out["stdout"]


def test_decode_gb18030_fallback():
    assert _decode("中文命令输出".encode("gb18030")) == "中文命令输出"
    assert _decode("utf-8 输出".encode("utf-8")) == "utf-8 输出"


def test_tool_definitions_generated():
    defs = {t.name: t.definition() for t in (terminal, run_command, powershell)}
    assert defs["terminal"].function.parameters["required"] == ["command"]
    ps_params = defs["powershell"].function.parameters
    assert ps_params["properties"]["command"]["type"] == "string"
    assert ps_params["properties"]["timeout"]["type"] == "integer"
    cmd_params = defs["run_command"].function.parameters
    assert cmd_params["properties"]["arguments"]["type"] == "array"


def test_register_shell_tools_into_registry():
    reg = ToolRegistry()
    names = register_shell_tools(reg)
    assert names == ["terminal", "run_command", "powershell"]
    manifest = asyncio.run(reg.tools_manifest())
    manifest_names = {d.function.name for d in manifest}
    assert {"terminal", "run_command", "powershell"} <= manifest_names


@pytest.mark.skipif(not WIN, reason="cmd 仅 Windows")
async def test_dispatcher_routes_builtin_tools():
    from app.dispatcher import dispatch
    from app.protocol import TranslatedToolCall

    reg = ToolRegistry()
    register_shell_tools(reg)
    results = await dispatch(reg, [
        TranslatedToolCall(id="c1", name="powershell", arguments={"command": "Write-Output 'routed'"}),
        TranslatedToolCall(id="c2", name="run_command", arguments={"program": "cmd", "arguments": ["/c", "echo", "ok"]}),
    ])
    assert all(r.ok for r in results)
    assert all(r.source == "local" for r in results)
    assert "routed" in results[0].content
