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
from app.client import ChatClient, ImageClient
from app.critic import review_image_vision, review_prompts
from app.designer import build_character_sheet, design_all, design_panel
from app.illustrator import generate_all, generate_panel
from app.settings import AppConfig, EnvSettings, parse_size
from app.splitter import Panel, polish_captions, split_story


@dataclass
class PipelineReport:
    status: str                      # ok | partial | error
    panels: list[dict] = field(default_factory=list)
    failed_indexes: list[int] = field(default_factory=list)
    regen_indexes: list[int] = field(default_factory=list)   # 经历过质检重生成/重设计的格
    comic_path: str = ""
    duration_s: float = 0.0
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)


class ComicPipeline:
    def __init__(self, env: EnvSettings, config: AppConfig, out_dir: Path):
        self.env = env
        self.config = config
        self.out_dir = out_dir
        self.panels_dir = out_dir / "panels"
        self.state_path = out_dir / "state.json"

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
        sheet: str, size: tuple[int, int],
    ) -> list[int]:
        """批量提示词质检 → 合格格立即生图,不合格格同时带反馈重设计(互不等待),
        重设计完成后直接进入同一个生图队列。返回失败格 index 列表。"""
        verdicts = await review_prompts(chat, panels)
        passed = [p for p in panels if verdicts[p.index].passed]
        rejected = [p for p in panels if not verdicts[p.index].passed]
        self.panels_dir.mkdir(parents=True, exist_ok=True)

        gen_sem = asyncio.Semaphore(self.config.concurrency.image)
        des_sem = asyncio.Semaphore(self.config.concurrency.design)
        results: list[tuple[Panel, Exception | None]] = []

        async def gen_one(panel: Panel) -> None:
            async with gen_sem:
                try:
                    await generate_panel(
                        image, panel, self.panels_dir, size,
                        retries=self.config.concurrency.retries,
                        qa=self.config.critic.image_qa,
                    )
                    results.append((panel, None))
                except Exception as e:
                    results.append((panel, e))

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
        size: tuple[int, int],
    ) -> None:
        """生成后视觉评分(需 critic.vision_model);不过 → 重设计+重生成一次(结果保留)。"""
        model = self.config.critic.vision_model
        sem = asyncio.Semaphore(self.config.concurrency.image)

        async def one(panel: Panel) -> None:
            async with sem:
                path = self.panels_dir / panel.image_file
                verdict = await review_image_vision(chat, model, path, panel.image_prompt)
                if verdict.passed:
                    return
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
                    await generate_panel(image, panel, self.panels_dir, size, retries=1)
                    self._regen.add(panel.index)
                except Exception:
                    pass  # 重生成失败:占位框兜底,不炸流程

        await asyncio.gather(*(one(p) for p in panels if p.image_file))

    # ---- 主链路 ----

    async def run(self, story: str, panel_count: int | None = None) -> PipelineReport:
        started = time.monotonic()
        count = panel_count or self.config.panels.count
        size = parse_size(self.config.image.size)
        chat = ChatClient(self.env)
        image = ImageClient(self.env)
        report = PipelineReport(status="error")
        self._regen: set[int] = set()

        try:
            # ---- 阶段 1:拆分 ----
            panels = await split_story(chat, story, count, self.config.panels.language)
            self._restore(panels)

            # ---- 阶段 1.5:台词优化(只补缺 caption 的格)----
            if self.config.polish.enabled and any(not p.caption for p in panels):
                await polish_captions(
                    chat, panels,
                    language=self.config.panels.language,
                    max_chars=self.config.polish.max_chars,
                )
            self._save_state(panels)

            # ---- 阶段 2:设计 ----
            todo_design = [p for p in panels if not p.image_prompt]
            sheet = ""
            if todo_design or self.config.critic.prompt_qa:
                if self.config.characters.lock:
                    sheet = await build_character_sheet(chat, panels)
            if todo_design:
                await design_all(
                    chat, todo_design,
                    style_anchor=self.config.image.style_anchor,
                    lock_characters=False,  # sheet 已在外部提炼
                    concurrency=self.config.concurrency.design,
                    extra_style=self.config.image.extra,
                    character_sheet=sheet,
                )
            self._save_state(panels)

            # ---- 阶段 3:批量质检 + 生成/重设计流水线 ----
            todo_image = [p for p in panels if not p.image_file]
            qa_scope = [p for p in todo_image if p.image_prompt]
            if self.config.critic.prompt_qa and qa_scope:
                report.failed_indexes = await self._qa_design_generate(
                    chat, image, qa_scope, sheet, size
                )
            elif todo_image:
                ok, failed = await generate_all(
                    image, todo_image, self.panels_dir, size,
                    concurrency=self.config.concurrency.image,
                    retries=self.config.concurrency.retries,
                    qa=self.config.critic.image_qa,
                )
                report.failed_indexes = [p.index for p in failed]
            self._save_state(panels)

            # ---- 阶段 3.5:视觉质检(可选,需 vision_model)----
            if self.config.critic.vision_model:
                await self._vision_qa_pass(chat, image, panels, sheet, size)
                self._save_state(panels)

            # ---- 阶段 4:拼接(失败格渲染占位框)----
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

            report.status = "partial" if report.failed_indexes else "ok"
            report.panels = [p.model_dump() for p in panels]
            report.regen_indexes = sorted(self._regen)
            report.comic_path = str(comic_path)
        except Exception as e:
            report.error = f"{type(e).__name__}: {e}"
        finally:
            await chat.aclose()
            await image.aclose()
            report.duration_s = round(time.monotonic() - started, 1)
        return report
