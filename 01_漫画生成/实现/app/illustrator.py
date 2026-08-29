"""阶段 3 · 生图:每格提示词 → panel_NN.png(统一尺寸,JPEG/PNG 均落盘为 PNG)。

live 探测结论:服务商返回尺寸可能与请求不符(请求 1024 实给 960),
所以每格生成后统一 resize 到目标尺寸,保证拼接整齐。
生成后过启发式质检(损坏/尺寸/纯色空白),不过则计入失败交给重试循环。

模型兜底链:主模型 429 配额耗尽时不重试、立即切换 fallback_models 依次尝试;
全链 429 则抛 QuotaExhaustedError,generate_all 据此熔断剩余格(快速退出,
不再逐格撞墙)。非 429 错误仍走原重试循环(瞬时网络抖动值得等)。
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image

from app.critic import review_image_heuristic
from app.client import EditClient, ImageClient, QuotaExhaustedError
from app.splitter import Panel
from app.errors import ImageQualityError, NetworkError
from app.logger import get_logger

logger = get_logger("comic.illustrator")


async def refine_panel(
    edit: EditClient, panel: Panel, image_file: Path, size: tuple[int, int],
    issues: str,
) -> Panel:
    """保形改图:视觉质检不过的格,step-image-edit-2 以成品图为参考修问题。

    与推倒重来的"重设计+文生图"互补:构图/角色/风格延续已通过质检的部分,
    只按 reviewer 的 issues 定向修正(如手部崩坏、表情错误、多余元素)。
    输出统一 resize 回目标尺寸并重过启发式质检;失败由调用方退回原路径。
    """
    instruction = (
        f"Refine this manga panel. Keep the composition, characters and art style "
        f"EXACTLY as they are, fix ONLY these problems: {issues}. "
        f"Original prompt for reference: {panel.image_prompt[:600]}"
    )
    ref_png = image_file.read_bytes()
    data = await edit.edit(instruction, ref_png)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    img.save(image_file, "PNG")  # 原位覆盖
    verdict = review_image_heuristic(image_file, size)
    if not verdict.passed:
        raise ImageQualityError(panel.index, verdict.issues)
    return panel


async def generate_panel(
    image: ImageClient, panel: Panel, out_dir: Path, size: tuple[int, int],
    retries: int = 2, qa: bool = True, fallback_models: list[str] | None = None,
    reference_image: bytes | None = None, img2img_strength: float = 0.6,
) -> Panel:
    # 兜底链:主模型(不覆盖)→ 依次 fallback;同名去重
    chain: list[str | None] = [None] + [
        m for m in (fallback_models or []) if m and m != image.model
    ]
    last_error: Exception | None = None
    for model in chain:
        try:
            return await _generate_with_model(
                image, panel, out_dir, size, model, retries, qa,
                reference_image, img2img_strength
            )
        except QuotaExhaustedError as e:  # 429:同模型重试无意义,立即换下一个
            last_error = e
    raise last_error  # 链上全部配额耗尽:交给上层熔断,消息已含"哪个模型都不行"


async def _generate_with_model(
    image: ImageClient, panel: Panel, out_dir: Path, size: tuple[int, int],
    model: str | None, retries: int, qa: bool,
    reference_image: bytes | None = None, img2img_strength: float = 0.6,
) -> Panel:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            # 如果有参考图，用图生图；否则用文生图
            if reference_image:
                data = await image.generate_with_reference(
                    panel.image_prompt, f"{size[0]}x{size[1]}",
                    reference_image, strength=img2img_strength
                )
            else:
                data = await image.generate(panel.image_prompt, f"{size[0]}x{size[1]}", model=model)
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if img.size != size:  # 服务商尺寸不守约时归一化
                img = img.resize(size, Image.LANCZOS)
            out_file = f"panel_{panel.index:02d}.png"
            img.save(out_dir / out_file, "PNG")
            if qa:
                verdict = review_image_heuristic(out_dir / out_file, size)
                if not verdict.passed:
                    (out_dir / out_file).unlink(missing_ok=True)
                    raise ImageQualityError(panel.index, verdict.issues)
            panel.image_file = out_file
            return panel
        except QuotaExhaustedError:
            raise  # 配额类错误不 sleep 不重试,交回 generate_panel 换模型
        except (NetworkError, ImageQualityError) as e:
            last_error = e
            await asyncio.sleep(1.5 * (attempt + 1))
        except Exception as e:  # 其他未预期错误也重试
            last_error = e
            await asyncio.sleep(1.5 * (attempt + 1))
    
    # 区分错误类型给出更好的提示
    if isinstance(last_error, ImageQualityError):
        raise ImageQualityError(
            panel.index, 
            f"重试 {retries + 1} 次后仍未通过质检: {last_error}"
        )
    raise NetworkError(
        f"第 {panel.index} 格生成失败 (重试 {retries + 1} 次): {last_error}",
        original=last_error
    )


async def generate_all(
    image: ImageClient,
    panels: list[Panel],
    out_dir: Path,
    size: tuple[int, int],
    concurrency: int = 2,
    retries: int = 2,
    qa: bool = True,
    fallback_models: list[str] | None = None,
    quota_dead: asyncio.Event | None = None,
) -> tuple[list[Panel], list[Panel]]:
    """并发生成全部格;返回 (成功, 失败),失败不炸整条流水线。

    任一格走完整条兜底链仍 429 → 熔断:同批剩余格直接标记失败不再请求
    (额度是账号级的,继续发只会逐格等待同样的 429)。
    quota_dead 可由调用方传入共享,供流水线其他阶段感知熔断状态。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    dead = quota_dead if quota_dead is not None else asyncio.Event()
    results: list[tuple[Panel, Exception | None]] = []

    async def one(panel: Panel) -> None:
        async with sem:
            if dead.is_set():
                results.append((panel, QuotaExhaustedError("skipped: image quota exhausted")))
                return
            try:
                results.append(
                    (await generate_panel(image, panel, out_dir, size, retries, qa, fallback_models), None)
                )
            except QuotaExhaustedError as e:
                dead.set()
                results.append((panel, e))
            except Exception as e:
                results.append((panel, e))

    await asyncio.gather(*(one(p) for p in panels))
    ok = [p for p, e in results if e is None]
    failed = [p for p, e in results if e is not None]
    return ok, failed


async def generate_all_with_consistency(
    image: ImageClient,
    panels: list[Panel],
    out_dir: Path,
    size: tuple[int, int],
    concurrency: int = 2,
    retries: int = 2,
    qa: bool = True,
    fallback_models: list[str] | None = None,
    quota_dead: asyncio.Event | None = None,
    img2img_strength: float = 0.6,
) -> tuple[list[Panel], list[Panel]]:
    """保证角色一致性的生成:第一格文生图,后续格子用第一格作参考图生图。
    
    流程:1.先生成第一格(文生图)
        2.读取第一格图片文件
        3.后续格子并发用第一格作参考图生图(img2img)
        返回(成功,失败)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    
    if not panels:
        return [], []
    
    first_panel = panels[0]
    try:
        ok_first, failed_first = await generate_all(
            image, [first_panel], out_dir, size,
            concurrency=1, retries=retries, qa=qa,
            fallback_models=fallback_models, quota_dead=quota_dead
        )
        if not ok_first:
            return [], list(panels)
    except Exception as e:
        return [], list(panels)
    
    ref_image_path = out_dir / f"panel_{first_panel.index:02d}.png"
    if not ref_image_path.exists():
        return [], list(panels)
    
    ref_image_data = ref_image_path.read_bytes()
    
    rest_panels = panels[1:]
    if not rest_panels:
        return ok_first, failed_first
    
    sem = asyncio.Semaphore(concurrency)
    dead = quota_dead if quota_dead is not None else asyncio.Event()
    results: list[tuple[Panel, Exception | None]] = []
    
    async def one(panel: Panel) -> None:
        async with sem:
            if dead.is_set():
                results.append((panel, QuotaExhaustedError("skipped: image quota exhausted")))
                return
            try:
                panel_result = await generate_panel(
                    image, panel, out_dir, size,
                    retries=retries, qa=qa,
                    fallback_models=fallback_models,
                    reference_image=ref_image_data,
                    img2img_strength=img2img_strength
                )
                results.append((panel_result, None))
            except QuotaExhaustedError as e:
                dead.set()
                results.append((panel, e))
            except Exception as e:
                results.append((panel, e))
    
    await asyncio.gather(*(one(p) for p in rest_panels))
    
    ok_rest = [p for p, e in results if e is None]
    failed_rest = [p for p, e in results if e is not None]
    
    return ok_first + ok_rest, failed_first + failed_rest
