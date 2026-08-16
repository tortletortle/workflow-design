"""缩放输出:裁透明边 → 补边距 → 等比缩放到各目标尺寸 → RGBA PNG。

每个尺寸产出一个正方形画布图(主体居中、四周留 padding_ratio 比例安全边),
另存裁边后的母版 master.png 供自定义再加工。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def trim_to_alpha(img: Image.Image) -> Image.Image:
    """按 alpha 包围盒裁掉全透明边。全透明则原样返回。"""
    rgba = img.convert("RGBA")
    bbox = rgba.split()[-1].getbbox()
    return rgba.crop(bbox) if bbox else rgba


def make_square(img: Image.Image, padding_ratio: float) -> Image.Image:
    """主体等比放入正方形画布,四周留 padding_ratio 比例边距。"""
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    if padding_ratio <= 0:
        return canvas
    inner = round(side * (1 - 2 * padding_ratio))
    if inner < 1:
        inner = 1
    canvas = canvas.resize((inner, inner), Image.LANCZOS)
    outer = side
    final = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    final.paste(canvas, ((outer - inner) // 2, (outer - inner) // 2), canvas)
    return final


def save_finals(
    trimmed: Image.Image,
    sizes: list[int],
    finals_dir: Path,
    slug: str,
    padding_ratio: float,
) -> list[dict]:
    """逐尺寸输出 <slug>_<size>.png,返回 [{size, path}] (绝对路径)。"""
    finals_dir.mkdir(parents=True, exist_ok=True)
    square = make_square(trimmed, padding_ratio)
    results = []
    for size in sizes:
        out = square.resize((size, size), Image.LANCZOS) if square.size != (size, size) else square
        path = finals_dir / f"{slug}_{size}.png"
        out.save(path, "PNG")
        results.append({"size": size, "path": str(path.resolve())})
    return results


def save_master(trimmed: Image.Image, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed.save(path, "PNG")
    return str(path.resolve())
