"""阶段 1 · 拆分:故事文本 → 分镜列表(结构化 JSON)。

容错解析:模型输出可能裹 markdown 围栏或前后带说明文字,
剥壳后截取首个 [...] JSON 段;解析失败自动降参重试一次。
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from app.client import ChatClient
from app.errors import ModelOutputError


class Panel(BaseModel):
    """一格分镜。caption / image_prompt / image_file 由后续阶段填充。"""

    index: int
    scene: str = Field(description="画面场景描述")
    characters: list[str] = Field(default_factory=list)
    dialogue: str = ""            # 台词/旁白(拆分原始产出)
    caption: str = ""             # 台词优化产物(拼接字幕条优先用它)
    shot: str = Field(default="", description="镜头:wide/medium/close-up 等")
    image_prompt: str = ""        # 阶段 2 产物:生图提示词
    image_file: str = ""          # 阶段 3 产物:落盘文件名


def _extract_json_array(text: str) -> str:
    """剥 ```json 围栏 / 截取首个 [ ... ] 平衡段。"""
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    start = text.find("[")
    if start < 0:
        raise ValueError("no JSON array found in model output")
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
        elif ch == '"' and not escape:
            in_str = not in_str
        elif not in_str:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    raise ValueError("unbalanced JSON array in model output")


def parse_panels(raw: str, expected: int | None = None) -> list[Panel]:
    try:
        arr = _extract_json_array(raw)
        panels = [Panel.model_validate(p) for p in json.loads(arr)]
    except (ValueError, json.JSONDecodeError) as e:
        raise ModelOutputError("splitter", f"JSON 解析失败: {e}", raw_output=raw)
    except Exception as e:
        raise ModelOutputError("splitter", f"分镜格式验证失败: {e}", raw_output=raw)
    
    panels.sort(key=lambda p: p.index)
    if expected and len(panels) != expected:
        raise ModelOutputError(
            "splitter", 
            f"期望 {expected} 格，实际返回 {len(panels)} 格",
            raw_output=raw
        )
    return panels


SPLIT_SYSTEM = (
    "You are a professional manga story-board editor. "
    "Split the given story into exactly {n} sequential panels. "
    "Respond with ONLY a JSON array, no explanations, no markdown fences. "
    "Each element: {{\"index\": <1-based int>, "
    "\"scene\": <concise visual description of what we SEE, in {lang}>, "
    "\"characters\": [names appearing in this panel], "
    "\"dialogue\": <spoken lines or narration for this panel, in {lang}, keep short>, "
    "\"shot\": <one of: wide / medium / close-up / low-angle / high-angle>}}. "
    "Maintain strict story order and visual continuity between panels."
)


async def split_story(
    chat: ChatClient, story: str, count: int, language: str = "zh"
) -> list[Panel]:
    system = SPLIT_SYSTEM.format(n=count, lang=language)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": story}]

    last_error: Exception | None = None
    for temperature in (0.3, 0.0):  # 失败一次后降温重试
        try:
            raw = await chat.chat(messages, temperature=temperature)
            return parse_panels(raw, expected=count)
        except ModelOutputError as e:
            last_error = e
    # 重试后仍失败，抛出最后一次错误
    raise last_error if last_error else ModelOutputError("splitter", "未知错误")


POLISH_SYSTEM = (
    "You are a manga caption editor. For each panel below, write ONE short caption "
    "in {lang}: a punchy spoken line or narration that fits the panel mood. Rules: "
    "max {n} characters; every panel MUST get a caption (use narration if no dialogue); "
    "keep original meaning; do NOT copy the scene text, write NEW wording; "
    "no quotes, no panel numbers. "
    "Respond ONLY a JSON array: [{{\"index\": <int>, \"caption\": \"...\"}}]."
)


def sanitize_caption(raw: str, scenes: set[str], max_chars: int) -> str:
    """清洗 polish 产物(live 2026-08-17:模型偶发把输入摘要原样回吐)。

    - 去引号/空白;截断 " | dialogue: ..." 类摘要伪影
    - 字幕恰好等于某格 scene → 判定为回吐,丢弃(拼接回退 dialogue/scene)
    - 硬性截断到 max_chars
    """
    cap = raw.strip().strip("\"'“”‘’").strip()
    cap = re.split(r"\s*\|\s*", cap, maxsplit=1)[0].strip()
    if not cap or cap in scenes:
        return ""
    return cap[:max_chars]


async def polish_captions(
    chat: ChatClient, panels: list[Panel], language: str = "zh", max_chars: int = 24
) -> list[Panel]:
    """台词优化:拆分产出的 dialogue 可能为空/啰嗦,统一改写成漫画式短字幕。

    一次调用处理全部格(省成本);解析失败不阻塞:保留原文本。
    """
    digest = "\n".join(
        f"#{p.index} scene: {p.scene} | dialogue: {p.dialogue or '(none)'}"
        for p in panels
    )
    scenes = {p.scene for p in panels}
    try:
        raw = await chat.chat(
            [
                {"role": "system", "content": POLISH_SYSTEM.format(lang=language, n=max_chars)},
                {"role": "user", "content": digest},
            ],
            temperature=0.4,
        )
        items = json.loads(_extract_json_array(raw))
        by_index = {
            item["index"]: sanitize_caption(str(item.get("caption", "")), scenes, max_chars)
            for item in items
        }
        for p in panels:
            if by_index.get(p.index):
                p.caption = by_index[p.index]
    except (ValueError, json.JSONDecodeError, KeyError, TypeError):
        pass  # 非致命:字幕条会回退用 dialogue/scene
    return panels
