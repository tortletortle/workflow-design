"""生图/图生图编辑测试:归一 / 启发式质检 / 重试 / 编辑请求形状。"""
from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

from app.client import EditClient, ImageClient
from app.generator import edit_with_qa, generate_with_qa, heuristic_qa, _normalize

from .conftest import asset_png


def test_normalize_resizes_and_flattens():
    # RGBA 图(透明区) → 压白底 RGB,尺寸归一
    rgba = Image.new("RGBA", (64, 64), (255, 0, 0, 128))
    buf = io.BytesIO()
    rgba.save(buf, "PNG")
    out = _normalize(buf.getvalue(), (96, 96))
    assert out.size == (96, 96)
    assert out.mode == "RGB"


def test_heuristic_qa_rejects_blank():
    blank = Image.new("RGB", (1024, 1024), (255, 255, 255))
    reason = heuristic_qa(blank, 200_000)
    assert reason is not None and "blank" in reason


def test_heuristic_qa_accepts_normal():
    img = Image.open(io.BytesIO(asset_png()))
    assert heuristic_qa(img, 50_000) is None


async def test_generate_with_qa_saves_normalized_png(image, tmp_path):
    out = tmp_path / "draft.png"
    # mock 图 96x96,请求尺寸也 96x96 → 直接存
    res = await generate_with_qa(image, "a sword", out, "96x96", retries=1)
    assert res.attempts == 1
    img = Image.open(out)
    assert img.size == (96, 96)


async def test_generate_with_qa_retries_then_raises(env, tmp_path):
    def handler(request):
        # 全白图 → 方差质检必拦
        buf = io.BytesIO()
        Image.new("RGB", (96, 96), (255, 255, 255)).save(buf, "PNG")
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "http://img.test/x.png"}]})
        return httpx.Response(200, content=buf.getvalue())

    image = ImageClient(env, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="blank"):
        await generate_with_qa(image, "p", tmp_path / "d.png", "96x96", retries=2)


def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode()


def _edit_transport(png: bytes | None = None, seen: dict | None = None):
    """images/edits → b64_json 回包;可选记录请求供断言。"""
    png = png if png is not None else asset_png()

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("Authorization", "")
            seen["content_type"] = request.headers.get("Content-Type", "")
            seen["body"] = request.content
        return httpx.Response(200, json={"data": [{"b64_json": _b64(png)}]})

    return httpx.MockTransport(handler)


async def test_edit_client_sends_multipart_shape(env):
    seen: dict = {}
    env_e = env.model_copy(update={"edit_api_key": "sk-edit"})
    edit = EditClient(env_e, transport=_edit_transport(asset_png(), seen))
    try:
        raw = await edit.edit("make the circle blue", b"\x89PNG-fake")
        assert raw[:4] == b"\x89PNG"
        # StepFun 官方形状:multipart 字段齐全 + b64_json 回包
        assert seen["path"].endswith("/images/edits")
        assert seen["auth"] == "Bearer sk-edit"
        assert "multipart/form-data" in seen["content_type"]
        body = seen["body"]
        assert b'name="model"' in body and b"step-image-edit-2" in body
        assert b'name="prompt"' in body and b"make the circle blue" in body
        assert b'name="response_format"' in body and b"b64_json" in body
        assert b'name="image"' in body and b"\x89PNG-fake" in body
    finally:
        await edit.aclose()


async def test_edit_with_qa_follows_ref_size(env, tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(asset_png((96, 96)))
    edit = EditClient(env, transport=_edit_transport())
    try:
        res = await edit_with_qa(edit, "same character, new pose", ref,
                                 tmp_path / "out.png", retries=1)
        assert res.attempts == 1
        img = Image.open(tmp_path / "out.png")
        assert img.size == (96, 96)  # 输出跟随参考图画幅
    finally:
        await edit.aclose()


async def test_edit_with_qa_http_error_raises(env, tmp_path):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    ref = tmp_path / "ref.png"
    ref.write_bytes(asset_png())
    edit = EditClient(env, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="edit HTTP 401"):
        await edit_with_qa(edit, "p", ref, tmp_path / "out.png", retries=0)
