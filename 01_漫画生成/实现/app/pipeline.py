"""流水线编排:拆分 → 台词优化 → 设计(→提示词质检·重设计)→ 生图(→启发式质检·重试)
→ [视觉质检·重生成,需 vision_model] → 拼接。

断点续跑:状态落盘 state.json(每格的 caption / image_prompt / image_file);
重跑时已完成生图的格直接跳过(生图是最贵步骤),缺的格补齐后重新拼接。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.assembler import assemble_comic
from app.client import ChatClient, EditClient, ImageClient, QuotaExhaustedError
from app.critic import review_image_vision, review_prompts
from app.designer import build_character_sheet, design_all, design_panel
from app.illustrator import generate_all, generate_all_with_consistency, generate_panel, refine_panel
from app.settings import AppConfig, EnvSettings, parse_size
from app.splitter import Panel, polish_captions, split_story
from app.progress import ProgressReporter
from app.logger import get_logger

logger = get_logger("comic.pipeline")


@dataclass
class PipelineReport:
    status: str                      # ok | partial | error
    panels: list[dict] = field(default_factory=list)
    failed_indexes: list[int] = field(default_factory=list)
    regen_indexes: list[int] = field(default_factory=list)   # 经历过质检重生成/重设计的格
    comic_path: str = ""
    duration_s: float = 0.0
    quota_exhausted: bool = False    # 生图全链 429:失败格是熔断跳过而非质量问题
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)


class ComicPipeline:
    def __init__(self, env: EnvSettings, config: AppConfig, out_dir: Path, 
                 reporter: ProgressReporter | None = None):
        self.env = env
        self.config = config
        self.out_dir = out_dir
        self.panels_dir = out_dir / "panels"
        self.state_path = out_dir / "state.json"
        self.reporter = reporter or ProgressReporter(silent=True)

    # ---- 状态(断点续跑)----

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_state(self, panels: list[Panel]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"panels": [p.model_dump() for p in panels]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _restore(self, panels: list[Panel]) -> None:
        state = self._load_state()
        prev = {p["index"]: p for p in state.get("panels", [])}
        for p in panels:
            old = prev.get(p.index)
            if not old:
                continue
            if not p.caption and old.get("caption"):
                p.caption = old["caption"]
            if not p.image_prompt and old.get("image_prompt"):
                p.image_prompt = old["image_prompt"]
            if old.get("image_file") and (self.panels_dir / old["image_file"]).exists():
                p.image_file = old["image_file"]

    # ---- 质检 + 生成(流水线化)----

    async def _qa_design_generate(
        self, chat: ChatClient, image: ImageClient, panels: list[Panel],
        sheet: str, size: tuple[int, int], update,
    ) -> list[int]:
        """批量提示词质检 → 合格格立即生图,不合格格同时带反馈重设计(互不等待),
        重设计完成后直接进入同一个生图队列。返回失败格 index 列表。

        生图全链 429 时触发熔断(self._quota_dead):剩余格不再请求,快速进入拼接。
        """
        verdicts = await review_prompts(chat, panels)
        passed = [p for p in panels if verdicts[p.index].passed]
        rejected = [p for p in panels if not verdicts[p.index].passed]
        self.panels_dir.mkdir(parents=True, exist_ok=True)

        gen_sem = asyncio.Semaphore(self.config.concurrency.image)
        des_sem = asyncio.Semaphore(self.config.concurrency.design)
        results: list[tuple[Panel, Exception | None]] = []

        async def gen_one(panel: Panel) -> None:
            async with gen_sem:
                if self._quota_dead.is_set():
                    results.append((panel, QuotaExhaustedError("skipped: image quota exhausted")))
                    await update(1)
                    return
                try:
                    await generate_panel(
                        image, panel, self.panels_dir, size,
                        retries=self.config.concurrency.retries,
                        qa=self.config.critic.image_qa,
                        fallback_models=self.config.image.fallback_models,
                    )
                    results.append((panel, None))
                except QuotaExhaustedError as e:
                    self._quota_dead.set()
                    results.append((panel, e))
                except Exception as e:
                    results.append((panel, e))
                finally:
                    await update(1)

        async def redesign_and_gen(panel: Panel) -> None:
            async with des_sem:  # 重设计期间不占生图并发
                panel.image_prompt = await design_panel(
                    chat, panel,
                    style_anchor=self.config.image.style_anchor,
                    character_sheet=sheet,
                    extra_style=self.config.image.extra,
                    feedback=verdicts[panel.index].issues,
                )
                self._regen.add(panel.index)
            await gen_one(panel)  # 设计完立即排进生图队列

        await asyncio.gather(
            *( [gen_one(p) for p in passed] + [redesign_and_gen(p) for p in rejected] )
        )
        return [p.index for p, e in results if e is not None]

    async def _vision_qa_pass(
        self, chat: ChatClient, image: ImageClient, panels: list[Panel], sheet: str,
        size: tuple[int, int], edit: EditClient | None = None,
    ) -> None:
        """生成后视觉评分(critic.vision_model);不过 → 保形改图或重设计+重生成(结果保留)。

        保形改图路径(env.edit_api_key 非空启用):step-image-edit-2 以该格成品为参考,
        只修 reviewer 指出的问题,构图/角色/风格不推倒重来;改图 429/失败退回原路径。
        """
        model = self.config.critic.vision_model
        sem = asyncio.Semaphore(self.config.concurrency.image)

        async def one(panel: Panel) -> None:
            async with sem:
                if self._quota_dead.is_set():  # 额度已熔断:重生成必撞 429,跳过
                    return
                path = self.panels_dir / panel.image_file
                verdict = await review_image_vision(chat, model, path, panel.image_prompt)
                if verdict.passed:
                    return
                # ---- 路径 A:保形改图(编辑通道已配置时优先)----
                if edit is not None and edit.enabled and not self._quota_dead.is_set():
                    try:
                        await refine_panel(edit, panel, path, size, verdict.issues)
                        self._regen.add(panel.index)
                        logger.info("panel_refined", extra={"panel": panel.index, "issues": str(verdict.issues)[:200]})
                        return
                    except QuotaExhaustedError as e:
                        logger.warning("edit_quota_exhausted_fallback_t2i", extra={"error": str(e)[:200]})
                    except Exception as e:
                        logger.warning("edit_failed_fallback_t2i", extra={
                            "panel": panel.index, "error": f"{type(e).__name__}: {str(e)[:200]}"
                        })
                # ---- 路径 B:重设计 + 文生图(原路径/兜底)----
                panel.image_prompt = await design_panel(
                    chat, panel,
                    style_anchor=self.config.image.style_anchor,
                    character_sheet=sheet,
                    extra_style=self.config.image.extra,
                    feedback=verdict.issues,
                )
                path.unlink(missing_ok=True)
                panel.image_file = ""
                try:
                    await generate_panel(
                        image, panel, self.panels_dir, size, retries=1,
                        fallback_models=self.config.image.fallback_models,
                    )
                    self._regen.add(panel.index)
                except Exception:
                    pass  # 重生成失败:占位框兜底,不炸流程

        await asyncio.gather(*(one(p) for p in panels if p.image_file))

    # ---- 进度反馈辅助方法 ----

    async def _design_all_with_progress(
        self, chat: ChatClient, panels: list[Panel], sheet: str, update
    ) -> None:
        """并发设计，每完成一个更新进度。"""
        sem = asyncio.Semaphore(self.config.concurrency.design)
        
        async def design_one(panel: Panel) -> None:
            async with sem:
                panel.image_prompt = await design_panel(
                    chat, panel,
                    style_anchor=self.config.image.style_anchor,
                    character_sheet=sheet,
                    extra_style=self.config.image.extra,
                )
                await update(1)
        
        await asyncio.gather(*(design_one(p) for p in panels))

    async def _generate_all_with_progress(
        self, image: ImageClient, panels: list[Panel], size: tuple[int, int], update
    ) -> tuple[list[Panel], list[Panel]]:
        """并发生成，每完成一个更新进度。支持consistency模式(图生图保证角色一致性)。"""
        self.panels_dir.mkdir(parents=True, exist_ok=True)
        
        use_consistency = self.config.image.consistency.get('enabled', False) if self.config.image.consistency else False
        img2img_strength = self.config.image.consistency.get('strength', 0.65) if self.config.image.consistency else 0.65
        
        if not use_consistency or len(panels) <= 1:
            sem = asyncio.Semaphore(self.config.concurrency.image)
            results: list[tuple[Panel, Exception | None]] = []

            async def one(panel: Panel) -> None:
                async with sem:
                    if self._quota_dead.is_set():
                        results.append((panel, QuotaExhaustedError("skipped: image quota exhausted")))
                        await update(1)
                        return
                    try:
                        await generate_panel(
                            image, panel, self.panels_dir, size,
                            retries=self.config.concurrency.retries,
                            qa=self.config.critic.image_qa,
                            fallback_models=self.config.image.fallback_models,
                        )
                        results.append((panel, None))
                    except QuotaExhaustedError as e:
                        self._quota_dead.set()
                        results.append((panel, e))
                    except Exception as e:
                        results.append((panel, e))
                    finally:
                        await update(1)

            await asyncio.gather(*(one(p) for p in panels))
            ok = [p for p, e in results if e is None]
            failed = [p for p, e in results if e is not None]
            return ok, failed
        
        # Consistency模式
        ok_first, failed_first = [], []
        try:
            ok_first, failed_first = await generate_all(
                image, [panels[0]], self.panels_dir, size,
                concurrency=1, retries=self.config.concurrency.retries,
                qa=self.config.critic.image_qa,
                fallback_models=self.config.image.fallback_models,
                quota_dead=self._quota_dead
            )
            await update(1)
            if not ok_first:
                for p in panels[1:]:
                    await update(1)
                return ok_first, list(panels)
        except Exception:
            for p in panels:
                await update(1)
            return [], list(panels)
        
        ref_path = self.panels_dir / f"panel_{panels[0].index:02d}.png"
        if not ref_path.exists():
            for p in panels[1:]:
                await update(1)
            return ok_first, list(panels[1:])
        
        ref_data = ref_path.read_bytes()
        rest_panels = panels[1:]
        if not rest_panels:
            return ok_first, failed_first
        
        sem = asyncio.Semaphore(self.config.concurrency.image)
        results: list[tuple[Panel, Exception | None]] = []
        
        async def one_with_ref(panel: Panel) -> None:
            async with sem:
                if self._quota_dead.is_set():
                    results.append((panel, QuotaExhaustedError("skipped")))
                    await update(1)
                    return
                try:
                    await generate_panel(
                        image, panel, self.panels_dir, size,
                        retries=self.config.concurrency.retries,
                        qa=self.config.critic.image_qa,
                        fallback_models=self.config.image.fallback_models,
                        reference_image=ref_data,
                        img2img_strength=img2img_strength
                    )
                    results.append((panel, None))
                except QuotaExhaustedError as e:
                    self._quota_dead.set()
                    results.append((panel, e))
                except Exception as e:
                    results.append((panel, e))
                finally:
                    await update(1)
        
        await asyncio.gather(*(one_with_ref(p) for p in rest_panels))
        ok_rest = [p for p, e in results if e is None]
        failed_rest = [p for p, e in results if e is not None]
        
        return ok_first + ok_rest, failed_first + failed_rest

    # ---- 主链路 ----

    async def run(self, story: str, panel_count: int | None = None, timeout_s: float | None = None) -> PipelineReport:
        started = time.monotonic()
        count = panel_count or self.config.panels.count
        size = parse_size(self.config.image.size)
        timeout_s = timeout_s or 300.0  # 默认 5 分钟
        
        chat = ChatClient(self.env)
        image = ImageClient(self.env)
        edit = EditClient(self.env)  # step-image-edit-2 保形改图;key 留空自动不启用
        report = PipelineReport(status="error")
        self._regen: set[int] = set()
        self._quota_dead = asyncio.Event()  # 生图全链 429 → 熔断剩余生图请求

        logger.info("pipeline_start", extra={
            "story_length": len(story),
            "panel_count": count,
            "output_dir": str(self.out_dir),
            "timeout_s": timeout_s,
            "edit_enabled": edit.enabled,
        })
        self.reporter.start()

        try:
            async with asyncio.timeout(timeout_s):
                # ---- 阶段 1:拆分 ----
                async with self.reporter.stage("拆分故事"):
                    logger.info("stage_start", extra={"stage": "splitter", "count": count})
                    panels = await split_story(chat, story, count, self.config.panels.language)
                    self._restore(panels)
                    logger.info("stage_complete", extra={"stage": "splitter", "panels": len(panels)})

                # ---- 阶段 1.5:台词优化(只补缺 caption 的格)----
                if self.config.polish.enabled and any(not p.caption for p in panels):
                    async with self.reporter.stage("优化台词"):
                        logger.info("stage_start", extra={"stage": "polish"})
                        await polish_captions(
                            chat, panels,
                            language=self.config.panels.language,
                            max_chars=self.config.polish.max_chars,
                        )
                        logger.info("stage_complete", extra={"stage": "polish"})
                self._save_state(panels)

                # ---- 阶段 2:设计 ----
                todo_design = [p for p in panels if not p.image_prompt]
                sheet = ""
                if todo_design or self.config.critic.prompt_qa:
                    if self.config.characters.lock:
                        async with self.reporter.stage("构建角色卡"):
                            logger.info("stage_start", extra={"stage": "character_sheet"})
                            sheet = await build_character_sheet(chat, panels)
                            logger.info("stage_complete", extra={"stage": "character_sheet", "sheet_length": len(sheet)})
                
                if todo_design:
                    async with self.reporter.stage("设计提示词", total=len(todo_design)) as update:
                        logger.info("stage_start", extra={"stage": "designer", "count": len(todo_design)})
                        await self._design_all_with_progress(chat, todo_design, sheet, update)
                        logger.info("stage_complete", extra={"stage": "designer"})
                self._save_state(panels)

                # ---- 阶段 3:批量质检 + 生成/重设计流水线 ----
                todo_image = [p for p in panels if not p.image_file]
                qa_scope = [p for p in todo_image if p.image_prompt]
                
                logger.info("stage_start", extra={"stage": "illustrator", "count": len(todo_image)})
                if self.config.critic.prompt_qa and qa_scope:
                    async with self.reporter.stage("生成图片（含质检）", total=len(qa_scope)) as update:
                        report.failed_indexes = await self._qa_design_generate(
                            chat, image, qa_scope, sheet, size, update
                        )
                elif todo_image:
                    async with self.reporter.stage("生成图片", total=len(todo_image)) as update:
                        ok, failed = await self._generate_all_with_progress(
                            image, todo_image, size, update
                        )
                        report.failed_indexes = [p.index for p in failed]
                
                logger.info("stage_complete", extra={
                    "stage": "illustrator",
                    "success": len(todo_image) - len(report.failed_indexes),
                    "failed": len(report.failed_indexes),
                    "quota_exhausted": self._quota_dead.is_set(),
                })
                report.quota_exhausted = self._quota_dead.is_set()
                self._save_state(panels)

                # ---- 阶段 3.5:视觉质检(可选,需 vision_model)----
                if self.config.critic.vision_model:
                    async with self.reporter.stage("视觉质检", total=len([p for p in panels if p.image_file])):
                        await self._vision_qa_pass(chat, image, panels, sheet, size, edit=edit)
                        self._save_state(panels)

                # ---- 阶段 4:拼接(失败格渲染占位框)----
                async with self.reporter.stage("拼接漫画"):
                    logger.info("stage_start", extra={"stage": "assembler"})
                    ext = ".jpg" if self.config.assemble.format.upper() == "JPEG" else ".png"
                    comic_path = self.out_dir / (Path(self.config.output.comic_name).stem + ext)
                    assemble_comic(
                        panels, self.panels_dir, comic_path, size,
                        caption_h=self.config.assemble.caption_height,
                        gap=self.config.assemble.gap,
                        background=self.config.assemble.background,
                        fmt=self.config.assemble.format,
                        quality=self.config.assemble.quality,
                    )
                    logger.info("stage_complete", extra={"stage": "assembler", "output": str(comic_path)})

                    report.status = "partial" if report.failed_indexes else "ok"
                    report.panels = [p.model_dump() for p in panels]
                    report.regen_indexes = sorted(self._regen)
                    report.comic_path = str(comic_path)
                    
                    logger.info("pipeline_complete", extra={
                        "status": report.status,
                        "duration_s": round(time.monotonic() - started, 1),
                        "failed_panels": report.failed_indexes,
                        "regen_panels": report.regen_indexes,
                    })
                    self.reporter.finish(success=True, message=f"漫画已保存: {comic_path}")
        
        except asyncio.TimeoutError:
            report.error = f"生成超时 (>{timeout_s}秒)"
            logger.error("pipeline_timeout", extra={
                "timeout_s": timeout_s,
                "duration_s": round(time.monotonic() - started, 1),
            })
            self.reporter.finish(success=False, message=f"超时 (>{timeout_s}s)")
        except Exception as e:
            report.error = f"{type(e).__name__}: {e}"
            logger.error("pipeline_failed", extra={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "duration_s": round(time.monotonic() - started, 1),
            }, exc_info=True)
            self.reporter.finish(success=False, message=str(e))
        finally:
            await chat.aclose()
            await image.aclose()
            await edit.aclose()
            report.duration_s = round(time.monotonic() - started, 1)
        return report


async def generate_all_with_consistency_and_progress(
    image, panels, out_dir, size, concurrency, retries, qa,
    fallback_models, quota_dead, update_fn, img2img_strength
):
    """包装generate_all_with_consistency,添加进度更新."""
    ok_first, failed_first = [], []
    ok_rest, failed_rest = [], []
    
    # 第一步:生成第一格(文生图)
    try:
        ok_first, failed_first = await generate_all(
            image, [panels[0]], out_dir, size,
            concurrency=1, retries=retries, qa=qa,
            fallback_models=fallback_models, quota_dead=quota_dead
        )
        await update_fn(1)
        if not ok_first:
            for p in panels[1:]:
                await update_fn(1)
            return ok_first, list(panels)
    except Exception:
        for p in panels:
            await update_fn(1)
        return [], list(panels)
    
    # 第二步:读取参考图
    ref_path = out_dir / f"panel_{panels[0].index:02d}.png"
    if not ref_path.exists():
        for p in panels[1:]:
            await update_fn(1)
        return ok_first, list(panels[1:])
    
    ref_data = ref_path.read_bytes()
    
    # 第三步:后续格子用图生图
    rest_panels = panels[1:]
    if not rest_panels:
        return ok_first, failed_first
    
    sem = asyncio.Semaphore(concurrency)
    results = []
    
    async def one(panel):
        async with sem:
            if quota_dead.is_set():
                results.append((panel, QuotaExhaustedError("skipped")))
                await update_fn(1)
                return
            try:
                from app.illustrator import generate_panel
                p = await generate_panel(
                    image, panel, out_dir, size,
                    retries=retries, qa=qa,
                    fallback_models=fallback_models,
                    reference_image=ref_data,
                    img2img_strength=img2img_strength
                )
                results.append((p, None))
            except QuotaExhaustedError as e:
                quota_dead.set()
                results.append((panel, e))
            except Exception as e:
                results.append((panel, e))
            finally:
                await update_fn(1)
    
    import asyncio
    await asyncio.gather(*(one(p) for p in rest_panels))
    
    ok_rest = [p for p, e in results if e is None]
    failed_rest = [p for p, e in results if e is not None]
    
    return ok_first + ok_rest, failed_first + failed_rest
