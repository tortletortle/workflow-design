"""MCP 服务库守护:templates/mcp_servers.json 必须是合法库,真实服务全部不启用。"""
import json
from pathlib import Path

from app.mcp_provider import MockMCPProvider, StdioMCPProvider, provider_from_config
from app.registry import ToolRegistry

LIBRARY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "templates" / "mcp_servers.json"
)


def _library() -> dict:
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


def test_library_file_parses_and_has_servers():
    lib = _library()
    servers = lib["servers"]
    assert len(servers) >= 10


def test_real_open_source_servers_disabled_except_memory():
    lib = _library()
    for s in lib["servers"]:
        if s["transport"] == "stdio":
            if s["name"] == "memory":  # 长期记忆:唯一启用的真实服务
                assert s["enabled"] is True
                assert s.get("permission") == "memory"
                assert s.get("env", {}).get("MEMORY_FILE_PATH"), "memory 必须配置持久化路径"
            else:
                assert s["enabled"] is False, f"{s['name']} 必须默认 enabled=false"
            # 每个真实服务必须给出仓库与用途,保证库可追溯
            assert s.get("repo"), f"{s['name']} 缺 repo"
            assert s.get("provides"), f"{s['name']} 缺 provides"
            assert s["command"]


def test_enabled_servers_are_demo_and_memory():
    lib = _library()
    enabled = sorted(s["name"] for s in lib["servers"] if s.get("enabled"))
    assert enabled == ["demo", "memory"]


def test_provider_from_config_mock():
    p = provider_from_config({
        "name": "demo", "transport": "mock",
        "tools": [{"name": "echo", "description": "", "inputSchema": {"type": "object"}}],
    })
    assert isinstance(p, MockMCPProvider)


def test_provider_from_config_stdio():
    p = provider_from_config({
        "name": "fetch", "transport": "stdio",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-fetch"],
    })
    assert isinstance(p, StdioMCPProvider)
    # 构造期不触达 SDK;真正连接时才需要 uv sync --extra mcp


def test_provider_from_config_unknown_transport():
    import pytest

    with pytest.raises(ValueError):
        provider_from_config({"name": "x", "transport": "http"})


def test_attach_mcp_config_registers_only_enabled():
    reg = ToolRegistry()
    registered = reg.attach_mcp_config(_library(), source_path=str(LIBRARY_PATH))
    # demo(mock)+ memory(stdio);stdio 的连接是懒式的,聚合 manifest 时才真正启动
    assert registered == ["demo", "memory"]
    assert reg._mcp["memory"].permission == "memory"


def test_content_to_text_handles_sdk_objects():
    from app.mcp_provider import content_to_text

    class FakeTextContent:  # 模拟官方 SDK mcp.types.TextContent
        def __init__(self, text):
            self.text = text
            self.annotations = None

    assert content_to_text([FakeTextContent("hello")]) == "hello"
    assert content_to_text([{"type": "text", "text": "a"}, FakeTextContent("b")]) == "a\nb"
    assert content_to_text("raw") == "raw"


def test_windows_launch_wraps_npx():
    import os

    from app.mcp_provider import _windows_launch

    if os.name == "nt":
        wrapped = _windows_launch(["npx", "-y", "pkg"])
        assert wrapped[:2] == ["cmd", "/c"]
        # 第三项是 npx 的原名或 which 解析出的绝对路径(.CMD)
        assert "npx" in wrapped[2].lower()
        assert wrapped[3:] == ["-y", "pkg"]
        assert _windows_launch(["node", "x.js"]) == ["node", "x.js"]  # exe 不包装
    else:
        # 类 Unix 原样返回
        assert _windows_launch(["npx", "-y", "pkg"]) == ["npx", "-y", "pkg"]
