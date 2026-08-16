"""一致性与满幅类别测试:主体卡 / 风格库 / 风格锚 / background 不抠图 / 独立视觉端。"""
from __future__ import annotations

from PIL import Image

from app.client import ChatClient, ImageClient
from app.consistency import list_library, resolve_style, save_style
from app.pipeline import generate_asset_pipeline
from app.settings import AppConfig

from .conftest import (VERDICT_PASS, asset_png, mock_chat_transport,
                       mock_image_transport, small_cfg)


async def test_background_category_skips_cutout(chat, image, env, tmp_path):
    result = await generate_asset_pipeline(
        "夜晚的魔法森林", "background", "", [64],
        env=env, cfg=small_cfg(), chat=chat, image=image, out_root=tmp_path,
    )
    assert result["status"] == "ok"
    assert result["cutout"]["method"].startswith("skipped")
    assert "path" not in result["cutout"]                     # 不产 cutout.png
    assert "complete scene filling the entire frame" in result["prompt"]
    assert "white background" not in result["prompt"]         # 满幅不加白底
    assert not (tmp_path / result["task_id"] / "cutout.png").exists()
    img = Image.open(result["finals"][0]["path"])
    assert img.split()[-1].getpixel((0, 0)) == 255            # 满幅不透明,无透明角


async def test_tile_also_fullbleed(chat, image, env, tmp_path):
    result = await generate_asset_pipeline(
        "草地砖块贴图", "tile", "", [64],
        env=env, cfg=small_cfg(), chat=chat, image=image, out_root=tmp_path,
    )
    assert result["cutout"]["method"].startswith("skipped")


async def test_subject_card_roundtrip(chat, image, env, tmp_path):
    cfg = small_cfg(lib_dir=tmp_path / "lib")
    r1 = await generate_asset_pipeline(
        "红披风骑士", "character", "pixel art", [64], save_subject_as="red knight",
        env=env, cfg=cfg, chat=chat, image=image, out_root=tmp_path,
    )
    assert any("subject card saved: red knight" in n for n in r1["notes"])
    lib = list_library(cfg)
    assert [s["name"] for s in lib["subjects"]] == ["red knight"]
    card = lib["subjects"][0]["card"]
    assert card and lib["subjects"][0]["style"] == "pixel art 16-bit"

    r2 = await generate_asset_pipeline(
        "骑士挥剑的瞬间", "character", "", [64], subject_ref="red knight",
        env=env, cfg=cfg, chat=chat, image=image, out_root=tmp_path,
    )
    assert any("subject card loaded" in n for n in r2["notes"])
    assert "LOCKED APPEARANCE" in r2["prompt"]
    assert card in r2["prompt"]                                # 卡文本逐字注入
    assert "pixel art" in r2["prompt"]                         # 未指定 style → 沿用卡的风格


async def test_subject_ref_missing_ignored(chat, image, env, tmp_path):
    result = await generate_asset_pipeline(
        "火球", "item", "", [64], subject_ref="no such card",
        env=env, cfg=small_cfg(lib_dir=tmp_path / "lib"), chat=chat, image=image,
        out_root=tmp_path,
    )
    assert any("subject card not found" in n for n in result["notes"])
    assert "LOCKED APPEARANCE" not in result["prompt"]


async def test_named_style_used_in_pipeline(chat, image, env, tmp_path):
    cfg = small_cfg(lib_dir=tmp_path / "lib")
    save_style("像素风", "16-bit pixel art, limited palette", cfg)
    result = await generate_asset_pipeline(
        "一把剑", "item", "像素风", [64],
        env=env, cfg=cfg, chat=chat, image=image, out_root=tmp_path,
    )
    assert any("style resolved from library" in n for n in result["notes"])
    assert "16-bit pixel art, limited palette" in result["prompt"]


async def test_global_style_anchor(chat, image, env, tmp_path):
    cfg = small_cfg(lib_dir=tmp_path / "lib")
    cfg.style.anchor = "dark gothic oil painting"
    result = await generate_asset_pipeline(
        "一把剑", "item", "", [64],
        env=env, cfg=cfg, chat=chat, image=image, out_root=tmp_path,
    )
    assert "dark gothic oil painting" in result["prompt"]


def test_resolve_style_semantics(tmp_path):
    cfg = AppConfig(style={"lib_dir": str(tmp_path)})
    save_style("像素风", "16-bit pixel art", cfg)
    assert resolve_style("像素风", cfg) == ("16-bit pixel art", "style-lib:像素风")
    assert resolve_style("watercolor", cfg) == ("watercolor", "literal")
    assert resolve_style("", cfg) == ("", "")


async def test_injected_vision_client_used(env, tmp_path):
    """审核走注入的独立 vision_chat(与分解用的 chat 分离)。"""
    chat = ChatClient(env, transport=mock_chat_transport(verdict="no, always fail"))  # 若误用必挂
    vision = ChatClient(env, transport=mock_chat_transport(verdict=VERDICT_PASS))
    image = ImageClient(env, transport=mock_image_transport())
    env_v = env.model_copy(update={
        "vision_model": "v-remote",
        "vision_base_url": "http://vision.test/v1",
        "vision_api_key": "sk-vision",
    })
    result = await generate_asset_pipeline(
        "火球特效", "effect", "", [64], env=env_v, cfg=small_cfg(),
        chat=chat, image=image, vision_chat=vision, out_root=tmp_path,
    )
    assert result["review"]["status"] == "ok"
    assert result["review"]["model"] == "v-remote"
    assert result["review"]["rounds"][0]["passed"] is True
