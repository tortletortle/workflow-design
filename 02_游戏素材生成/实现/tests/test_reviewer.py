"""视觉审核测试:裁决解析 / 能力探测 / 降级。"""
from __future__ import annotations

import json

import httpx
import pytest

from app.client import ChatClient
from app.reviewer import Reviewer, parse_verdict

from .conftest import VERDICT_FAIL, VERDICT_PASS, asset_png, chat_response, mock_chat_transport


def test_parse_verdict_threshold():
    v = parse_verdict(VERDICT_PASS, min_score=8)
    assert v.passed and v.score == 9
    # 模型说 pass 但分数低于阈值 → 仍不过
    low = '{"pass": true, "score": 7, "issues": ""}'
    assert not parse_verdict(low, min_score=8).passed
    assert not parse_verdict(VERDICT_FAIL, min_score=8).passed


async def test_reviewer_unavailable_without_model(chat):
    r = Reviewer(chat, "")
    assert not await r.available()


async def test_reviewer_probe_failure_marks_unavailable(env):
    chat = ChatClient(env, transport=mock_chat_transport(vision_ok=False))
    r = Reviewer(chat, "some-vision-model")
    assert not await r.available()
    assert not await r.available()  # 缓存生效


async def test_review_ok_path(env, tmp_path):
    chat = ChatClient(env, transport=mock_chat_transport(verdict=VERDICT_PASS))
    r = Reviewer(chat, "v-model")
    assert await r.available()
    img = tmp_path / "draft.png"
    img.write_bytes(asset_png())
    verdict = await r.review(img, "a flaming sword", min_score=8)
    assert verdict.passed and verdict.score == 9


async def test_review_error_does_not_raise(env, tmp_path):
    def handler(request):
        body = request.read()
        if b"game asset reviewer" in body:
            return httpx.Response(500, json={"error": {"message": "vision upstream down"}})
        return httpx.Response(200, json=chat_response("ok"))

    chat = ChatClient(env, transport=httpx.MockTransport(handler))
    r = Reviewer(chat, "v-model")
    assert await r.available()  # 探测(无 system)通过
    img = tmp_path / "draft.png"
    img.write_bytes(asset_png())
    verdict = await r.review(img, "sword", min_score=8)
    assert verdict.status == "error" and not verdict.passed
    assert not await r.available()  # 正式调用失败后置为不可用


async def test_anim_frame_review_ignores_background(env, tmp_path):
    """动画帧口径:送审 system 用 sprite frame reviewer(忽略背景/阴影,只判姿势/单主体)。"""
    seen: dict = {}

    def handler(request):
        body = json.loads(request.content.decode("utf-8"))
        msgs = body.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        if "sprite frame reviewer" in system:
            seen["system"] = system
            return httpx.Response(200, json=chat_response(VERDICT_PASS))
        return httpx.Response(200, json=chat_response("ok"))

    chat = ChatClient(env, transport=httpx.MockTransport(handler))
    r = Reviewer(chat, "v-model")
    img = tmp_path / "frame.png"
    img.write_bytes(asset_png())
    verdict = await r.review(img, "knight; pose: stepping forward", min_score=8,
                             category="anim_frame")
    assert verdict.passed
    assert "IGNORE" in seen["system"] and "post-processing" in seen["system"]
