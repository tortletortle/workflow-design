"""生图:调用 ImageClient → 尺寸归一 → 启发式质检 → 重试。

质检不过(空白/尺寸坏/体积可疑)重试,超过 retries 次抛错。
最终产物恒为指定尺寸的 RGB PNG(白底)。
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

from app.client import EditClient, ImageClient
from app.settings import parse_size


@dataclass
class GenResult:
    path: Path
    attempts: int  # 实际生图次数(含失败重试)


def _normalize(raw: bytes, expected: tuple[int, int]) -> Image.Image:
    """解码→RGB(带 alpha 的压到白底)→尺寸归一(服务商可能不守约)。"""
    img = Image.open(io.BytesIO(raw))
    img.load()
    if img.mode != "RGB":
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    if img.size != expected:
        img = img.resize(expected, Image.LANCZOS)
    return img


def heuristic_qa(img: Image.Image, raw_len: int) -> str | None:
    """返回 None = 通过;返回字符串 = 不通过原因。"""
    stat = ImageStat.Stat(img.convert("RGB"))
    if max(stat.stddev) < 6:  # 像素方差极低 → 纯色/空白废图
        return "image looks blank (pixel variance too low)"
    if min(img.size) >= 512 and raw_len < 15_000:
        # 体积检查只对生产尺寸生效(小图天然就小)
        return f"suspiciously small image data ({raw_len}B)"
    return None


async def generate_with_qa(
    image: ImageClient,
    prompt: str,
    out_path: Path,
    size: str,
    retries: int = 2,
) -> GenResult:
    expected = parse_size(size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = 0
    last_reason = ""
    for i in range(retries + 1):
        attempts += 1
        raw = await image.generate(prompt, size)
        try:
            img = _normalize(raw, expected)
        except Exception as e:
            last_reason = f"decode failed: {e}"
            continue
        reason = heuristic_qa(img, len(raw))
        if reason is None:
            img.save(out_path, "PNG")
            return GenResult(path=out_path, attempts=attempts)
        last_reason = reason
        await asyncio.sleep(1.0 + 0.5 * i)  # 递增间隔,给上游恢复时间
    raise RuntimeError(f"image generation failed after {attempts} attempts: {last_reason}")


async def edit_with_qa(
    edit: "EditClient",
    prompt: str,
    ref_path: Path,
    out_path: Path,
    retries: int = 2,
) -> GenResult:
    """图生图版本:编辑参考图 → 质检 → 重试。输出尺寸跟随参考图。

    编辑模型的输出天然跟随输入画幅,不做强制缩放(尺寸不一致时才归一),
    保证动画帧与锚帧同画幅,减少归一化阶段的形变。
    """
    ref_bytes = ref_path.read_bytes()
    expected = Image.open(io.BytesIO(ref_bytes)).size
    out_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = 0
    last_reason = ""
    for i in range(retries + 1):
        attempts += 1
        raw = await edit.edit(prompt, ref_bytes)
        try:
            img = _normalize(raw, expected)
        except Exception as e:
            last_reason = f"decode failed: {e}"
            continue
        reason = heuristic_qa(img, len(raw))
        if reason is None:
            img.save(out_path, "PNG")
            return GenResult(path=out_path, attempts=attempts)
        last_reason = reason
        await asyncio.sleep(1.0 + 0.5 * i)
    raise RuntimeError(f"image edit failed after {attempts} attempts: {last_reason}")
