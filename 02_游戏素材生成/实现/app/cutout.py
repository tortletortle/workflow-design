"""背景抠除(抠图):rembg 主通道 + PIL 白底键控兜底。

rembg:AI 语义抠图(onnxruntime),未安装/调用失败自动降级;
white_key:利用"白底生成"前提,从四边泛洪标记背景连通近白区 → 透明。
  只删与边缘连通的白区,素材内部白色(白色盔甲/高光)受保护;
  主体本身近白导致误删过多时守卫放行原图(宁可留白底也不删空主体)。
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.settings import CutoutConfig

# 守卫:抠图后不透明像素占比低于此值 → 判定误删,保留原图
_MIN_OPAQUE_RATIO = 0.08


def _rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


def rembg_cutout(img: Image.Image) -> Image.Image:
    """rembg 语义抠图(首次调用会下载 ~170MB 模型,之后离线)。"""
    from rembg import remove

    out = remove(img)
    if out.mode != "RGBA":
        out = out.convert("RGBA")
    return out


def white_key_cutout(img: Image.Image, tolerance: int, feather: int) -> Image.Image:
    """白底键控:边缘泛洪抠背景 + alpha 羽化。"""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    # 1. 四周补 1px 纯白边,让 (0,0) 单点泛洪就能覆盖整圈背景
    padded = Image.new("RGBA", (w + 2, h + 2), (255, 255, 255, 255))
    padded.paste(rgba, (1, 1))
    gray = padded.convert("L")
    # 2. 从 (0,0) 泛洪:与种子(255)灰度差 <= tolerance 的连通区 = 背景,填 0
    ImageDraw.floodfill(gray, (0, 0), 0, thresh=tolerance)
    # 3. 背景(0) → alpha 0;其余 255。numpy 向量化
    arr = np.asarray(gray)
    alpha = np.where(arr == 0, 0, 255).astype(np.uint8)
    opaque_ratio = float((alpha > 0).mean())
    if opaque_ratio < _MIN_OPAQUE_RATIO:
        # 主体本身近白被误删 → 守卫:保留原图不透明(宁可留白底)
        return rgba
    alpha_img = Image.fromarray(alpha, "L").crop((1, 1, w + 1, h + 1))  # 去掉补边
    # 4. 羽化:先收缩 1px 吃掉白边光晕,再轻模糊过渡
    if feather > 0:
        alpha_img = alpha_img.filter(ImageFilter.MinFilter(3))
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(radius=feather))
    rgba.putalpha(alpha_img)
    return rgba


def cutout(img: Image.Image, cfg: CutoutConfig) -> tuple[Image.Image, str]:
    """抠图入口。返回 (RGBA 图, 所用方法名)。方法名含失败标记供结果透传。"""
    if cfg.engine in ("auto", "rembg") and _rembg_available():
        try:
            return rembg_cutout(img), "rembg"
        except Exception as e:
            if cfg.engine == "rembg":
                raise RuntimeError(f"rembg cutout failed: {e}") from e
            # auto:降级白底键控
    if cfg.engine == "rembg":
        raise RuntimeError("rembg not installed (pip install rembg / uv sync --extra rembg)")
    out = white_key_cutout(img, cfg.white_tolerance, cfg.feather)
    method = "white_key"
    # 守卫触发时(整图仍不透明)标注,调用方可在结果中提示
    if np.asarray(out.split()[-1]).min() >= 255:
        method = "white_key:guard_kept_original"
    return out, method


def load_image(data: bytes | Image.Image) -> Image.Image:
    """bytes 或 PIL Image → RGB Image。"""
    if isinstance(data, Image.Image):
        return data.copy()
    img = Image.open(io.BytesIO(data))
    img.load()
    return img
