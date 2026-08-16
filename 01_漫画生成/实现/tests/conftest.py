"""conftest:离线测试基线,零网络零 API 消耗。"""
import io
import json

import httpx
import pytest
from PIL import Image

from app.client import ChatClient, ImageClient
from app.settings import EnvSettings


def fake_png(color=(200, 30, 30), size=(64, 64)) -> bytes:
    """带渐变方差的假图(纯色会被启发式质检的方差检查拦截)。"""
    buf = io.BytesIO()
    grad = Image.linear_gradient("L").resize(size)
    img = Image.merge("RGB", (grad, grad.point(lambda v: (v + color[0]) % 256), grad))
    img.save(buf, "PNG")
    return buf.getvalue()


def chat_response(content: str) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


PANELS_JSON = """[
 {"index": 1, "scene": "深夜便利店,店员小李在打盹", "characters": ["小李"], "dialogue": "呼……", "shot": "medium"},
 {"index": 2, "scene": "橘猫推门进来开口说话", "characters": ["小李", "橘猫"], "dialogue": "来一份关东煮。", "shot": "close-up"},
 {"index": 3, "scene": "橘猫放下金色硬币离开", "characters": ["橘猫"], "dialogue": "这是守夜人的礼物。", "shot": "wide"}
]"""


def mock_chat_transport() -> httpx.MockTransport:
    """按 system 提示词内容分流:拆分/角色卡/质检/台词优化/设计,与调用顺序无关。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        system = next((m["content"] for m in body.get("messages", []) if m["role"] == "system"), "")
        if "story-board" in system:
            return httpx.Response(200, json=chat_response(f"```json\n{PANELS_JSON}\n```"))
        if "character designer" in system:
            return httpx.Response(200, json=chat_response("小李: young man in store uniform\n橘猫: fat orange cat"))
        if "prompt reviewer" in system:
            return httpx.Response(200, json=chat_response(
                '[{"index": 1, "pass": true, "score": 9, "issues": ""},'
                ' {"index": 2, "pass": true, "score": 9, "issues": ""},'
                ' {"index": 3, "pass": true, "score": 9, "issues": ""}]'
            ))
        if "caption editor" in system:
            return httpx.Response(200, json=chat_response(
                '[{"index": 1, "caption": "深夜,小李在打盹"}, {"index": 2, "caption": "橘猫:来份关东煮"}, {"index": 3, "caption": "这是守夜人的礼物"}]'
            ))
        return httpx.Response(200, json=chat_response("a manga panel prompt for this scene"))

    return httpx.MockTransport(handler)


def mock_image_transport() -> httpx.MockTransport:
    """images/generations → 返回 data url(指向 mock download);GET 图片 → PNG 字节。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "http://img.test/panel.png"}]})
        return httpx.Response(200, content=fake_png())

    return httpx.MockTransport(handler)


@pytest.fixture
def env() -> EnvSettings:
    return EnvSettings(base_url="http://api.test/v1", api_key="sk-test",
                       chat_model="test-chat", image_model="test-image")


@pytest.fixture
def chat(env) -> ChatClient:
    return ChatClient(env, transport=mock_chat_transport())


@pytest.fixture
def image(env) -> ImageClient:
    return ImageClient(env, transport=mock_image_transport())
