"""server 测试:工具注册 / 错误透传为结构化结果 / list_assets 工具可用。"""
from __future__ import annotations

import pytest

import app.server as server
from app.settings import AppConfig


async def test_five_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {"generate_asset", "cutout_image", "list_assets", "list_library",
                     "generate_animation"}
    # 主工具 schema 覆盖关键参数(含一致性参数),供调用方 AI 理解
    main = next(t for t in tools if t.name == "generate_asset")
    schema = getattr(main, "input_schema", None) or main.inputSchema
    for prop in ("sizes", "category", "subject_ref", "save_subject_as", "save_style_as"):
        assert prop in schema.get("properties", {}), prop


async def test_generate_asset_error_returns_structured(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(server, "generate_asset_pipeline", boom)
    result = await server.generate_asset(description="x")
    assert result["status"] == "error"
    assert "upstream down" in result["message"]


async def test_cutout_image_error_returns_structured(monkeypatch):
    async def boom(**kw):
        raise ValueError("bad path")

    monkeypatch.setattr(server, "cutout_image_pipeline", boom)
    result = await server.cutout_image(image_path="Z:/nope.png")
    assert result["status"] == "error"


async def test_list_assets_tool_offline():
    # 读本地 out/ 目录,零网络;目录不存在/为空都应正常返回
    result = await server.list_assets()
    assert result["status"] == "ok" and "count" in result


async def test_list_library_tool_no_shadow():
    # 防回归:曾因与导入同名遮蔽导致工具自调用报错
    # (list_library() takes 0 positional arguments but 1 was given)
    result = await server.list_library()
    assert result["status"] == "ok"
    assert isinstance(result["styles"], list) and isinstance(result["subjects"], list)
