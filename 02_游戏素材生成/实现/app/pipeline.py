"""流水线编排:分解 → 白底/满幅生图 → 视觉审核(可降级) → 抠图(满幅类别跳过) → 多尺寸输出。

每个任务独立目录 out/<时间戳>_<slug>/:draft.png / cutout.png / master.png /
finals/<slug>_<size>.png / task.json(元数据,供 list_assets 汇总)。

一致性:
- 全局风格锚(config.yaml style.anchor)与风格库引用统一在 resolve_style 解析;
- subject_ref 引用主体卡(锁定外观),save_subject_as 把当次主体描述冻结成卡。

视觉审核可走独立服务商(vision_base_url/vision_api_key),与主端点分离。
重 IO/CPU 的图像后处理放线程池执行,不阻塞 MCP 事件循环。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from app import cutout as cutout_mod
from app import scaler
from app.client import ChatClient, EditClient, ImageClient
from app.consistency import (list_library, load_subject, resolve_style, save_style,
                             save_subject, slugify)
from app.decomposer import CATEGORIES, NO_CUTOUT_CATEGORIES, AssetSpec, decompose
from app.generator import edit_with_qa, generate_with_qa
from app.reviewer import Reviewer
from app.settings import AppConfig, EnvSettings, PROJECT_ROOT, normalize_sizes


def _new_task_dir(out_root: Path, slug: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_dir = out_root / f"{stamp}_{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def _postprocess(draft_path: Path, task_dir: Path, sizes: list[int], slug: str,
                 cfg: AppConfig, cutout_enabled: bool = True) -> dict:
    """抠图 + 裁边 + 多尺寸输出(同步重计算,由调用方放线程)。

    满幅类别(background/tile)不抠图:直接整图缩放输出,保留完整画面。
    """
    img = Image.open(draft_path)
    img.load()
    if cutout_enabled:
        cut, method = cutout_mod.cutout(img, cfg.cutout)
        cutout_path = task_dir / "cutout.png"
        cut.save(cutout_path, "PNG")
        trimmed = scaler.trim_to_alpha(cut)
        padding = cfg.cutout.padding_ratio
    else:
        cut, method = None, "skipped(fullbleed category)"
        trimmed = img.convert("RGBA")
        padding = 0.0  # 满幅铺满画布,不留边距
    master_path = scaler.save_master(trimmed, task_dir / "master.png")
    finals = scaler.save_finals(trimmed, sizes, task_dir / "finals", slug, padding)
    cutout_info = ({"path": str(cutout_path.resolve()), "method": method}
                   if cut is not None else {"method": method})
    return {"cutout": cutout_info, "master": master_path, "finals": finals}


def _write_task_json(task_dir: Path, payload: dict) -> None:
    (task_dir / "task.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def generate_asset_pipeline(
    description: str,
    category: str = "item",
    style: str = "",
    sizes: list[int] | None = None,
    feedback: str = "",
    subject_ref: str = "",
    save_subject_as: str = "",
    save_style_as: str = "",
    env: EnvSettings | None = None,
    cfg: AppConfig | None = None,
    chat: ChatClient | None = None,
    image: ImageClient | None = None,
    vision_chat: ChatClient | None = None,
    edit: EditClient | None = None,
    out_root: Path | None = None,
) -> dict:
    """主流程:一条龙生成游戏素材。chat/image/vision_chat/edit 可注入(测试),缺省按 .env 自建。"""
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
    if vision_chat is None and env.vision_model:
        # 视觉模型可挂独立服务商;未配独立端点则与主端点共用
        v_env = env.model_copy(update={
            "base_url": (env.vision_base_url or env.base_url).rstrip("/"),
            "api_key": env.vision_api_key or env.api_key,
        })
        vision_chat = ChatClient(v_env)
        own_vision = True
    try:
        return await _run_generation(description, category, style, sizes, feedback,
                                     subject_ref, save_subject_as, save_style_as,
                                     chat, image, vision_chat, edit, env, cfg, out_root)
    finally:
        # 只关闭自己创建的客户端;注入的(测试/复用方)归调用方管理
        if own_chat:
            await chat.aclose()
        if own_image:
            await image.aclose()
        if own_edit:
            await edit.aclose()
        if own_vision:
            await vision_chat.aclose()


async def _run_generation(description, category, style, sizes, feedback,
                          subject_ref, save_subject_as, save_style_as,
                          chat, image, vision_chat, edit, env, cfg, out_root) -> dict:
    t0 = time.monotonic()
    notes: list[str] = []
    category = category.strip().lower() if category else "item"
    if category not in CATEGORIES:
        notes.append(f"unknown category {category!r}, fallback to 'item'")
        category = "item"
    cutout_enabled = category not in NO_CUTOUT_CATEGORIES
    sizes = normalize_sizes(sizes, cfg)
    out_root = out_root or (PROJECT_ROOT / cfg.output.dir)

    # 一致性解析:全局风格锚 + style 参数(可为风格库名)+ 主体卡
    style_anchor, style_src = resolve_style(style, cfg)
    if cfg.style.anchor:
        style_anchor = " ".join(s for s in (cfg.style.anchor, style_anchor) if s)
    if style_src.startswith("style-lib:"):
        notes.append(f"style resolved from library: {style_src.split(':', 1)[1]}")
    subject_card, subject_entry = "", None
    if subject_ref:
        subject_entry = load_subject(subject_ref, cfg)
        if subject_entry:
            subject_card = subject_entry.get("card", "")
            notes.append(f"subject card loaded: {subject_entry.get('name')}")
            # 卡自带风格且调用方未指定 → 沿用卡的风格,保证同款观感
            if not style and subject_entry.get("style"):
                style_anchor = " ".join(s for s in (style_anchor, subject_entry["style"]) if s)
        else:
            notes.append(f"subject card not found: {subject_ref!r} (ignored)")

    # 1. 请求分解
    spec: AssetSpec = await decompose(chat, description, category, style, feedback, cfg,
                                      subject_card=subject_card, style_anchor=style_anchor)
    if spec.fallback:
        notes.append("llm decompose failed, used fallback template prompt")
    slug = slugify(spec.subject_en) if not spec.fallback else slugify(description)
    task_dir = _new_task_dir(out_root, slug)

    # 2. 生图 + 启发式质检
    gen = await generate_with_qa(image, spec.final_prompt, task_dir / "draft.png",
                                 cfg.image.size, cfg.image.retries)
    image_calls = gen.attempts

    # 3. 视觉审核(可降级) → 不过则带反馈重生成,最多 max_rounds 轮
    reviewer = Reviewer(vision_chat, env.vision_model) if vision_chat else None
    review_summary: dict = {"status": "skipped",
                            "reason": "vision model not configured or unavailable"}
    if reviewer is not None and await reviewer.available():
        rounds = []
        for i in range(cfg.review.max_rounds + 1):
            verdict = await reviewer.review(gen.path, spec.subject_en, cfg.review.min_score,
                                            category=category)
            rounds.append(verdict.to_dict())
            if verdict.passed or verdict.status == "error":
                break
            if i < cfg.review.max_rounds:
                # 带审核反馈重生成:优先图生图编辑当前草稿(保形改图,保留已对的部分);
                # 编辑通道不可用则回退"重新分解 + 全新生成"
                edit_used = False
                if edit is not None:
                    try:
                        gen = await edit_with_qa(
                            edit, f"{spec.final_prompt}. Fix: {verdict.issues}",
                            gen.path, task_dir / "draft.png", cfg.image.retries)
                        edit_used = True
                    except Exception as e:
                        notes.append(f"edit channel failed ({str(e)[:80]}), "
                                     "fallback to full regeneration")
                if not edit_used:
                    spec = await decompose(chat, description, category, style, verdict.issues, cfg,
                                           subject_card=subject_card, style_anchor=style_anchor)
                    gen = await generate_with_qa(image, spec.final_prompt, task_dir / "draft.png",
                                                 cfg.image.size, cfg.image.retries)
                image_calls += gen.attempts
        last = rounds[-1] if rounds else {}
        review_summary = {
            "status": "ok" if last.get("passed") else "failed",
            "model": reviewer.model,
            "rounds": rounds,
        }
        if review_summary["status"] == "failed":
            # 审核不过仍交付(草稿路径已含),由调用方 AI 终判
            notes.append("vision review did not pass; deliver anyway, check draft path")
    elif env.vision_model:
        reason = f" ({reviewer.probe_error})" if reviewer else ""
        notes.append(f"vision model {env.vision_model!r} probe failed{reason}, review skipped")

    # 4+5. 抠图(满幅类别跳过)→ 裁边 → 多尺寸输出(线程池,不阻塞事件循环)
    post = await asyncio.to_thread(_postprocess, gen.path, task_dir, sizes, slug, cfg,
                                   cutout_enabled)
    if post["cutout"]["method"].endswith("guard_kept_original"):
        notes.append("cutout guard: subject looks near-white, kept original background")

    result = {
        "task_id": task_dir.name,
        "status": "ok",
        "category": category,
        "description": description,
        "prompt": spec.final_prompt,
        "image_calls": image_calls,
        "review": review_summary,
        "draft_path": str(gen.path.resolve()),
        **post,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "task_dir": str(task_dir.resolve()),
        "notes": notes,
    }
    _write_task_json(task_dir, result)
    # 冻结主体卡(当次生效的主体描述 → 下次 subject_ref 引用即得同款外观)
    if save_subject_as:
        card = save_subject(save_subject_as, spec.subject_en,
                            style=spec.style_tokens or style, task_id=task_dir.name, cfg=cfg)
        notes.append(f"subject card saved: {card['name']}")
    # 冻结命名风格(当次生效的风格描述 → 下次 style 按名引用)
    if save_style_as:
        saved = save_style(save_style_as, spec.style_tokens or style_anchor, cfg=cfg)
        notes.append(f"style saved: {saved['name']}")
    if save_subject_as or save_style_as:
        _write_task_json(task_dir, result)  # notes 已补,重写元数据
    return result


async def cutout_image_pipeline(
    image_path: str,
    sizes: list[int] | None = None,
    padding_ratio: float | None = None,
    cfg: AppConfig | None = None,
    out_root: Path | None = None,
) -> dict:
    """独立后处理:对任意已有图片执行抠图 + 多尺寸输出。"""
    cfg = cfg or AppConfig()
    src = Path(image_path)
    if not src.is_file():
        return {"status": "error", "message": f"image not found: {image_path}"}
    sizes = normalize_sizes(sizes, cfg)
    if padding_ratio is not None:
        if not 0 <= padding_ratio <= 0.4:
            return {"status": "error", "message": f"padding_ratio out of range: {padding_ratio}"}
        cfg = cfg.model_copy(update={"cutout": cfg.cutout.model_copy(update={"padding_ratio": padding_ratio})})
    out_root = out_root or (PROJECT_ROOT / cfg.output.dir)
    task_dir = _new_task_dir(out_root, f"cutout_{slugify(src.stem)}")

    def work() -> dict:
        img = Image.open(src)
        img.load()
        cut, method = cutout_mod.cutout(img, cfg.cutout)
        trimmed = scaler.trim_to_alpha(cut)
        cutout_path = task_dir / "cutout.png"
        cut.save(cutout_path, "PNG")
        master = scaler.save_master(trimmed, task_dir / "master.png")
        finals = scaler.save_finals(trimmed, sizes, task_dir / "finals",
                                    slugify(src.stem), cfg.cutout.padding_ratio)
        return {"cutout": {"path": str(cutout_path.resolve()), "method": method},
                "master": master, "finals": finals}

    post = await asyncio.to_thread(work)
    result = {
        "task_id": task_dir.name,
        "status": "ok",
        "source": str(src.resolve()),
        **post,
        "task_dir": str(task_dir.resolve()),
    }
    _write_task_json(task_dir, result)
    return result


def list_assets(out_root: Path | None = None, cfg: AppConfig | None = None,
                prefix: str = "") -> dict:
    """汇总 out/ 下各任务的 task.json。prefix 匹配 task_id 前缀。"""
    cfg = cfg or AppConfig()
    root = out_root or (PROJECT_ROOT / cfg.output.dir)
    if not root.is_dir():
        return {"status": "ok", "count": 0, "assets": []}
    assets = []
    for task_json in sorted(root.glob("*/task.json")):
        if prefix and not task_json.parent.name.startswith(prefix):
            continue
        try:
            data = json.loads(task_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        assets.append({
            "task_id": data.get("task_id", task_json.parent.name),
            "status": data.get("status"),
            "category": data.get("category") or data.get("type"),
            "description": (data.get("description") or data.get("source")
                            or (f"anim:{data.get('action')} {data.get('subject')}"
                                if data.get("type") == "animation" else None)),
            "review": data.get("review", {}).get("status") if isinstance(data.get("review"), dict) else None,
            "finals": [f.get("path") for f in data.get("finals", [])],
            "task_dir": str(task_json.parent.resolve()),
        })
    return {"status": "ok", "count": len(assets), "assets": assets}
