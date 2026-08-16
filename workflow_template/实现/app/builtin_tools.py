"""内置工具集:终端调用(terminal)/ 命令执行(run_command)/ PowerShell(powershell)。

三类工具的差异:
- terminal       在系统默认 shell(Windows 为 cmd)里执行命令行,支持管道/重定向/&& 链
- run_command    直接启动指定程序 + 参数列表,不经 shell 解析(无通配符/注入面,更安全)
- powershell     用 PowerShell 执行(pwsh 优先,回退 Windows PowerShell),适合系统管理

返回统一为 JSON 字符串:{ok, exit_code, stdout, stderr, timed_out, duration_ms}。

安全提示:这三类工具会真实执行本机命令。接入业务前建议:
1. 用 hooks 的 pre_dispatch 做审批/白名单拦截(见 app/hooks.py);
2. timeout 上限 300s、输出截断 20000 字符(已内建);
3. 尽量在容器/沙箱内运行。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time

from app.registry import tool

MAX_TIMEOUT = 300
MAX_OUTPUT_CHARS = 20000


def _decode(data: bytes) -> str:
    """Windows 中文环境输出常为 GBK 系编码;utf-8 解不开则回退 gb18030。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[输出超长,已截断,原长度 {len(text)} 字符]"


async def _run(argv: list[str], timeout: int, cwd: str | None = None, shell: bool = False) -> dict:
    """统一的子进程执行器:超时杀进程、捕获三态(正常/超时/启动失败)。"""
    timeout = min(int(timeout or 60), MAX_TIMEOUT)
    started = time.monotonic()
    result: dict = {
        "ok": False, "exit_code": None, "stdout": "", "stderr": "",
        "timed_out": False, "duration_ms": 0, "command": argv[0] if shell else " ".join(argv),
    }
    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                argv[0],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
    except (FileNotFoundError, PermissionError, OSError) as e:
        result["stderr"] = f"failed to start: {e}"
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        result["timed_out"] = True
        result["stderr"] = f"command timed out after {timeout}s and was killed"
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result

    result["exit_code"] = proc.returncode
    result["ok"] = proc.returncode == 0
    result["stdout"] = _truncate(_decode(stdout or b""))
    result["stderr"] = _truncate(_decode(stderr or b""))
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result


def _to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


# PowerShell 可执行文件:pwsh( PowerShell 7+)优先,回退 Windows 自带 powershell
_PS_EXE: str | None = None


def _powershell_exe() -> str:
    global _PS_EXE
    if _PS_EXE is None:
        _PS_EXE = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return _PS_EXE


@tool(permission="shell")
async def terminal(command: str, timeout: int = 60, cwd: str = "") -> str:
    """终端调用工具:在系统默认 shell(Windows 为 cmd,类 Unix 为 /bin/sh)中执行命令行。

    适用:需要 shell 语法的场景(管道 |、重定向 >、命令链 &&)。
    返回 JSON:{ok, exit_code, stdout, stderr, timed_out, duration_ms}。
    """
    return _to_json(await _run([command], timeout, cwd, shell=True))


@tool(permission="shell")
async def run_command(program: str, arguments: list = None, timeout: int = 60, cwd: str = "") -> str:
    """命令工具:直接启动指定程序并传入参数列表,不经 shell 解析。

    适用:调用明确的外部程序(python/git/npm…),无通配符展开与注入面,比 terminal 更安全。
    示例:program="python", arguments=["-c", "print(1)"]
    返回 JSON 同 terminal。
    """
    return _to_json(await _run([program, *(arguments or [])], timeout, cwd))


@tool(permission="shell")
async def powershell(command: str, timeout: int = 60, cwd: str = "") -> str:
    """PowerShell 工具:用 PowerShell 执行命令或脚本片段(pwsh 优先,回退 Windows PowerShell)。

    适用:Windows 系统管理、对象管道、注册表/服务/进程/WMI 操作。
    以 -NoProfile -NonInteractive 运行,避免加载用户配置和交互阻塞。
    返回 JSON 同 terminal。
    """
    argv = [
        _powershell_exe(), "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", command,
    ]
    return _to_json(await _run(argv, timeout, cwd))


def register_shell_tools(registry) -> list[str]:
    """把三个内置工具挂入注册表,返回工具名列表。"""
    names: list[str] = []
    for t in (terminal, run_command, powershell):
        registry.register_local(t)
        names.append(t.name)
    return names


if __name__ == "__main__":
    import asyncio as _a


    async def _demo():
        print("== terminal ==")
        print(await terminal.invoke({"command": "echo hello from terminal"}))
        print("== run_command ==")
        print(await run_command.invoke({"program": "cmd", "arguments": ["/c", "echo", "hello from command"]}))
        print("== powershell ==")
        print(await powershell.invoke({"command": "Write-Output 'hello from powershell'"}))


    _a.run(_demo())
