"""流水线测试:端到端(mock)/ 审核循环 / 降级 / list_assets。"""
from __future__ import annotations

import json

import httpx
import pytest
from PIL import Image

from app.client import ChatClient, ImageClient
from app.pipeline import cutout_image_pipeline, generate_asset_pipeline, list_assets

from .conftest import VERDICT_FAIL, asset_png, mock_chat_transport, mock_image_transport, small_cfg


async def test_pipeline_end_to_end_vision_skipped(chat, image, env, tmp_path):
    result = await generate_asset_pipeline(
        "一把火焰剑", "item", "pixel art", [64, 32],
        env=env, cfg=small_cfg(), chat=chat, image=image, out_root=tmp_path,
    )
    assert result["status"] == "ok"
    assert result["review"]["status"] == "skipped"  # 未配视觉模型 → 降级
    assert "pure solid white background" in result["prompt"]
    # 产物齐全
    assert Image.open(result["draft_path"]).size == (96, 96)
    assert Image.open(result["cutout"]["path"]).mode == "RGBA"
    finals = {f["size"]: f["path"] for f in result["finals"]}
    assert set(finals) == {64, 32}
    for size, path in finals.items():
        img = Image.open(path)
        assert img.size == (size, size) and img.mode == "RGBA"
        assert img.split()[-1].getpixel((0, 0)) == 0  # 背景透明
    # task.json 落盘
    task_json = tmp_path / result["task_id"] / "task.json"
    assert json.loads(task_json.read_text(encoding="utf-8"))["task_id"] == result["task_id"]


async def test_pipeline_review_loop_regenerates(env, tmp_path):
    calls = {"gen": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            calls["gen"] += 1
            return httpx.Response(200, json={"data": [{"url": "http://img.test/a.png"}]})
        return httpx.Response(200, content=asset_png())

    image = ImageClient(env, transport=httpx.MockTransport(handler))
    chat = ChatClient(env, transport=mock_chat_transport(verdict=VERDICT_FAIL))
    env_v = env.model_copy(update={"vision_model": "v-model"})
    cfg = small_cfg(review={"max_rounds": 1, "min_score": 8})

    result = await generate_asset_pipeline(
        "一把火焰剑", "item", "", [64], env=env_v, cfg=cfg, chat=chat, image=image,
        vision_chat=chat, out_root=tmp_path,
    )
    # 1 次初审 + 1 次重生成 = 2 次生图;审核始终不过 → 仍交付,标记 failed
    assert calls["gen"] == 2
    assert result["review"]["status"] == "failed"
    assert len(result["review"]["rounds"]) == 2
    assert any("deliver anyway" in n for n in result["notes"])
    assert result["finals"]


async def test_pipeline_unknown_category_note(chat, image, env, tmp_path):
    result = await generate_asset_pipeline(
        "火球", "vehicle", "", None, env=env, cfg=small_cfg(), chat=chat, image=image,
        out_root=tmp_path,
    )
    assert result["category"] == "item"
    assert any("fallback to 'item'" in n for n in result["notes"])


async def test_cutout_image_pipeline(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(asset_png())
    result = await cutout_image_pipeline(str(src), [48], cfg=small_cfg(), out_root=tmp_path / "out")
    assert result["status"] == "ok"
    img = Image.open(result["finals"][0]["path"])
    assert img.size == (48, 48) and img.mode == "RGBA"


async def test_cutout_image_missing_path(tmp_path):
    result = await cutout_image_pipeline(str(tmp_path / "nope.png"), cfg=small_cfg())
    assert result["status"] == "error"


async def test_list_assets(tmp_path, chat, image, env):
    r1 = await generate_asset_pipeline("一把火焰剑", "item", "", [64],
                                       env=env, cfg=small_cfg(), chat=chat, image=image,
                                       out_root=tmp_path)
    summary = list_assets(out_root=tmp_path)
    assert summary["count"] == 1
    entry = summary["assets"][0]
    assert entry["task_id"] == r1["task_id"]
    assert entry["finals"] == [f["path"] for f in r1["finals"]]
    # 前缀过滤
    assert list_assets(out_root=tmp_path, prefix=r1["task_id"][:8])["count"] == 1
    assert list_assets(out_root=tmp_path, prefix="9999")["count"] == 0
