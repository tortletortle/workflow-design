"""动画帧生成 v2: 参考SpriteForge架构改进

核心改进:
1. 更多帧数: walk 16帧(2步×8帧/步), idle 8帧, 互动6-8帧
2. 渐进式姿势提示词: 每帧描述精确到可见几何,帧间变化量小且可控
3. 重试升级: soft→guided→constrained 三级重试(最多10次)
4. 锚帧锁定: 第0帧为身份锚点,后续帧Gate 1强制对比一致性
5. 帧间连续性: Gate 2检查相邻帧平滑度
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from app import cutout as cutout_mod
from app.client import ChatClient, EditClient, ImageClient
from app.consistency import load_subject
from app.decomposer import WHITE_BG_SUFFIX, CATEGORY_HINTS
from app.generator import edit_with_qa, generate_with_qa
from app.reviewer import Reviewer
from app.settings import AppConfig, EnvSettings, PROJECT_ROOT

from dataclasses import dataclass as _dc


@_dc
class ActionDef:
    poses: list[str]
    view: str = ""


# ====== 侧视走路: 16帧 = 2步 × 8帧/步 ======
# 每帧描述精确到可见几何,帧间变化量控制在最小
# 关键: 用"脚离地几厘米""膝盖弯曲角度"等具体描述,不用动画行话
WALK_SIDE_16 = ActionDef(
    view=("full body side profile view facing right, walking toward the right, "
          "legs and arms clearly visible, complete figure from head to feet"),
    poses=[
        # === 第1步: 右脚起步 ===
        "standing upright, right foot flat on ground ahead, left foot flat on ground behind, weight on right leg, arms relaxed at sides",
        "right leg bearing weight, left heel lifting off ground, left knee starting to bend, right arm swinging forward slightly",
        "left foot completely off ground, left knee bent 90 degrees, thigh parallel to ground, right arm forward, left arm back",
        "left leg extending forward, left foot moving ahead, right leg straight, both arms passing through center",
        "left foot landing flat on ground ahead, right heel lifting, weight transferring to left leg",
        "right foot off ground, right knee bent 90 degrees, thigh parallel to ground, left arm forward, right arm back",
        "right leg extending forward, right foot moving ahead, left leg straight, both arms passing through center",
        "right foot landing flat on ground ahead, left heel lifting, weight transferring to right leg",
        # === 第2步: 左脚起步(镜像) ===
        "standing upright, left foot flat on ground ahead, right foot flat on ground behind, weight on left leg, arms relaxed at sides",
        "left leg bearing weight, right heel lifting off ground, right knee starting to bend, left arm swinging forward slightly",
        "right foot completely off ground, right knee bent 90 degrees, thigh parallel to ground, left arm forward, right arm back",
        "right leg extending forward, right foot moving ahead, left leg straight, both arms passing through center",
        "right foot landing flat on ground ahead, left heel lifting, weight transferring to right leg",
        "left foot off ground, left knee bent 90 degrees, thigh parallel to ground, right arm forward, left arm back",
        "left leg extending forward, left foot moving ahead, right leg straight, both arms passing through center",
        "left foot landing flat on ground ahead, right heel lifting, weight transferring to left leg",
    ],
)

# ====== 待机: 8帧呼吸循环 ======
IDLE_8 = ActionDef(poses=[
    "standing relaxed, weight evenly distributed on both feet, arms at sides, neutral calm expression",
    "standing relaxed, chest slightly expanding on inhale, shoulders rising 1cm, subtle breath",
    "standing relaxed, peak of inhale, chest fully expanded, shoulders at highest point",
    "standing relaxed, beginning exhale, chest settling, shoulders dropping slightly",
    "standing relaxed, exhaling, chest returning to normal, weight shifting slightly to left foot",
    "standing relaxed, weight on left foot, right hip slightly raised, subtle lean",
    "standing relaxed, weight returning to center, right foot flat again",
    "standing relaxed, completing breath cycle, back to neutral balanced pose",
])

# ====== 互动动作: 6帧 ======
HAPPY_6 = ActionDef(poses=[
    "standing neutral, slight smile appearing, eyes brightening, hands at sides",
    "standing, gentle smile, one hand rising to chest level, fingers spread",
    "standing, happy expression, hand near chin in cute pose, slight head tilt",
    "standing, bright smile, both hands clasped together in front of chest",
    "standing, cheerful expression, hands squeezing together, shoulders raised slightly",
    "standing, content smile, hands returning to sides, relaxed happy pose",
])

WAVE_6 = ActionDef(poses=[
    "standing, right arm starting to lift from side, palm opening, friendly expression",
    "standing, right arm raised to shoulder height, elbow bent, palm facing forward",
    "standing, right arm raised above head, palm open waving right, bright smile",
    "standing, right arm above head, palm waving left, fingers spread",
    "standing, right arm coming down to shoulder height, wave finishing",
    "standing, right arm returning to side, warm expression, wave complete",
])

HEART_6 = ActionDef(poses=[
    "standing, both hands moving together in front of chest, fingers touching",
    "standing, both hands forming diamond shape near chest, slight blush appearing",
    "standing, heart shape completed with both hands, pushing forward slightly",
    "standing, heart shape held out toward viewer, warm smile, sparkling eyes",
    "standing, heart shape fading, hands separating, still smiling",
    "standing, hands returning to sides, content warm expression",
])

JUMP_8 = ActionDef(poses=[
    "standing, crouching down, knees bent deeply, arms pulled back, preparing to jump",
    "mid-jump, leaving ground, legs straightening, arms swinging upward, dynamic pose",
    "ascending, body rising, legs tucking slightly, arms raised high, joyful expression",
    "peak of jump, highest point, body fully extended upward, arms spread wide",
    "descending, body coming down, legs extending, arms coming down for balance",
    "landing, knees bent absorbing impact, arms out to sides, feet touching ground",
    "standing upright, recovering from landing, knees straightening, arms settling",
    "standing normal, happy expression after successful jump, back to neutral pose",
])

SPEAK_6 = ActionDef(poses=[
    "standing, mouth opening to speak, one hand rising to gesture, calm expression",
    "standing, talking, mouth open, hand raised with index finger pointing up",
    "standing, talking, hand moving to side gesture, explaining something",
    "standing, talking, hand returning near chest, mouth still open mid-sentence",
    "standing, finishing sentence, hand settling, mouth closing slightly",
    "standing, finished speaking, hand at side, calm polite expression",
])


# 覆盖内置动作
ANIM_ACTIONS: dict[str, ActionDef] = {
    "walk_side": WALK_SIDE_16,
    "idle": IDLE_8,
    "happy": HAPPY_6,
    "wave": WAVE_6,
    "heart": HEART_6,
    "jump": JUMP_8,
    "speak": SPEAK_6,
}

CHARACTER_TAIL = f"{CATEGORY_HINTS['character']}, consistent proportions and framing across the series"

# 图生图编辑指令: 更严格的不变量约束
EDIT_INSTRUCTION = (
    "Keep the EXACT same character: identical design, colors, outfit, proportions, art "
    "style, framing, scale and centered full-body composition. Only change the pose to: "
    "{pose}. Keep a completely flat uniform pure white background with no floor shadow, "
    "no gradient, no color tint, and do not add any new elements or text. "
    "The character must remain in the same position in the frame."
)

# 重试升级配置(参考SpriteForge)
RETRY_TIERS = [
    {"name": "soft", "attempts": (1, 3), "temp": 1.0, "guidance": ""},
    {"name": "guided", "attempts": (4, 6), "temp": 0.7,
     "guidance": "Focus on matching the exact pose description. Pay attention to arm and leg positions."},
    {"name": "constrained", "attempts": (7, 10), "temp": 0.3,
     "guidance": "CRITICAL: You must match the pose EXACTLY. Do not deviate from the description."},
]


@dataclass
class AnimResult:
    action: str
    subject: str
    frames: list[Path] = field(default_factory=list)
    spritesheet: Path | None = None
    gif: Path | None = None
    image_calls: int = 0
    notes: list[str] = field(default_factory=list)
    review_status: str = "skipped"
    edit_used: bool = False


def frame_prompt(subject: str, style: str, pose: str, view: str = "") -> str:
    tail = view or CHARACTER_TAIL
    parts = [subject, style or "clean game art style", tail, pose, WHITE_BG_SUFFIX]
    return ", ".join(p.strip().rstrip(".") for p in parts if p and p.strip())


async def generate_frames(
    subject_ref: str,
    action: str = "walk_side",
    size_px: int = 128,
    env: EnvSettings | None = None,
    cfg: AppConfig | None = None,
    chat: ChatClient | None = None,
    image: ImageClient | None = None,
    vision_chat: ChatClient | None = None,
    edit: EditClient | None = None,
    out_root: Path | None = None,
    review: bool = True,
) -> AnimResult:
    """生成一个动作的多帧素材(改进版)。

    改进:
    - 更多帧数(16帧walk, 8帧idle)
    - 渐进式姿势提示词
    - 重试升级机制(soft→guided→constrained)
    - 锚帧身份锁定
    - 严格的帧间连续性检查
    """
    from datetime import datetime

    env = env or EnvSettings()
    cfg = cfg or AppConfig()
    own_chat = chat is None
    chat = chat or ChatClient(env)
    own_image = image is None
    image = image or ImageClient(env)
    own_edit = False
    if edit is None and env.edit_api_key:
        edit = EditClient(env)
        own_edit = True
    own_vision = False
    if vision_chat is None and env.vision_model and review:
        v_env = env.model_copy(update={
            "base_url": (env.vision_base_url or env.base_url).rstrip("/"),
            "api_key": env.vision_api_key or env.api_key,
        })
        vision_chat = ChatClient(v_env)
        own_vision = True
    try:
        action = (action or "walk_side").strip().lower()
        action_def = ANIM_ACTIONS.get(action)
        if not action_def:
            raise ValueError(f"unknown action {action!r}; available: {', '.join(ANIM_ACTIONS)}")
        poses, view = action_def.poses, action_def.view
        card = load_subject(subject_ref, cfg)
        if not card or not card.get("card"):
            raise ValueError(f"subject card not found: {subject_ref!r}")

        subject, style = card["card"], card.get("style", "")

        out_root = out_root or (PROJECT_ROOT / cfg.output.dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        from app.consistency import slugify
        task_dir = out_root / f"{stamp}_anim_{slugify(subject_ref)}_{action}"
        task_dir.mkdir(parents=True, exist_ok=True)

        result = AnimResult(action=action, subject=subject_ref)
        reviewer = Reviewer(vision_chat, env.vision_model) if (review and vision_chat) else None
        if reviewer is not None and not await reviewer.available():
            result.notes.append("vision unavailable, per-frame review skipped")
            reviewer = None
        if reviewer is None:
            result.review_status = "skipped"

        anchor: Path | None = None  # 锚帧 = 第0帧(身份锚点)
        edit_failed = False

        def get_retry_tier(attempt: int) -> dict:
            """根据尝试次数返回重试配置"""
            for tier in RETRY_TIERS:
                lo, hi = tier["attempts"]
                if lo <= attempt <= hi:
                    return tier
            return RETRY_TIERS[-1]  # 默认最严格

        async def gen_frame(i: int, pose: str, fix: str = "", attempt: int = 1):
            """单帧生成: 带重试升级"""
            nonlocal edit_failed
            tier = get_retry_tier(attempt)

            if edit is not None and not edit_failed and i > 0 and anchor is not None:
                instruction = EDIT_INSTRUCTION.format(pose=pose)
                if fix:
                    instruction += f" Fix: {fix}"
                if tier["guidance"]:
                    instruction += f" {tier['guidance']}"
                try:
                    gen = await edit_with_qa(edit, instruction, anchor,
                                             task_dir / f"frame_{i}.png", cfg.image.retries)
                    result.edit_used = True
                    return gen
                except Exception as e:
                    edit_failed = True
                    result.notes.append(f"frame{i}: edit channel failed ({str(e)[:80]}), fallback to text-to-image")

            prompt = frame_prompt(subject, style, f"{pose}. Fix: {fix}" if fix else pose, view)
            if tier["guidance"]:
                prompt += f". {tier['guidance']}"
            return await generate_with_qa(image, prompt, task_dir / f"frame_{i}.png",
                                          cfg.image.size, cfg.image.retries)

        for i, pose in enumerate(poses):
            gen = await gen_frame(i, pose)
            result.image_calls += gen.attempts
            if i == 0:
                anchor = gen.path

            # 逐帧审核 + 重试升级
            if reviewer is not None:
                for attempt in range(1, cfg.review.max_rounds + 2):  # 最多max_rounds+1次
                    verdict = await reviewer.review(gen.path, f"{subject}; pose: {pose}",
                                                    cfg.review.min_score, category="anim_frame")
                    if verdict.passed or verdict.status == "error":
                        result.review_status = "ok" if verdict.passed else "error"
                        if not verdict.passed and verdict.issues:
                            result.notes.append(f"frame{i}: {verdict.issues[:80]}")
                        break
                    if attempt > cfg.review.max_rounds:
                        # 超过重试次数,记录问题但继续
                        result.notes.append(f"frame{i}: max retries reached, issues: {verdict.issues[:80]}")
                        result.review_status = "partial"
                        break
                    # 带反馈重生成(升级重试)
                    gen = await gen_frame(i, pose, fix=verdict.issues, attempt=attempt + 1)
                    result.image_calls += gen.attempts
                    if i == 0:
                        anchor = gen.path

            result.frames.append(gen.path)

        # 相邻帧连贯性审核(Gate 2: 时间连续性)
        if reviewer is not None and len(result.frames) > 2:
            criterion = f"{subject}; consecutive frames of a '{action}' cycle, view: {view or 'front 3/4'}"
            for i in range(len(result.frames) - 1):
                v = await reviewer.review_pair(result.frames[i], result.frames[i + 1],
                                               criterion, cfg.review.min_score)
                if v.status == "error":
                    result.notes.append(f"pair{i}-{i+1}: review error, skipped")
                    continue
                if v.passed:
                    continue
                # 连续性失败,重生成后帧
                pose_next = poses[i + 1]
                for attempt in range(1, 4):  # 最多3次重试
                    regen = await gen_frame(i + 1, pose_next,
                                            fix=f"coherence with previous frame: {v.issues}",
                                            attempt=attempt)
                    result.image_calls += regen.attempts
                    v2 = await reviewer.review_pair(result.frames[i], result.frames[i + 1],
                                                    criterion, cfg.review.min_score)
                    if v2.passed:
                        break
                    if attempt == 3:
                        result.notes.append(f"pair{i}-{i+1}: still failing after 3 retries: {(v2.issues or v.issues)[:80]}")
                        result.review_status = "partial"

        # 后期归一化 + 拼条 + GIF
        await asyncio.to_thread(_assemble, result, task_dir, size_px, cfg)
        result.spritesheet = task_dir / "spritesheet.png"
        result.gif = task_dir / "preview.gif"
        _write_meta(task_dir, result, cfg)
        return result
    finally:
        if own_chat:
            await chat.aclose()
        if own_image:
            await image.aclose()
        if own_edit:
            await edit.aclose()
        if own_vision:
            await vision_chat.aclose()


def _normalize(frame: Image.Image, subject_height: int, canvas: int) -> Image:
    """裁白底/透明边 → 主体等比缩放到统一高度 → 底边中线对齐"""
    rgba = frame.convert("RGBA")
    alpha = rgba.split()[3]
    bbox = alpha.getbbox()
    
    # 如果alpha通道没有透明区域(白底图)，用亮度检测裁剪白底
    if bbox is None or (bbox == (0, 0, rgba.width, rgba.height) and alpha.getextrema()[0] == 255):
        # 白底图：找非白区域
        rgb = rgba.convert("RGB")
        arr = np.array(rgb)
        non_white = (arr[:,:,0] < 245) | (arr[:,:,1] < 245) | (arr[:,:,2] < 245)
        if np.sum(non_white) > 0:
            rows, cols = np.where(non_white)
            bbox = (cols.min(), rows.min(), cols.max() + 1, rows.max() + 1)
        else:
            bbox = None
    
    if bbox:
        rgba = rgba.crop(bbox)
    scale = subject_height / rgba.height
    w, h = max(1, round(rgba.width * scale)), subject_height
    rgba = rgba.resize((w, h), Image.LANCZOS)
    base = round(canvas * 0.94)
    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(rgba, ((canvas - w) // 2, base - h), rgba)
    return out


def _assemble(result: AnimResult, task_dir: Path, size_px: int, cfg: AppConfig,
              skip_cutout: bool = True) -> None:
    """组装动画帧。skip_cutout=True时跳过抠图(避免白色衣物被扣掉)。"""
    frames = [Image.open(p) for p in result.frames]
    if skip_cutout:
        # 不抠图，直接转RGBA(白底保留)
        cutouts = [f.convert("RGBA") for f in frames]
    else:
        cutouts = [cutout_mod.cutout(f, cfg.cutout)[0] for f in frames]
    normed = [_normalize(c, round(size_px * 0.84), size_px) for c in cutouts]
    norm_dir = task_dir / "norm"
    norm_dir.mkdir(exist_ok=True)
    for i, fr in enumerate(normed):
        fr.save(norm_dir / f"frame_{i}.png", "PNG")
    result.frames = [norm_dir / f"frame_{i}.png" for i in range(len(normed))]
    # 横向拼条
    sheet = Image.new("RGBA", (size_px * len(normed), size_px), (0, 0, 0, 0))
    for i, fr in enumerate(normed):
        sheet.paste(fr, (i * size_px, 0))
    sheet.save(task_dir / "spritesheet.png", "PNG")
    # 透明 GIF 预览
    gif_frames = []
    for fr in normed:
        alpha = fr.split()[3]
        p = fr.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
        mask = alpha.point(lambda a: 255 if a <= 128 else 0)
        p.paste(255, mask)
        gif_frames.append(p)
    gif_frames[0].save(task_dir / "preview.gif", save_all=True,
                       append_images=gif_frames[1:], duration=140, loop=0,
                       disposal=2, transparency=255)


def _write_meta(task_dir: Path, result: AnimResult, cfg: AppConfig) -> None:
    import json

    payload = {
        "task_id": task_dir.name,
        "status": "ok",
        "type": "animation",
        "action": result.action,
        "subject": result.subject,
        "edit": result.edit_used,
        "image_calls": result.image_calls,
        "review": {"status": result.review_status},
        "frames": [str(p.resolve()) for p in result.frames],
        "spritesheet": str((task_dir / "spritesheet.png").resolve()),
        "preview_gif": str((task_dir / "preview.gif").resolve()),
        "notes": result.notes,
    }
    (task_dir / "task.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
