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
    "You are a strict manga prompt reviewer. For EACH panel, evaluate the image prompt quality:\n\n"
    "Check if the prompt:\n"
    "1. Includes ALL required characters with specific appearances (not generic descriptions)\n"
    "2. Describes the scene with rich environmental detail (not just 'a room')\n"
    "3. Specifies lighting and atmosphere appropriate for the mood\n"
    "4. Follows the shot type with proper composition guidance\n"
    "5. Contains 100+ words with concrete visual details (colors, textures, spatial relationships)\n"
    "6. Maintains consistency with the art style anchor\n\n"
    "Scoring: 9-10 = excellent detail; 7-8 = adequate but could be richer; <7 = too vague/missing key elements.\n"
    "Pass threshold: score >= 9 (we demand high quality).\n\n"
    'Respond ONLY with JSON array: [{"index": <int>, "pass": true/false, "score": 1-10, '
    '"issues": "specific problems or empty"}]. No other text.'
)

VISION_QA_SYSTEM = (
    "You are a strict manga art reviewer. Evaluate the generated image on multiple dimensions:\n\n"
    "1. PROMPT MATCH: Does the image accurately reflect the prompt's scene, characters, and details?\n"
    "2. CHARACTER CONSISTENCY: Do characters match their established appearance? Same face, hair, outfit?\n"
    "3. TECHNICAL QUALITY: Check for broken anatomy, garbled content, artifacts, distortions\n"
    "4. COMPOSITION: Is the framing effective? Good use of space, clear focal point?\n"
    "5. ARTISTIC MERIT: Does it have visual appeal? Appropriate mood, lighting, atmosphere?\n\n"
    "Scoring: 9-10 = excellent on all dimensions; 7-8 = good but minor issues; 5-6 = noticeable problems; "
    "<5 = major defects or completely wrong.\n"
    "Pass threshold: score >= 8 (high quality bar).\n\n"
    'Respond ONLY with JSON: {"pass": true/false, "score": 1-10, "issues": "specific problems or empty"}.'
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
            verdicts.setdefault(p.index, CriticVerdict(True, 9, auto_pass + " missing from response"))
        return verdicts
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        return {p.index: CriticVerdict(True, 9, f"{auto_pass} parse failed: {e}") for p in panels}


async def review_image_vision(
    chat: ChatClient, vision_model: str, image_path: Path, prompt: str
) -> CriticVerdict:
    """视觉评分(需端点支持视觉模型;通过 config critic.vision_model 启用)。

    chat 传入的是主 ChatClient;若 env 配了 vision_base_url/vision_api_key,
    则临时构建独立客户端直连视觉端点(绕过 newapi 包装层)。
    """
    from app.client import ChatClient as _CC  # 局部导入避免循环

    b64 = base64.b64encode(image_path.read_bytes()).decode()
    messages = [
        {"role": "system", "content": VISION_QA_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Prompt was:\n{prompt}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        },
    ]
    client_to_use = chat
    temp_client = None
    if chat._vision_base_url:
        temp_client = _CC.__new__(_CC)
        import httpx as _httpx

        temp_client._client = _httpx.AsyncClient(
            base_url=chat._vision_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {chat._vision_api_key}"},
            timeout=_httpx.Timeout(120.0, connect=10.0),
        )
        temp_client.model = vision_model
        client_to_use = temp_client

    try:
        raw = await client_to_use.chat(messages, temperature=0.0, model=vision_model or None)
        return parse_verdict(raw)
    finally:
        if temp_client:
            await temp_client.aclose()


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
