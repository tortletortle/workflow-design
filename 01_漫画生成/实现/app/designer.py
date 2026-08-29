"""阶段 2 · 设计:每格分镜 → 生图提示词(细控)。

细控手段:
- 风格锚(style_anchor):所有格共用的风格前缀,保证整卷统一
- 角色外貌锁定:先从全部分镜提炼角色卡(一次调用),再逐格引用,
  避免同一角色每格长得不一样
- 镜头语言:把 shot 映射进提示词构图描述
- 输出纯英文提示词(生图模型对英文更稳)
"""
from __future__ import annotations

import asyncio
import re

from app.client import ChatClient
from app.splitter import Panel

DESIGN_SYSTEM = (
    "You are an expert manga prompt engineer. Create a DETAILED English image-generation prompt "
    "(100-200 words) with rich visual description. Structure your prompt with these elements:\n\n"
    "1. SUBJECT: Character actions, expressions, poses, exact appearance from character sheet\n"
    "2. COMPOSITION: Frame the shot according to the shot type (close-up/medium/wide), "
    "apply composition rules (rule of thirds, leading lines, depth)\n"
    "3. ENVIRONMENT: Detailed background, setting atmosphere, spatial relationships\n"
    "4. LIGHTING: Describe light source, shadows, mood (dramatic/soft/harsh), contrast level\n"
    "5. DETAILS: Textures, small objects, emotional cues, environmental storytelling\n\n"
    "Shot type guidance:\n"
    "- close-up: focus on face/expression, shallow depth, emotional lighting\n"
    "- medium: upper body, balanced composition, context visible\n"
    "- wide: full scene, environmental storytelling, establishing shot\n\n"
    "Rules: Single panel composition; keep character appearance EXACTLY as character sheet; "
    "no text/speech bubbles/watermarks; maintain consistent manga art style. "
    "Output ONLY the prompt itself."
)

CHARACTER_SHEET_SYSTEM = (
    "You are a character designer. From the panels below, extract every recurring character "
    "and create a DETAILED appearance sheet for visual consistency. For each character, "
    "write 5-10 lines covering:\n\n"
    "- Hair: exact color, style, length, texture (e.g. 'shoulder-length blue hair with side-swept bangs')\n"
    "- Face: shape, eye color/shape, distinctive features (moles, scars, glasses)\n"
    "- Body: build, height, posture characteristics\n"
    "- Outfit: specific clothing items with colors, patterns, accessories\n"
    "- Distinctive traits: unique visual markers that make this character recognizable\n\n"
    "Format: 'Name:\n- [detail 1]\n- [detail 2]\n...'\n\n"
    "Be SPECIFIC with colors, shapes, and sizes. Avoid vague terms like 'nice' or 'typical'. "
    "Output ONLY the character sheets."
)


async def build_character_sheet(chat: ChatClient, panels: list[Panel]) -> str:
    """从全部分镜提炼角色外貌卡,供每格提示词引用(跨格一致性)。"""
    digest = "\n".join(
        f"#{p.index} [{p.shot or 'medium'}] {p.scene} | characters: {', '.join(p.characters) or 'none'}"
        for p in panels
    )
    return (
        await chat.chat(
            [
                {"role": "system", "content": CHARACTER_SHEET_SYSTEM},
                {"role": "user", "content": digest},
            ],
            temperature=0.2,
        )
    ).strip()


def _clean_prompt(text: str) -> str:
    text = text.strip().strip("`")
    text = re.sub(r"^(prompt|image prompt)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


async def design_panel(
    chat: ChatClient,
    panel: Panel,
    style_anchor: str,
    character_sheet: str,
    extra_style: str = "",
    feedback: str = "",
) -> str:
    """单格提示词设计;feedback 为质检不通过时的问题反馈,重设计时会针对性修正。"""
    user = (
        f"Shot: {panel.shot or 'medium'}\n"
        f"Panel description: {panel.scene}\n"
        f"Characters in panel: {', '.join(panel.characters) or 'none'}\n"
        f"Character appearance sheet:\n{character_sheet or '(none)'}"
    )
    if feedback:
        user += (
            f"\n\nIMPORTANT — previous attempt was rejected by review with these issues, "
            f"fix them all:\n{feedback}"
        )
    raw = await chat.chat(
        [
            {"role": "system", "content": DESIGN_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
    )
    prompt = _clean_prompt(raw)
    anchor = " ".join(" ".join(x.split()) for x in (style_anchor, extra_style) if x)
    return f"{anchor}, {prompt}" if anchor else prompt


async def design_all(
    chat: ChatClient,
    panels: list[Panel],
    style_anchor: str,
    lock_characters: bool = True,
    concurrency: int = 4,
    extra_style: str = "",
    character_sheet: str | None = None,
) -> tuple[list[Panel], str]:
    """逐格设计(并发,信号量限流);返回 (panels, 角色卡)。

    character_sheet 由外部传入则复用(质检重设计时避免重复提炼)。
    """
    sheet = character_sheet
    if sheet is None:
        sheet = await build_character_sheet(chat, panels) if lock_characters else ""
    sem = asyncio.Semaphore(concurrency)

    async def one(panel: Panel) -> str:
        async with sem:
            return await design_panel(chat, panel, style_anchor, sheet, extra_style)

    prompts = await asyncio.gather(*(one(p) for p in panels))
    for panel, prompt in zip(panels, prompts):
        panel.image_prompt = prompt
    return panels, sheet
