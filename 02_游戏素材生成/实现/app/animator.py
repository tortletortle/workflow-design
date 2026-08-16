"""动画帧生成:角色卡锁外观 + 逐帧姿势提示词 → 多帧素材 → 归一化对齐 → 拼条/GIF。

帧间一致性的四板斧(文生图没有"帧"概念,必须主动压漂移):
1. 姿势专属提示词:每帧写死该帧的具体姿势描述,不交给 LLM 自由发挥
2. 图生图条件生成(④):第 0 帧文生图定锚,后续帧编辑锚帧"同角色仅改姿势",
   外观由参考图条件化(需配 ASSET_EDIT_*;未配/失败自动回退逐帧文生图)
3. 逐帧视觉审核:审核该帧"姿势是否符合 + 单主体白底",不过带反馈重生成;
   相邻帧连贯性审核抓"跳变帧"
4. 后期归一化:每帧裁透明边 → 等比缩放到统一主体高度 → 底边中线对齐
   (比例/站位漂移在绘制时被压平,播放时基本无抖动)

复用现有模块:主体卡(consistency)、生图+质检(generator)、图生图编辑(EditClient)、
视觉审核(reviewer)、抠图(cutout)。帧提示词不走 LLM 分解——卡里已有主体描述,
直接拼模板更可控。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

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
    # 动作专属视角/构图提示;空 = 默认正面全身(CHARACTER_TAIL)
    view: str = ""


ANIM_ACTIONS: dict[str, ActionDef] = {
    "walk": ActionDef(poses=[
        "standing neutral pose, legs together, arms resting at sides",
        "mid-stride, left leg lifted stepping forward, arms swinging naturally",
        "contact pose, legs passing each other mid-cycle, arms centered",
        "mid-stride, right leg lifted stepping forward, arms swinging opposite",
    ]),
    "idle": ActionDef(poses=[
        "standing relaxed, subtle breathing pose, weight even on both legs",
        "standing relaxed, slight weight shift, chest slightly expanded",
    ]),
    "attack": ActionDef(poses=[
        "winding up, weapon raised high above head, body coiled",
        "mid-swing, weapon sweeping forward, body leaning into the strike",
        "follow-through, weapon lowered after impact, body settling",
    ]),
    # 侧视走路:姿势梯度更细(右脚在前↔左脚在前 ×2 交替),可读性远好于正面走。
    # 描述用可见几何而非动画行话(contact/down/passing),编辑模型按行话会蹲成"下蹲"
    "walk_side": ActionDef(
        view=("full body side profile view facing right, walking toward the right, "
              "legs and arms clearly separated in stride, complete figure"),
        poses=[
            "stepping forward, right foot flat on the ground ahead, left heel lifting behind, arms swinging naturally",
            "mid-stride, both legs under the body, back heel lifted, shoulders level",
            "stepping forward, left foot flat on the ground ahead, right heel lifting behind, arms swinging opposite",
            "stepping forward, left foot flat on the ground ahead, right heel lifting behind, arms swinging naturally",
            "mid-stride, both legs under the body, back heel lifted, shoulders level",
            "stepping forward, right foot flat on the ground ahead, left heel lifting behind, arms swinging opposite",
        ],
    ),
}

CHARACTER_TAIL = f"{CATEGORY_HINTS['character']}, consistent proportions and framing across the series"

# 图生图编辑指令:锚帧(第 0 帧)已定死角色外观,后续帧只允许改姿势。
# 不变量(同角色/同风格/同构图同比例)显式写死;背景与阴影也要显式禁止——
# 编辑模型有自发加地面阴影/底色渐变的倾向,靠默认行为守不住纯白底
EDIT_INSTRUCTION = (
    "Keep the exact same character: identical design, colors, outfit, proportions, art "
    "style, framing, scale and centered full-body composition. Only change the pose to: "
    "{pose}. Keep a completely flat uniform pure white background with no floor shadow, "
    "no gradient, no color tint, and do not add any new elements or text."
)


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
    edit_used: bool = False  # 后续帧是否走了图生图编辑(锚帧条件生成)


def frame_prompt(subject: str, style: str, pose: str, view: str = "") -> str:
    tail = view or CHARACTER_TAIL
    parts = [subject, style or "clean game art style", tail, pose, WHITE_BG_SUFFIX]
    return ", ".join(p.strip().rstrip(".") for p in parts if p and p.strip())


async def generate_frames(
    subject_ref: str,
    action: str = "walk",
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
    """生成一个动作的多帧素材。

    帧生成两级策略(④图生图落地):
    - 第 0 帧文生图定锚(姿势提示词 + 角色卡锁外观);
    - 后续帧优先图生图编辑锚帧("同角色仅改姿势"),外观由参考图条件化,
      漂移在生成阶段被压住;编辑通道失败自动回退逐帧文生图并记 notes。
    生成后仍有逐帧姿势审核 + 相邻帧连贯性审核 + 归一化对齐三道保险。
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
        action = (action or "walk").strip().lower()
        action_def = ANIM_ACTIONS.get(action)
        if not action_def:
            raise ValueError(f"unknown action {action!r}; available: {', '.join(ANIM_ACTIONS)}")
        poses, view = action_def.poses, action_def.view
        card = load_subject(subject_ref, cfg)
        if not card or not card.get("card"):
            raise ValueError(f"subject card not found: {subject_ref!r} (generate with "
                             f"save_subject_as first)")
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

        anchor: Path | None = None  # 锚帧 = 第 0 帧产物(白底原图,后续帧编辑它)
        edit_failed = False  # 编辑通道失败后置位,本任务余下帧全走文生图

        async def gen_frame(i: int, pose: str, fix: str = ""):
            """单帧生成:编辑通道可用且非锚帧 → 编辑锚帧;否则文生图。fix 非空 = 带审核反馈重生成。"""
            nonlocal edit_failed
            if edit is not None and not edit_failed and i > 0 and anchor is not None:
                instruction = EDIT_INSTRUCTION.format(pose=pose)
                if fix:
                    instruction += f" Fix: {fix}"
                try:
                    gen = await edit_with_qa(edit, instruction, anchor,
                                             task_dir / f"frame_{i}.png", cfg.image.retries)
                    result.edit_used = True
                    return gen
                except Exception as e:
                    edit_failed = True
                    result.notes.append(f"frame{i}: edit channel failed "
                                        f"({str(e)[:80]}), fallback to text-to-image")
            prompt = frame_prompt(subject, style, f"{pose}. Fix: {fix}" if fix else pose, view)
            return await generate_with_qa(image, prompt, task_dir / f"frame_{i}.png",
                                          cfg.image.size, cfg.image.retries)

        for i, pose in enumerate(poses):
            gen = await gen_frame(i, pose)
            result.image_calls += gen.attempts
            if i == 0:
                anchor = gen.path
            if reviewer is not None:
                for r in range(cfg.review.max_rounds + 1):
                    verdict = await reviewer.review(gen.path, f"{subject}; pose: {pose}",
                                                    cfg.review.min_score, category="anim_frame")
                    if verdict.passed or verdict.status == "error" or r == cfg.review.max_rounds:
                        result.review_status = "ok" if verdict.passed else (
                            "error" if verdict.status == "error" else "partial")
                        if not verdict.passed and verdict.issues:
                            result.notes.append(f"frame{i}: {verdict.issues[:80]}")
                        break
                    # 姿势不符 → 带反馈重生成该帧
                    gen = await gen_frame(i, pose, fix=verdict.issues)
                    result.image_calls += gen.attempts
                    if i == 0:
                        anchor = gen.path
            result.frames.append(gen.path)

        # 相邻帧连贯性审核:两帧同消息送审,抓"跳变帧"(每对最多重生成后帧 1 次)
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
                pose_next = poses[i + 1]
                regen = await gen_frame(i + 1, pose_next,
                                        fix=f"coherence with previous frame: {v.issues}")
                result.image_calls += regen.attempts
                v2 = await reviewer.review_pair(result.frames[i], result.frames[i + 1],
                                                criterion, cfg.review.min_score)
                if not v2.passed:
                    result.notes.append(f"pair{i}-{i+1}: { (v2.issues or v.issues)[:80]}")
                    result.review_status = "partial"

        # 后期归一化 + 拼条 + GIF(线程池)
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
    """裁透明边 → 主体等比缩放到统一高度 → 底边中线对齐(压帧间漂移)。"""
    rgba = frame.convert("RGBA")
    bbox = rgba.split()[-1].getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    scale = subject_height / rgba.height
    w, h = max(1, round(rgba.width * scale)), subject_height
    rgba = rgba.resize((w, h), Image.LANCZOS)
    base = round(canvas * 0.94)  # 统一基线:底部留 6% 边距
    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(rgba, ((canvas - w) // 2, base - h), rgba)
    return out


def _assemble(result: AnimResult, task_dir: Path, size_px: int, cfg: AppConfig) -> None:
    frames = [Image.open(p) for p in result.frames]
    cutouts = [cutout_mod.cutout(f, cfg.cutout)[0] for f in frames]
    normed = [_normalize(c, round(size_px * 0.84), size_px) for c in cutouts]
    # 归一化帧单独落盘(直接可用),原图 frame_i.png 留作审计
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
    # 透明 GIF 预览(经典调色板法:index 255 = 透明)
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
