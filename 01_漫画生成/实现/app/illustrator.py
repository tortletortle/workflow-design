"""阶段 3 · 生图:每格提示词 → panel_NN.png(统一尺寸,JPEG/PNG 均落盘为 PNG)。

live 探测结论:服务商返回尺寸可能与请求不符(请求 1024 实给 960),
所以每格生成后统一 resize 到目标尺寸,保证拼接整齐。
生成后过启发式质检(损坏/尺寸/纯色空白),不过则计入失败交给重试循环。
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image

from app.critic import review_image_heuristic
from app.client import ImageClient
from app.splitter import Panel


async def generate_panel(
    image: ImageClient, panel: Panel, out_dir: Path, size: tuple[int, int],
    retries: int = 2, qa: bool = True,
) -> Panel:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            data = await image.generate(panel.image_prompt, f"{size[0]}x{size[1]}")
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if img.size != size:  # 服务商尺寸不守约时归一化
                img = img.resize(size, Image.LANCZOS)
            out_file = f"panel_{panel.index:02d}.png"
            img.save(out_dir / out_file, "PNG")
            if qa:
                verdict = review_image_heuristic(out_dir / out_file, size)
                if not verdict.passed:
                    (out_dir / out_file).unlink(missing_ok=True)
                    raise RuntimeError(f"image QA failed: {verdict.issues}")
            panel.image_file = out_file
            return panel
        except Exception as e:  # 单格失败重试(含质检不过),耗尽后抛出由 pipeline 记为失败格
            last_error = e
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"panel {panel.index} failed after {retries + 1} attempts: {last_error}")


async def generate_all(
    image: ImageClient,
    panels: list[Panel],
    out_dir: Path,
    size: tuple[int, int],
    concurrency: int = 2,
    retries: int = 2,
    qa: bool = True,
) -> tuple[list[Panel], list[Panel]]:
    """并发生成全部格;返回 (成功, 失败),失败不炸整条流水线。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[Panel, Exception | None]] = []

    async def one(panel: Panel) -> None:
        async with sem:
            try:
                results.append((await generate_panel(image, panel, out_dir, size, retries, qa), None))
            except Exception as e:
                results.append((panel, e))

    await asyncio.gather(*(one(p) for p in panels))
    ok = [p for p, e in results if e is None]
    failed = [p for p, e in results if e is not None]
    return ok, failed
