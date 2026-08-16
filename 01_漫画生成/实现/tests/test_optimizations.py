"""三项优化的测试:质检重生成循环 / JPEG 体积优化 / 台词优化。"""
import json

import httpx
import pytest
from PIL import Image

from app.client import ChatClient, ImageClient
from app.critic import parse_verdict, review_image_heuristic, review_prompts
from app.illustrator import generate_all
from app.pipeline import ComicPipeline
from app.settings import AppConfig, EnvSettings
from app.splitter import Panel, polish_captions
from tests.conftest import PANELS_JSON, chat_response, fake_png, mock_chat_transport, mock_image_transport


def _pixels_close(a, b, tol=3):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ---- 台词优化 ----


async def test_polish_captions_fills_empty_dialogue(chat):
    panels = [Panel(index=1, scene="s1", dialogue=""), Panel(index=2, scene="s2", dialogue="太啰嗦的原始台词啦真的太长了")]
    out = await polish_captions(chat, panels, max_chars=24)
    assert out[0].caption  # 空台词被补上
    assert out[1].caption


async def test_polish_captions_non_fatal_on_garbage(env):
    def handler(request):
        return httpx.Response(200, json=chat_response("我无法输出 JSON"))

    client = ChatClient(env, transport=httpx.MockTransport(handler))
    panels = [Panel(index=1, scene="s", dialogue="原文")]
    out = await polish_captions(client, panels)
    assert out[0].caption == ""      # 解析失败不炸
    assert out[0].dialogue == "原文"  # 原文保留


# ---- 质检器 ----


def test_parse_verdict_variants():
    ok = parse_verdict('{"pass": true, "score": 9, "issues": ""}')
    assert ok.passed and ok.score == 9
    bad = parse_verdict('说明 ```json\n{"pass": false, "score": 3, "issues": "角色缺失;镜头错误"}\n``` 完')
    assert not bad.passed and "角色缺失" in bad.issues


async def test_review_prompts_batch_all_pass(chat):
    panels = [
        Panel(index=1, scene="s", characters=["小李"], shot="wide"),
        Panel(index=2, scene="s2", characters=["橘猫"], shot="close-up"),
        Panel(index=3, scene="s3", shot="medium"),
    ]
    verdicts = await review_prompts(chat, panels)
    assert len(verdicts) == 3
    assert all(v.passed and v.score >= 8 for v in verdicts.values())


async def test_review_prompts_batch_garbage_auto_pass(env):
    def handler(request):
        return httpx.Response(200, json=chat_response("not json at all"))

    client = ChatClient(env, transport=httpx.MockTransport(handler))
    panels = [Panel(index=1, scene="s"), Panel(index=2, scene="s")]
    verdicts = await review_prompts(client, panels)
    assert all(v.passed for v in verdicts.values())  # 质检故障 → 自动放行


async def test_review_prompts_batch_missing_panel_auto_pass(env):
    """响应漏掉某格 → 该格自动放行,其余按裁决。"""

    def handler(request):
        return httpx.Response(200, json=chat_response('[{"index": 1, "pass": false, "score": 4, "issues": "cat missing"}]'))

    client = ChatClient(env, transport=httpx.MockTransport(handler))
    panels = [Panel(index=1, scene="s"), Panel(index=2, scene="s")]
    verdicts = await review_prompts(client, panels)
    assert not verdicts[1].passed and "cat missing" in verdicts[1].issues
    assert verdicts[2].passed  # 漏格放行


def test_heuristic_qa_catches_blank_image(tmp_path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (64, 64), (10, 10, 10)).save(path)
    verdict = review_image_heuristic(path, (64, 64))
    assert not verdict.passed and "blank" in verdict.issues


def test_heuristic_qa_catches_wrong_size(tmp_path):
    path = tmp_path / "img.png"
    Image.linear_gradient("L").resize((32, 32)).save(path)
    verdict = review_image_heuristic(path, (64, 64))
    assert not verdict.passed and "size" in verdict.issues


def test_heuristic_qa_passes_good_image(tmp_path):
    path = tmp_path / "img.png"
    path.write_bytes(fake_png())
    verdict = review_image_heuristic(path, (64, 64))
    assert verdict.passed


async def test_blank_image_triggers_regen(env, tmp_path):
    """启发式质检:第一张纯色废图 → 重试 → 第二张合格图通过。"""
    seq = {"n": 0}

    def img_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            seq["n"] += 1
            return httpx.Response(200, json={"data": [{"url": f"http://img.test/{seq['n']}.png"}]})
        # 第一次下载给纯色废图,之后给合格渐变图
        if seq["n"] == 1:
            buf = __import__("io").BytesIO()
            Image.new("RGB", (64, 64), (5, 5, 5)).save(buf, "PNG")
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(200, content=fake_png())

    image = ImageClient(env, transport=httpx.MockTransport(img_handler))
    panel = Panel(index=1, scene="s", image_prompt="p")
    ok, failed = await generate_all(image, [panel], tmp_path, (64, 64), retries=1, qa=True)
    assert len(ok) == 1 and not failed
    assert seq["n"] == 2  # 废图触发了第二次生成


# ---- 流水线集成:三项优化同时生效 ----


@pytest.fixture
def opt_config() -> AppConfig:
    cfg = AppConfig()
    cfg.panels.count = 3
    cfg.image.size = "64x64"
    cfg.concurrency.retries = 0
    cfg.polish.enabled = True
    cfg.critic.prompt_qa = True
    cfg.assemble.format = "JPEG"
    return cfg


def _pipeline(config, tmp_path, chat_transport=None, img_transport=None):
    env = EnvSettings(base_url="http://api.test/v1", api_key="sk-test")
    p = ComicPipeline.__new__(ComicPipeline)
    p.env = env
    p.config = config
    p.out_dir = tmp_path
    p.panels_dir = tmp_path / "panels"
    p.state_path = tmp_path / "state.json"
    import app.pipeline as pl

    orig_chat, orig_image = pl.ChatClient, pl.ImageClient
    pl.ChatClient = lambda e: ChatClient(e, transport=chat_transport or mock_chat_transport())
    transport = httpx.MockTransport(img_transport) if img_transport else mock_image_transport()
    pl.ImageClient = lambda e: ImageClient(e, transport=transport)
    return p, orig_chat, orig_image


async def test_pipeline_with_all_optimizations(opt_config, tmp_path):
    p, oc, oi = _pipeline(opt_config, tmp_path)
    import app.pipeline as pl

    try:
        report = await p.run("故事")
    finally:
        pl.ChatClient, pl.ImageClient = oc, oi

    assert report.status == "ok"
    assert report.comic_path.endswith("comic.jpg")           # JPEG 输出
    assert (tmp_path / "comic.jpg").exists()
    # 台词优化已写入状态
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert all(pn.get("caption") for pn in state["panels"])  # 每格都有字幕


async def test_pipeline_prompt_qa_fail_triggers_redesign(tmp_path):
    """批量质检:第 1 格不通过 → 与其他格生图并行地重设计 → 完成后进入生图队列。"""
    cfg = AppConfig()
    cfg.panels.count = 3
    cfg.image.size = "64x64"
    cfg.concurrency.retries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        system = next((m["content"] for m in body.get("messages", []) if m["role"] == "system"), "")
        if "story-board" in system:
            return httpx.Response(200, json=chat_response(PANELS_JSON))
        if "character designer" in system:
            return httpx.Response(200, json=chat_response("小李: man"))
        if "caption editor" in system:
            return httpx.Response(200, json=chat_response("[]"))
        if "prompt reviewer" in system:
            # 一次批量裁决:第 1 格不通过,其余通过
            return httpx.Response(200, json=chat_response(
                '[{"index": 1, "pass": false, "score": 4, "issues": "cat missing"},'
                ' {"index": 2, "pass": true, "score": 9, "issues": ""},'
                ' {"index": 3, "pass": true, "score": 9, "issues": ""}]'
            ))
        return httpx.Response(200, json=chat_response("redesigned prompt with cat"))

    p, oc, oi = _pipeline(cfg, tmp_path, chat_transport=httpx.MockTransport(handler))
    import app.pipeline as pl

    try:
        report = await p.run("故事")
    finally:
        pl.ChatClient, pl.ImageClient = oc, oi

    assert report.status == "ok"
    assert report.regen_indexes == [1]           # 只有第 1 格重设计
    assert report.failed_indexes == []           # 重设计后生图成功
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "redesigned prompt" in state["panels"][0]["image_prompt"]
