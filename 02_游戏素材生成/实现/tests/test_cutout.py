"""抠图测试:白底键控 / 内部白保护 / 守卫 / 引擎选择。"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app import cutout as cutout_mod
from app.settings import CutoutConfig

from .conftest import asset_png


def _load(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


CFG = CutoutConfig(engine="white_key", white_tolerance=18, feather=2)


def test_white_key_removes_background_only():
    out = cutout_mod.white_key_cutout(_load(asset_png()), CFG.white_tolerance, CFG.feather)
    assert out.mode == "RGBA"
    a = out.split()[-1]
    # 四角背景 → 透明
    assert a.getpixel((0, 0)) == 0
    assert a.getpixel((95, 95)) == 0
    # 主体红块 → 不透明
    assert a.getpixel((30, 30)) > 0
    # 主体内部的白点(不与边缘连通)→ 受保护,不透明
    assert a.getpixel((48, 48)) > 0


def test_white_key_guard_keeps_all_white_subject():
    buf = io.BytesIO()
    Image.new("RGB", (96, 96), (255, 255, 255)).save(buf, "PNG")
    out = cutout_mod.white_key_cutout(_load(buf.getvalue()), 18, 2)
    # 全白图误删后不透明占比 0 → 守卫触发,保留原图
    assert out.split()[-1].getpixel((48, 48)) == 255


def test_cutout_auto_without_rembg(monkeypatch):
    monkeypatch.setattr(cutout_mod, "_rembg_available", lambda: False)
    out, method = cutout_mod.cutout(_load(asset_png()), CutoutConfig(engine="auto"))
    assert method == "white_key"
    assert out.mode == "RGBA"
    assert out.split()[-1].getpixel((0, 0)) == 0


def test_cutout_engine_rembg_missing_raises(monkeypatch):
    monkeypatch.setattr(cutout_mod, "_rembg_available", lambda: False)
    with pytest.raises(RuntimeError, match="rembg not installed"):
        cutout_mod.cutout(_load(asset_png()), CutoutConfig(engine="rembg"))


def test_cutout_rembg_failure_degrades(monkeypatch):
    monkeypatch.setattr(cutout_mod, "_rembg_available", lambda: True)

    def boom(img):
        raise RuntimeError("onnx crashed")

    monkeypatch.setattr(cutout_mod, "rembg_cutout", boom)
    out, method = cutout_mod.cutout(_load(asset_png()), CutoutConfig(engine="auto"))
    assert method == "white_key"  # auto 模式下 rembg 挂了 → 白底键控兜底
