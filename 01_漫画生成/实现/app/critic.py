"""质检器:三层质量把关(重生成循环的判定核心)。

1. review_prompt   生成前·文本质检:提示词是否忠实覆盖分镜(场景/角色/镜头/风格)
2. review_image_heuristic 生成后·无视觉启发式:可解码/尺寸正确/非纯色空白/体积合理
3. review_image_vision    生成后·视觉评分(需端点提供视觉模型,live 探测结论:
   grok-chat-fast 看不了图 → 默认关闭,config critic.vision_model 填了才启用)

质检不通过 → 由 pipeline 触发"带反馈重设计/重生成",最多 max_regens 次。
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from PIL import Image, ImageStat

from app.client import ChatClient
from app.splitter import Panel, _extract_json_array

PROMPT_QA_SYSTEM = (
    "You are a strict manga prompt reviewer. For EACH panel below, check whether its image "
    "prompt faithfully covers the panel requirements: scene content, ALL listed characters, "
    "shot type, and the anchored art style. Respond ONLY with a JSON array, one object per "
    'panel: [{"index": <int>, "pass": true/false, "score": 1-10, "issues": "brief problems '
    'or empty"}]. Score >= 8 counts as pass. No other text.'
)

VISION_QA_SYSTEM = (
    "You are a strict manga art reviewer. Look at the generated panel image and judge: "
    "(a) does it match the prompt; (b) character consistency; (c) visible defects "
    "(broken anatomy, garbled content, wrong composition). Respond ONLY with JSON: "
    '{"pass": true/false, "score": 1-10, "issues": "brief problems or empty"}.'
)


class CriticVerdict:
    __slots__ = ("passed", "score", "issues")

    def __init__(self, passed: bool, score: int, issues: str = ""):
        self.passed = passed
        self.score = score
        self.issues = issues


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


def parse_verdict(raw: str) -> CriticVerdict:
    data = _extract_json_object(raw)
    return CriticVerdict(
        passed=bool(data.get("pass", False)),
        score=int(data.get("score", 0)),
        issues=str(data.get("issues", "")),
    )


async def review_prompts(chat: ChatClient, panels: list[Panel]) -> dict[int, CriticVerdict]:
    """批量提示词质检:一次调用核对全部格,返回 {panel.index: 裁决}。

    质检自身故障(解析失败/漏格)一律自动放行 —— 质检不能成为新故障点。
    """
    if not panels:
        return {}
    user = "\n\n".join(
        f"Panel #{p.index} [{p.shot or 'medium'}]\n"
        f"- scene: {p.scene}\n"
        f"- characters (must ALL appear): {', '.join(p.characters) or 'none'}\n"
        f"- prompt: {p.image_prompt}"
        for p in panels
    )
    raw = await chat.chat(
        [{"role": "system", "content": PROMPT_QA_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.0,
    )
    auto_pass = "(critic auto-pass)"
    try:
        verdicts: dict[int, CriticVerdict] = {}
        for item in json.loads(_extract_json_array(raw)):
            verdicts[int(item["index"])] = CriticVerdict(
                passed=bool(item.get("pass", False)),
                score=int(item.get("score", 0)),
                issues=str(item.get("issues", "")),
            )
        for p in panels:  # 响应漏掉的格 → 放行
            verdicts.setdefault(p.index, CriticVerdict(True, 8, auto_pass + " missing from response"))
        return verdicts
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        return {p.index: CriticVerdict(True, 8, f"{auto_pass} parse failed: {e}") for p in panels}


async def review_image_vision(
    chat: ChatClient, vision_model: str, image_path: Path, prompt: str
) -> CriticVerdict:
    """视觉评分(需端点支持视觉模型;通过 config critic.vision_model 启用)。"""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    raw = await chat.chat(
        [
            {"role": "system", "content": VISION_QA_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Prompt was:\n{prompt}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            },
        ],
        temperature=0.0,
    )
    return parse_verdict(raw)


def review_image_heuristic(image_path: Path, expected_size: tuple[int, int]) -> CriticVerdict:
    """生成后无视觉启发式质检:损坏/尺寸/纯色空白/体积。"""
    try:
        img = Image.open(image_path)
        img.load()
    except Exception as e:
        return CriticVerdict(False, 0, f"corrupt image: {e}")
    if img.size != expected_size:
        return CriticVerdict(False, 3, f"size {img.size} != expected {expected_size}")
    stat = ImageStat.Stat(img.convert("RGB"))
    if max(stat.stddev) < 6:  # 像素方差极低 → 纯色/空白废图
        return CriticVerdict(False, 2, "image looks blank (pixel variance too low)")
    if min(img.size) >= 512 and image_path.stat().st_size < 15_000:
        # 体积检查只对生产尺寸生效(小图天然就小)
        return CriticVerdict(False, 4, f"suspiciously small file ({image_path.stat().st_size}B)")
    return CriticVerdict(True, 9, "")
