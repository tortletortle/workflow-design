"""视觉审核:把草稿图 base64 塞给视觉模型打分,不过则由 pipeline 带反馈重生成。

降级策略(质检不能成为新故障点):
- vision_model 未配置 → 直接 skipped
- 首次使用探测(1x1 测试图):调用失败/报错 → 标记不可用,本轮及以后跳过
- 审核中途调用失败 → 本轮记 error 并继续交付,不阻断流水线
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.client import ChatClient


def _probe_png() -> bytes:
    """1x1 红色像素(视觉能力探测用),运行时生成保证字节合法。"""
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()

REVIEW_SYSTEM_CUTOUT = (
    "You are a strict game asset reviewer. Look at the generated image and judge: "
    "(a) does it faithfully show the requested subject; (b) is it a SINGLE clean subject "
    "centered on a pure white background, usable for background removal; (c) visible "
    "defects (garbled content, wrong composition, cropped subject, extra objects, "
    "shadows stuck to the subject). Respond ONLY with JSON: "
    '{"pass": true/false, "score": 1-10, "issues": "brief problems or empty"}.'
)

REVIEW_SYSTEM_FULLBLEED = (
    "You are a strict game background reviewer. Look at the generated image and judge: "
    "(a) does it faithfully show the requested scene; (b) is it a COMPLETE full-bleed "
    "environment filling the whole frame (no white background, no borders, no isolated "
    "subject on empty background); (c) visible defects (garbled content, text, "
    "watermark, empty dead areas). Respond ONLY with JSON: "
    '{"pass": true/false, "score": 1-10, "issues": "brief problems or empty"}.'
)

REVIEW_SYSTEM_ANIM = (
    "You are a strict sprite frame reviewer. This is ONE frame of a game animation; "
    "the background will be removed automatically in post-processing, so IGNORE "
    "background color, floor shadows and background tint entirely. Judge ONLY: "
    "(a) a SINGLE character, complete uncropped full body, no duplicated figures; "
    "(b) the POSE matches the requested pose description; (c) no garbled content or "
    "extra elements fused into the character. Respond ONLY with JSON: "
    '{"pass": true/false, "score": 1-10, "issues": "brief problems or empty"}.'
)

REVIEW_SYSTEM_PAIR = (
    "You are a strict sprite animation reviewer. You see TWO consecutive frames of an "
    "animation cycle (frame A then frame B). Judge: (a) SAME character: same outfit, "
    "colors, proportions, art style; (b) SAME scale and facing direction (no sudden "
    "mirror/rotation/zoom); (c) the pose change from A to B is SMALL and PROGRESSIVE "
    "(one step of the cycle), NOT a jumpy large/random change. Respond ONLY with JSON: "
    '{"pass": true/false, "score": 1-10, "issues": "brief problems or empty"}.'
)


@dataclass
class ReviewVerdict:
    passed: bool
    score: int
    issues: str
    status: str = "ok"  # ok | error

    def to_dict(self) -> dict:
        return {"passed": self.passed, "score": self.score, "issues": self.issues, "status": self.status}


def parse_verdict(raw: str, min_score: int) -> ReviewVerdict:
    """容错解析审核回复;解析失败按未过处理(pipeline 会重试,而非放行)。"""
    from app.decomposer import _extract_json_object

    data = _extract_json_object(raw)
    score = int(data.get("score", 0) or 0)
    passed = bool(data.get("pass", False)) and score >= min_score
    return ReviewVerdict(passed=passed, score=score, issues=str(data.get("issues", "") or ""))


class Reviewer:
    """带能力探测缓存的视觉审核器。"""

    def __init__(self, chat: ChatClient, vision_model: str):
        self._chat = chat
        self._model = vision_model.strip()
        self._available: bool | None = None  # None = 未探测
        self.probe_error: str = ""           # 探测失败原因(供结果透传诊断)

    @property
    def model(self) -> str:
        return self._model

    async def available(self) -> bool:
        """视觉模型可用性:配置为空直接 False;否则发 1x1 测试图探测(瞬时抖动重试一次)并缓存。"""
        if not self._model:
            return False
        if self._available is not None:
            return self._available
        b64 = base64.b64encode(_probe_png()).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply with the single word: ok"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ]
        last_err = ""
        for _ in range(2):  # 单次瞬时断连不应把视觉能力缓存成"永久不可用"
            try:
                await self._chat.chat(
                    messages, temperature=0.0,
                    max_tokens=64,  # 推理型模型会先消耗 reasoning token,给足余量
                    model=self._model,
                )
                self._available = True
                return True
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:160]}"
        self._available = False  # 端点无视觉能力(连接上游失败/接口不存在等)
        self.probe_error = last_err
        return False

    async def review(self, image_path: Path, subject: str, min_score: int = 8,
                     category: str = "item") -> ReviewVerdict:
        """视觉打分(按类别适配标准)。调用/解析失败返回 status=error 的裁决(不抛错,不阻断)。

        anim_frame:动画帧专用口径——背景交给抠图后处理,只判姿势/单主体/完整全身,
        避免编辑通道的地面阴影/底色偏差触发无意义重生成(重生成也修不掉)。
        """
        from app.decomposer import NO_CUTOUT_CATEGORIES

        if category in NO_CUTOUT_CATEGORIES:
            system = REVIEW_SYSTEM_FULLBLEED
        elif category == "anim_frame":
            system = REVIEW_SYSTEM_ANIM
        else:
            system = REVIEW_SYSTEM_CUTOUT
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Requested subject: {subject}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            },
        ]
        try:
            raw = await self._chat.chat(messages, temperature=0.0, max_tokens=1500,
                                        model=self._model)
            try:
                return parse_verdict(raw, min_score)
            except (ValueError, json.JSONDecodeError):
                # 推理型模型偶发把 token 全烧在 reasoning 上正文为空 → 加倍余量重试一次
                raw2 = await self._chat.chat(messages, temperature=0.0, max_tokens=3000,
                                             model=self._model)
                return parse_verdict(raw2, min_score)
        except Exception as e:
            self._available = False  # 探测通过但正式调用挂了 → 后续轮次不再浪费时间
            return ReviewVerdict(False, 0, f"vision review error: {str(e)[:160]}", status="error")

    async def review_pair(self, path_a: Path, path_b: Path, criterion: str,
                          min_score: int = 8) -> ReviewVerdict:
        """相邻帧连贯性审核:两帧同消息送审(同一角色/同比例朝向/姿势小幅渐进)。"""
        def _part(p: Path) -> dict:
            b64 = base64.b64encode(p.read_bytes()).decode()
            return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

        try:
            raw = await self._chat.chat(
                [
                    {"role": "system", "content": REVIEW_SYSTEM_PAIR},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"Animation context: {criterion}\nFrame A:"},
                        _part(path_a),
                        {"type": "text", "text": "Frame B:"},
                        _part(path_b),
                    ]},
                ],
                temperature=0.0,
                max_tokens=1200,
                model=self._model,
            )
            try:
                return parse_verdict(raw, min_score)
            except (ValueError, json.JSONDecodeError):
                raw2 = await self._chat.chat(
                    [{"role": "system", "content": REVIEW_SYSTEM_PAIR},
                     {"role": "user", "content": [
                         {"type": "text", "text": f"Animation context: {criterion}\nFrame A (retry):"},
                         _part(path_a),
                         {"type": "text", "text": "Frame B:"},
                         _part(path_b),
                     ]}],
                    temperature=0.0, max_tokens=2400, model=self._model)
                return parse_verdict(raw2, min_score)
        except Exception as e:
            return ReviewVerdict(False, 0, f"pair review error: {str(e)[:160]}", status="error")
