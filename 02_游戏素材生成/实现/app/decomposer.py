"""请求分解:AI 的自然语言素材请求 → 结构化英文生图提示词。

三层合成:
1. LLM 分解(chat):中文/口语描述 → JSON{subject_en, style_tokens, composition, negative_prompt}
2. 一致性注入:全局风格锚 + 主体卡(必须精确匹配的外观约束)
3. 本地模板合成:类别构图模板 + 背景后缀
   - 抠图类别(角色/道具/图标/特效/UI)→ 强制纯白背景(抠图硬前提)
   - 满幅类别(background/tile)→ 完整画面铺满画幅,不加白底

分解失败自动降级为直译拼模板 —— 分解是增强,不是阻断点。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.client import ChatClient
from app.settings import AppConfig

CATEGORIES = ("character", "item", "icon", "tile", "effect", "ui", "background")

# 满幅类别:不抠图、保留完整画面(背景图/可平铺贴图)
NO_CUTOUT_CATEGORIES = frozenset({"background", "tile"})

CATEGORY_HINTS = {
    "character": "full body single character, standing pose, front 3/4 view, complete figure including feet, no cropping",
    "item": "single game item prop, centered, 3/4 view, complete object with clear readable silhouette",
    "icon": "simple bold iconic shape, high contrast, readable when scaled down small, minimal fine detail",
    "tile": "single tileable texture block, flat top-down view, pattern fills the whole frame edge to edge",
    "effect": "game VFX sprite, spell or impact effect centered, vivid energetic shapes",
    "ui": "single game UI element, flat clean design, crisp sharp edges, centered",
    "background": "full-bleed game environment background, complete scene with depth, wide establishing view",
}

# 白底是后续抠图的硬前提,仅对抠图类别强制追加
WHITE_BG_SUFFIX = (
    "isolated on a pure solid white background (#FFFFFF), no shadows, no ground plane, "
    "no text, no watermark, no frame, single subject centered with clear margins"
)

# 满幅类别反之:完整画面,禁止白底(否则抠图逻辑会误删)
FULLBLEED_SUFFIX = (
    "complete scene filling the entire frame edge to edge, no borders, no margins, "
    "no isolated subject, no text, no watermark"
)

DECOMPOSE_SYSTEM = (
    "You are a game asset art director. Turn the user's asset request (possibly in Chinese, "
    "possibly vague) into an English image-generation prompt spec for ONE game asset. "
    "If a LOCKED APPEARANCE is provided, subject_en MUST reuse it verbatim (only add pose/"
    "context words, never change the appearance). Respond ONLY with JSON: "
    '{"subject_en": "the main subject described in concise English", '
    '"style_tokens": "art style keywords (e.g. pixel art 16-bit / painterly fantasy / cel-shaded anime)", '
    '"composition": "extra composition or detail keywords, empty if none", '
    '"negative_prompt": "things to avoid, short"}. No other text.'
)


@dataclass
class AssetSpec:
    description: str                 # 原始请求
    category: str
    style: str                       # 用户指定风格(空 = 交给 LLM/默认)
    subject_en: str = ""
    style_tokens: str = ""
    composition: str = ""
    negative_prompt: str = ""
    subject_card: str = ""           # 主体卡:必须精确匹配的外观描述
    style_anchor: str = ""           # 全局/库风格锚
    fallback: bool = False           # True = LLM 分解失败,用直译模板
    final_prompt: str = field(default="", init=False)

    def build_prompt(self) -> str:
        style = " ".join(s for s in (self.style_anchor, self.style_tokens or self.style) if s)
        style = style or "clean game art style"
        parts = [
            self.subject_en,
            style,
            CATEGORY_HINTS.get(self.category, CATEGORY_HINTS["item"]),
            self.composition,
            FULLBLEED_SUFFIX if self.category in NO_CUTOUT_CATEGORIES else WHITE_BG_SUFFIX,
        ]
        prompt = ", ".join(p.strip().rstrip(".") for p in parts if p and p.strip())
        if self.subject_card:
            prompt += f". LOCKED APPEARANCE (must match exactly): {self.subject_card.strip()}"
        if self.negative_prompt:
            prompt += f". Avoid: {self.negative_prompt.strip().rstrip('.')}"
        self.final_prompt = prompt
        return prompt


def _extract_json_object(text: str) -> dict:
    """从模型回复中剥围栏、截取首个平衡的 {...} 段。"""
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON object")


def _spec_from_llm(data: dict, description: str, category: str, style: str,
                   subject_card: str, style_anchor: str) -> AssetSpec:
    subject = str(data.get("subject_en") or "").strip()
    if not subject:
        raise ValueError("empty subject_en")
    return AssetSpec(
        description=description,
        category=category,
        style=style,
        subject_en=subject,
        style_tokens=str(data.get("style_tokens") or "").strip(),
        composition=str(data.get("composition") or "").strip(),
        negative_prompt=str(data.get("negative_prompt") or "").strip(),
        subject_card=subject_card,
        style_anchor=style_anchor,
    )


def _fallback_spec(description: str, category: str, style: str, reason: str,
                   subject_card: str = "", style_anchor: str = "") -> AssetSpec:
    spec = AssetSpec(
        description=description,
        category=category,
        style=style,
        subject_en=description,  # 直译交给生图模型(其对中文有一定容忍度)
        subject_card=subject_card,
        style_anchor=style_anchor,
        fallback=True,
    )
    spec.composition = f"(note: llm decompose fallback: {reason})"
    spec.build_prompt()
    return spec


async def decompose(
    chat: ChatClient,
    description: str,
    category: str,
    style: str,
    feedback: str = "",
    cfg: AppConfig | None = None,
    subject_card: str = "",
    style_anchor: str = "",
) -> AssetSpec:
    """请求分解。feedback 非空 = 上一轮审核被拒,把问题带回让 LLM 修正。"""
    temp = cfg.decompose.temperature if cfg else 0.3
    user = f"Category: {category}\nRequest: {description}"
    if style_anchor:
        user += f"\nGlobal style anchor (apply to style_tokens): {style_anchor}"
    if style:
        user += f"\nRequired style: {style}"
    if subject_card:
        user += f"\nLOCKED APPEARANCE (subject_en must reuse verbatim): {subject_card}"
    if feedback:
        user += (
            f"\nIMPORTANT - previous attempt was rejected by review with feedback: {feedback}. "
            "Fix these issues in the new spec."
        )

    last_err = ""
    for temperature in (temp, 0.0):  # 解析失败降温重试一次
        try:
            raw = await chat.chat(
                [{"role": "system", "content": DECOMPOSE_SYSTEM}, {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=400,
            )
            spec = _spec_from_llm(_extract_json_object(raw), description, category, style,
                                  subject_card, style_anchor)
            spec.build_prompt()
            return spec
        except (ValueError, json.JSONDecodeError, RuntimeError) as e:
            last_err = str(e)[:120]
    return _fallback_spec(description, category, style, last_err, subject_card, style_anchor)
