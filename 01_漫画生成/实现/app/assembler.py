"""阶段 4 · 拼接:按顺序把分镜一格一格往下拼成条漫(Pillow)。

每格 = 图片 + 底部字幕条(编号 + 台词);缺图的格用占位框(标 FAILED),
保证断点续跑/部分失败时也能看到整体效果。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.splitter import Panel


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf"):  # Windows 中文字体优先
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_panel(panel: Panel, img_path: Path, size: tuple[int, int], caption_h: int) -> Image.Image:
    """单格渲染:图片区 + 字幕条(编号/字幕/失败占位)。字幕优先用优化后的 caption。"""
    w, h = size
    cell = Image.new("RGB", (w, h + caption_h), "white")
    draw = ImageDraw.Draw(cell)

    if panel.image_file and img_path.exists():
        img = Image.open(img_path).convert("RGB")
        if img.size != (w, h):
            img = img.resize((w, h), Image.LANCZOS)
        cell.paste(img, (0, 0))
    else:  # 失败占位框:流水线仍可完整拼接
        draw.rectangle([0, 0, w - 1, h - 1], outline="#c0392b", width=4)
        draw.text(
            (w // 2, h // 2),
            f"PANEL {panel.index} FAILED",
            fill="#c0392b",
            font=_font(28),
            anchor="mm",
        )

    # 字幕条:背景 + 编号 + 字幕(caption 优先,回退 dialogue/scene)
    draw.rectangle([0, h, w - 1, h + caption_h - 1], fill="#f2f2f2", outline="#dddddd")
    font = _font(min(26, caption_h - 8))
    caption = f"[{panel.index}] {panel.caption or panel.dialogue or panel.scene[:40]}"
    while draw.textlength(caption, font=font) > w - 24 and len(caption) > 4:
        caption = caption[:-2] + "…"
    draw.text((12, h + caption_h // 2), caption, fill="#222222", font=font, anchor="lm")
    return cell


def assemble_comic(
    panels: list[Panel], panels_dir: Path, out_path: Path,
    size: tuple[int, int], caption_h: int = 64, gap: int = 12, background: str = "#ffffff",
    fmt: str = "JPEG", quality: int = 90,
) -> Path:
    """按 index 顺序竖排拼接(像列表一样逐格追加),输出条漫长图。

    fmt=JPEG(默认):体积约为 PNG 的 1/5,适合最终交付;
    PNG:无损,适合再编辑。out_path 后缀按 fmt 自动纠正。
    """
    panels = sorted(panels, key=lambda p: p.index)
    cells = [render_panel(p, panels_dir / p.image_file if p.image_file else panels_dir, size, caption_h) for p in panels]
    w = size[0]
    total_h = sum(c.height for c in cells) + gap * (len(cells) - 1)
    comic = Image.new("RGB", (w, total_h), background)
    y = 0
    for cell in cells:
        comic.paste(cell, (0, y))
        y += cell.height + gap
    ext = ".jpg" if fmt.upper() == "JPEG" else ".png"
    out_path = out_path.with_suffix(ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"quality": quality} if fmt.upper() == "JPEG" else {}
    comic.save(out_path, fmt.upper(), **save_kwargs)
    return out_path
