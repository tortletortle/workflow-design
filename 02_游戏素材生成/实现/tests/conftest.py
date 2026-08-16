"""conftest:离线测试基线,零网络零 API 消耗。"""
from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from app.client import ChatClient, ImageClient
from app.settings import AppConfig, EnvSettings, ImageConfig


def small_cfg(lib_dir=None, **kw) -> AppConfig:
    """离线测试用小图配置。lib_dir 指到 tmp,避免写真实素材库。

    engine=white_key:离线测试不依赖 rembg(其模型下载属 live 冒烟范畴)。
    """
    return AppConfig(
        image=ImageConfig(size="96x96", retries=1),
        cutout={"engine": "white_key", "white_tolerance": 18, "feather": 2, "padding_ratio": 0.06},
        style={"anchor": "", "lib_dir": str(lib_dir) if lib_dir else "lib"},
        **kw,
    )

SPEC_JSON = json.dumps({
    "subject_en": "a flaming sword",
    "style_tokens": "pixel art 16-bit",
    "composition": "sword pointing up",
    "negative_prompt": "blurry, extra weapons",
})

VERDICT_PASS = json.dumps({"pass": True, "score": 9, "issues": ""})
VERDICT_FAIL = json.dumps({"pass": False, "score": 4, "issues": "subject cropped at bottom"})


def chat_response(content: str) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}]}


def asset_png(size=(96, 96), bg=(255, 255, 255)) -> bytes:
    """白底 + 红色方块(方块内含不与外界连通的白色圆点) 的测试图。

    - 有方差,不会被启发式质检误拦
    - 内部白点用于验证白底键控"只删边缘连通白区"
    """
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, 72, 72], fill=(200, 30, 30))
    d.ellipse([42, 42, 54, 54], fill=(255, 255, 255))  # 主体内部的白点
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def mock_chat_transport(verdict: str = VERDICT_PASS, vision_ok: bool = True) -> httpx.MockTransport:
    """按 system 提示词分流:分解 / 视觉审核 / 探测。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        msgs = body.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        if "art director" in system:
            return httpx.Response(200, json=chat_response(f"```json\n{SPEC_JSON}\n```"))
        if "game asset reviewer" in system or "sprite frame reviewer" in system:
            return httpx.Response(200, json=chat_response(verdict))
        # 无 system = 视觉能力探测 / 生图降级通道
        if not vision_ok:
            return httpx.Response(500, json={"error": {"message": "no vision upstream"}})
        return httpx.Response(200, json=chat_response("ok"))

    return httpx.MockTransport(handler)


def mock_image_transport(png: bytes | None = None) -> httpx.MockTransport:
    """images/generations → data url;GET 图片 → PNG 字节。"""
    png = png if png is not None else asset_png()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "http://img.test/asset.png"}]})
        return httpx.Response(200, content=png)

    return httpx.MockTransport(handler)


@pytest.fixture
def env() -> EnvSettings:
    # _env_file=None:彻底隔离真实 .env(否则配了真实 vision_model 后,
    # 离线测试会真的去调视觉接口,破坏零网络承诺)
    return EnvSettings(_env_file=None, base_url="http://api.test/v1", api_key="sk-test",
                       chat_model="test-chat", image_model="test-image", vision_model="")


@pytest.fixture
def chat(env) -> ChatClient:
    return ChatClient(env, transport=mock_chat_transport())


@pytest.fixture
def image(env) -> ImageClient:
    return ImageClient(env, transport=mock_image_transport())
