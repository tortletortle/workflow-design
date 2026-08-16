"""生图与拼接:双通道生图、尺寸归一化、占位框、竖排拼接。"""
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.assembler import assemble_comic, render_panel
from app.client import ImageClient
from app.illustrator import generate_all, generate_panel
from app.splitter import Panel
from tests.conftest import chat_response, fake_png


def test_image_client_via_url(image):
    import asyncio

    data = asyncio.run(image.generate("a cat", "64x64"))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_client_via_b64(env):
    import asyncio
    import base64

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(fake_png()).decode()}]})
        return httpx.Response(404)

    client = ImageClient(env, transport=httpx.MockTransport(handler))
    data = asyncio.run(client.generate("a cat", "64x64"))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_client_fallback_to_chat(env):
    """images 接口挂 → 降级 chat,从回复提取图片 URL 并下载。"""
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(500, text="boom")
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=chat_response("图片已生成: http://img.test/x.png 请查收"))
        return httpx.Response(200, content=fake_png())

    client = ImageClient(env, transport=httpx.MockTransport(handler))
    data = asyncio.run(client.generate("a cat", "64x64"))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


async def test_generate_panel_normalizes_size(image, tmp_path):
    panel = Panel(index=1, scene="s", image_prompt="p")
    # mock 图是 64x64,目标 32x32 → 必须被 resize
    await generate_panel(image, panel, tmp_path, (32, 32), retries=0)
    out = tmp_path / "panel_01.png"
    assert out.exists()
    assert Image.open(out).size == (32, 32)


async def test_generate_all_failed_panel_not_fatal(tmp_path):
    ok_panel = Panel(index=1, scene="s", image_prompt="p")
    bad_panel = Panel(index=2, scene="s", image_prompt="p")

    class BoomImage:
        model = "x"

        async def generate(self, prompt, size):
            raise RuntimeError("upstream down")

    ok, failed = await generate_all(BoomImage(), [ok_panel, bad_panel], tmp_path, (32, 32), retries=0)
    assert ok == []
    assert len(failed) == 2


def test_render_panel_placeholder_when_file_missing(tmp_path):
    panel = Panel(index=7, scene="场景", dialogue="台词")
    cell = render_panel(panel, tmp_path, (64, 64), caption_h=20)
    assert cell.size == (64, 84)
    # 占位框仍然可以拼出完整图,不抛异常


def test_assemble_comic_vertical_order(tmp_path):
    panels = []
    for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)], start=1):
        img = Image.new("RGB", (50, 50), color)
        name = f"panel_{i:02d}.png"
        img.save(tmp_path / name)
        panels.append(Panel(index=i, scene=f"s{i}", dialogue=f"台词{i}", image_file=name))
    out = assemble_comic(panels, tmp_path, tmp_path / "comic.png", (50, 50), caption_h=20, gap=5, fmt="PNG")
    comic = Image.open(out)
    assert comic.size == (50, 3 * 70 + 2 * 5)  # 每格 50+20,间隔 5
    # 验证顺序:第 1 格图片区是红色(PNG 无损,像素精确)
    assert comic.getpixel((25, 25)) == (255, 0, 0)
    # 第 2 格图片区(偏移 75)是绿色
    assert comic.getpixel((25, 75 + 25)) == (0, 255, 0)


def test_assemble_comic_jpeg_output(tmp_path):
    """JPEG 输出:后缀/格式正确、可打开。(体积优势在小测试图上不成立,
    生产 1024 级图片 live 验证:5.8MB PNG → ~1MB JPEG)"""
    panels = []
    for i in range(3):
        grad = Image.linear_gradient("L").resize((60, 60))
        name = f"panel_{i + 1:02d}.png"
        grad.save(tmp_path / name)
        panels.append(Panel(index=i + 1, scene="s", dialogue="d", image_file=name))
    jpg = assemble_comic(panels, tmp_path, tmp_path / "c.jpg", (60, 60), caption_h=16, fmt="JPEG", quality=85)
    assert jpg.suffix == ".jpg"
    assert Image.open(jpg).format == "JPEG"
    assert Image.open(jpg).size == (60, 3 * 76 + 2 * 12)


def test_assemble_comic_suffix_corrected_by_format(tmp_path):
    panel = Panel(index=1, scene="s", dialogue="d", image_file="")
    out = assemble_comic([panel], tmp_path, tmp_path / "comic.png", (40, 40), caption_h=16, fmt="JPEG")
    assert out.suffix == ".jpg"  # 传入 .png 但按 JPEG 纠正


def test_assemble_comic_with_missing_panel_keeps_order(tmp_path):
    p1 = Panel(index=1, scene="s", dialogue="d", image_file="")
    out = assemble_comic([p1], tmp_path, tmp_path / "comic.png", (40, 40), caption_h=16)
    assert Image.open(out).size == (40, 56)
