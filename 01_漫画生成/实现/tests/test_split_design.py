"""拆分与设计:JSON 容错解析、重试、角色卡、提示词组装。"""
import json

import pytest

from app.designer import _clean_prompt, build_character_sheet, design_all, design_panel
from app.splitter import Panel, parse_panels, split_story


def test_parse_panels_strips_markdown_fences():
    raw = "好的,以下是分镜:\n```json\n[{\"index\": 1, \"scene\": \"s\", \"characters\": [], \"dialogue\": \"d\", \"shot\": \"wide\"}]\n```\n希望有帮助"
    panels = parse_panels(raw)
    assert len(panels) == 1
    assert panels[0].scene == "s"


def test_parse_panels_extracts_embedded_array():
    raw = "前缀说明 [{\"index\": 2, \"scene\": \"b\"}, {\"index\": 1, \"scene\": \"a\"}] 后缀说明"
    panels = parse_panels(raw)  # 无 expected,允许任意数量
    assert [p.index for p in panels] == [1, 2]  # 按 index 排序


def test_parse_panels_count_mismatch_raises():
    with pytest.raises(ValueError):
        parse_panels("[{\"index\": 1, \"scene\": \"s\"}]", expected=3)


def test_parse_panels_no_array_raises():
    with pytest.raises(ValueError):
        parse_panels("抱歉我不能这样做")


def test_parse_panels_unbalanced_raises():
    with pytest.raises(ValueError):
        parse_panels("[{\"index\": 1, \"scene\": \"s\"")


async def test_split_story_ok_with_fenced_output(chat):
    panels = await split_story(chat, "一个故事", 3)
    assert len(panels) == 3
    assert panels[0].dialogue == "呼……"
    assert panels[2].shot == "wide"


async def test_build_character_sheet(chat):
    sheet = await build_character_sheet(chat, [Panel(index=1, scene="x", characters=["小李"])])
    assert "小李" in sheet


async def test_design_panel_prepends_style_anchor(chat):
    panel = Panel(index=1, scene="深夜便利店", characters=["小李"], shot="close-up")
    prompt = await design_panel(chat, panel, "black and white manga", "小李: young man")
    assert prompt.startswith("black and white manga,")
    assert "manga panel prompt" in prompt


def test_clean_prompt_strips_prefix():
    assert _clean_prompt("Prompt: a cat") == "a cat"
    assert _clean_prompt("`a dog`") == "a dog"
    assert _clean_prompt("  multiple   spaces\nhere  ") == "multiple spaces here"


async def test_design_all_fills_every_panel(chat):
    panels = [
        Panel(index=1, scene="a", characters=["小李"]),
        Panel(index=2, scene="b", characters=["橘猫"]),
    ]
    out, sheet = await design_all(chat, panels, "style anchor", lock_characters=True, concurrency=2)
    assert all(p.image_prompt.startswith("style anchor,") for p in out)
    assert "小李" in sheet


async def test_design_panel_with_feedback(chat):
    panel = Panel(index=1, scene="场景", characters=["小李"])
    prompt = await design_panel(chat, panel, "anchor", "小李: man", feedback="character missing, shot wrong")
    assert prompt  # 反馈路径不炸,产出提示词
