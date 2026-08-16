"""缩放输出测试:裁边 / 正方形画布 / 多尺寸 PNG。"""
from __future__ import annotations

from PIL import Image

from app import scaler


def _red_on_transparent() -> Image.Image:
    # 透明画布中央 49x49 红块(与 cutout 后的形态一致)
    img = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    for x in range(24, 73):
        for y in range(24, 73):
            img.putpixel((x, y), (200, 30, 30, 255))
    return img


def test_trim_to_alpha():
    trimmed = scaler.trim_to_alpha(_red_on_transparent())
    assert trimmed.size == (49, 49)


def test_make_square_no_padding():
    square = scaler.make_square(Image.new("RGBA", (40, 60), (255, 0, 0, 255)), 0.0)
    assert square.size == (60, 60)
    # 宽 40 居中 → 左右各 10px 透明
    assert square.split()[-1].getpixel((5, 30)) == 0
    assert square.split()[-1].getpixel((30, 30)) == 255


def test_make_square_with_padding():
    square = scaler.make_square(Image.new("RGBA", (60, 60), (255, 0, 0, 255)), 0.1)
    assert square.size == (60, 60)
    # 10% 边距 → 中心不透明,边角透明
    assert square.split()[-1].getpixel((30, 30)) > 0
    assert square.split()[-1].getpixel((2, 2)) == 0


def test_save_finals(tmp_path):
    trimmed = scaler.trim_to_alpha(_red_on_transparent())
    finals = scaler.save_finals(trimmed, [64, 32], tmp_path / "finals", "sword", 0.06)
    assert len(finals) == 2
    for item, size in zip(finals, (64, 32)):
        img = Image.open(item["path"])
        assert img.size == (size, size)
        assert img.mode == "RGBA"
        assert item["path"].endswith(f"sword_{size}.png")


def test_save_master(tmp_path):
    path = scaler.save_master(_red_on_transparent(), tmp_path / "master.png")
    assert Image.open(path).mode == "RGBA"
