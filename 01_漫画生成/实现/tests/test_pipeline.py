"""流水线端到端(离线):四阶段串联、断点续跑、部分失败仍可拼接。"""
import json

import httpx
import pytest
from PIL import Image

from app.pipeline import ComicPipeline
from app.settings import AppConfig
from tests.conftest import PANELS_JSON, chat_response, fake_png, mock_chat_transport, mock_image_transport
from app.client import ChatClient, ImageClient
from app.settings import EnvSettings


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.panels.count = 3
    cfg.image.size = "64x64"
    cfg.concurrency.retries = 0
    cfg.concurrency.image = 2
    return cfg


async def test_pipeline_end_to_end(config, tmp_path):
    env = EnvSettings(base_url="http://api.test/v1", api_key="sk-test")
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.env = env
    pipeline.config = config
    pipeline.out_dir = tmp_path
    pipeline.panels_dir = tmp_path / "panels"
    pipeline.state_path = tmp_path / "state.json"
    
    # 设置 reporter (静默模式)
    from app.progress import ProgressReporter
    pipeline.reporter = ProgressReporter(silent=True)
    # 注入 mock transport:直接替换客户端构造(通过子类化 run 太重,这里复用 run 但替换类属性)
    import app.pipeline as pl

    orig_chat, orig_image = pl.ChatClient, pl.ImageClient
    pl.ChatClient = lambda e: ChatClient(e, transport=mock_chat_transport())
    pl.ImageClient = lambda e: ImageClient(e, transport=mock_image_transport())
    try:
        report = await pipeline.run("一个关于便利店和橘猫的故事")
    finally:
        pl.ChatClient, pl.ImageClient = orig_chat, orig_image

    assert report.status == "ok"
    assert report.comic_path and (tmp_path / "comic.jpg").exists()
    assert len(report.panels) == 3
    # 三格图 + 拼接图 + 状态文件
    for i in (1, 2, 3):
        assert (tmp_path / "panels" / f"panel_{i:02d}.png").exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(state["panels"]) == 3
    comic = Image.open(tmp_path / "comic.jpg")
    assert comic.size[0] == 64


async def test_pipeline_resume_skips_existing_images(config, tmp_path):
    """断点续跑:第 1 格已有图 → 重跑只补第 2、3 格,不再调用拆分后的设计。"""
    env = EnvSettings(base_url="http://api.test/v1", api_key="sk-test")
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.env = env
    pipeline.config = config
    pipeline.out_dir = tmp_path
    pipeline.panels_dir = tmp_path / "panels"
    pipeline.state_path = tmp_path / "state.json"
    
    from app.progress import ProgressReporter
    pipeline.reporter = ProgressReporter(silent=True)

    # 预置:第 1 格已完成(有提示词 + 有图)
    pipeline.panels_dir.mkdir(parents=True)
    Image.new("RGB", (64, 64), (1, 2, 3)).save(pipeline.panels_dir / "panel_01.png")
    pipeline.state_path.write_text(json.dumps({"panels": [
        {"index": 1, "scene": "深夜便利店", "characters": ["小李"], "dialogue": "呼", "shot": "medium",
         "image_prompt": "already designed", "image_file": "panel_01.png"},
    ]}, ensure_ascii=False), encoding="utf-8")

    calls = {"chat": 0, "img": 0}

    def chat_handler(request: httpx.Request) -> httpx.Response:
        calls["chat"] += 1
        if calls["chat"] == 1:
            return httpx.Response(200, json=chat_response(PANELS_JSON))
        if calls["chat"] == 2:
            return httpx.Response(200, json=chat_response("小李: man\n橘猫: cat"))
        return httpx.Response(200, json=chat_response("designed prompt"))

    def img_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            calls["img"] += 1
            return httpx.Response(200, json={"data": [{"url": "http://img.test/p.png"}]})
        return httpx.Response(200, content=fake_png())

    import app.pipeline as pl

    orig_chat, orig_image = pl.ChatClient, pl.ImageClient
    pl.ChatClient = lambda e: ChatClient(e, transport=httpx.MockTransport(chat_handler))
    pl.ImageClient = lambda e: ImageClient(e, transport=httpx.MockTransport(img_handler))
    try:
        report = await pipeline.run("同一个故事")
    finally:
        pl.ChatClient, pl.ImageClient = orig_chat, orig_image

    assert report.status == "ok"
    assert calls["img"] == 2          # 只补了第 2、3 格(第 1 格跳过)
    # 第 1 格提示词来自旧状态,没有被重新设计
    assert report.panels[0]["image_prompt"] == "already designed"


async def test_pipeline_partial_failure_still_assembles(config, tmp_path):
    """一半格生图失败 → status=partial,漫画仍产出(失败格占位框)。"""
    env = EnvSettings(base_url="http://api.test/v1", api_key="sk-test")
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.env = env
    pipeline.config = config
    pipeline.out_dir = tmp_path
    pipeline.panels_dir = tmp_path / "panels"
    pipeline.state_path = tmp_path / "state.json"
    
    from app.progress import ProgressReporter
    pipeline.reporter = ProgressReporter(silent=True)

    seq = {"img": 0}

    def img_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            seq["img"] += 1
            if seq["img"] == 2:  # 第 2 格永远失败
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"data": [{"url": "http://img.test/p.png"}]})
        return httpx.Response(200, content=fake_png())

    import app.pipeline as pl

    orig_chat, orig_image = pl.ChatClient, pl.ImageClient
    pl.ChatClient = lambda e: ChatClient(e, transport=mock_chat_transport())
    pl.ImageClient = lambda e: ImageClient(e, transport=httpx.MockTransport(img_handler))
    try:
        report = await pipeline.run("故事")
    finally:
        pl.ChatClient, pl.ImageClient = orig_chat, orig_image

    assert report.status == "partial"
    assert 2 in report.failed_indexes
    assert (tmp_path / "comic.jpg").exists()
