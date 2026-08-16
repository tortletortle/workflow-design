"""请求分解测试:LLM 分解 / 白底后缀 / 容错降级。"""
from __future__ import annotations

import httpx
import pytest

from app.client import ChatClient
from app.decomposer import WHITE_BG_SUFFIX, decompose
from app.settings import AppConfig

from .conftest import SPEC_JSON, chat_response


async def test_decompose_builds_prompt_with_white_bg(chat):
    spec = await decompose(chat, "一把火焰剑", "item", "", cfg=AppConfig())
    assert spec.subject_en == "a flaming sword"
    assert spec.style_tokens == "pixel art 16-bit"
    assert not spec.fallback
    assert "pure solid white background" in spec.final_prompt
    assert "single game item prop" in spec.final_prompt  # item 类别模板
    assert "flaming sword" in spec.final_prompt
    assert "blurry" in spec.final_prompt  # 负面词进 prompt


async def test_decompose_category_hints(env):
    spec = None
    for category, hint in [
        ("character", "full body single character"),
        ("icon", "readable when scaled down"),
        ("effect", "VFX sprite"),
    ]:
        chat = ChatClient(env, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=chat_response(SPEC_JSON))))
        spec = await decompose(chat, "测试", category, "", cfg=AppConfig())
        assert hint in spec.final_prompt, category


async def test_decompose_fallback_on_garbage(env):
    def handler(request):
        return httpx.Response(200, json=chat_response("抱歉我不能输出 JSON"))

    chat = ChatClient(env, transport=httpx.MockTransport(handler))
    spec = await decompose(chat, "一把火焰剑", "item", "", cfg=AppConfig())
    assert spec.fallback
    assert "一把火焰剑" in spec.final_prompt  # 直译进 prompt 交给生图模型
    assert WHITE_BG_SUFFIX[:30] in spec.final_prompt


async def test_decompose_llm_error_falls_back(env):
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    chat = ChatClient(env, transport=httpx.MockTransport(handler))
    spec = await decompose(chat, "火焰剑", "item", "", cfg=AppConfig())
    assert spec.fallback


@pytest.mark.parametrize("style", ["pixel art 16-bit", ""])
async def test_decompose_style_used(chat, style):
    spec = await decompose(chat, "a sword", "item", style, cfg=AppConfig())
    if style:
        assert style in spec.final_prompt
