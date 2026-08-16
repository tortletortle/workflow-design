"""批量生成《森林守护者》缺失素材(真实 API + haiku 视觉审核)。

用法: uv run python 游戏演示/batch_gen.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "实现"))

from app.pipeline import generate_asset_pipeline  # noqa: E402
from app.settings import load_app_config  # noqa: E402

GAME_DIR = Path(__file__).resolve().parent
ASSETS = GAME_DIR / "assets"

# 新生成:蝙蝠/火球/爆炸/爱心
NEW_ASSETS = [
    ("一只像素风的蝙蝠怪物,翅膀展开,露出小獠牙", "character", "bat", [128]),
    ("一颗像素风火球,圆形火焰核心,拖着火焰尾迹", "effect", "fireball", [128]),
    ("像素风爆炸特效,橙红色火花向四周炸开", "effect", "explosion", [128]),
    ("像素风红色爱心图标,饱满圆润,游戏UI血量用", "ui", "heart", [128]),
]

# 复用已有任务的产物: (任务目录前缀, 尺寸后缀, 目标名)
REUSE = [
    ("20260816_161503_female-knight", "_256.png", "player.png"),      # 站立女骑士
    ("20260816_162424_a-round-and-cute-blue-slime", "_256.png", "slime.png"),
    ("20260816_171323_a-glass-bottle-of-red-healing", "_256.png", "potion.png"),
    ("20260816_173019_a-glowing-magic-ring", "_256.png", "ring.png"),
    ("20260816_165705_a-single-golden-coin-icon", "_64.png", "coin.png"),
    ("20260816_161423_magical-dark-night-forest-with-g", "_512.png", "background.png"),
]

OUT_ROOT = Path(__file__).resolve().parent.parent / "实现" / "out"


def pick(prefix: str, suffix: str) -> Path | None:
    for finals in sorted(OUT_ROOT.glob(f"{prefix}*/finals")):
        match = sorted(finals.glob(f"*{suffix}"))
        if match:
            return match[-1]
    return None


async def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    env, cfg = load_app_config()

    # 1) 复用
    for prefix, suffix, name in REUSE:
        src = pick(prefix, suffix)
        if src:
            shutil.copyfile(src, ASSETS / name)
            print(f"reuse {name:14s} <- {src.parent.parent.name}")
        else:
            print(f"!! reuse miss: {prefix}*{suffix} -> {name}")

    # 2) 新生成(串行,观察每条审核结论)
    for description, category, name, sizes in NEW_ASSETS:
        r = await generate_asset_pipeline(description, category, "pixel art", sizes,
                                          env=env, cfg=cfg)
        verdict = r["review"]["rounds"][0] if r["review"].get("rounds") else {}
        print(f"gen {name:14s} {r['status']} calls={r['image_calls']} "
              f"review={r['review']['status']} score={verdict.get('score')} {r['elapsed_s']}s")
        if r["status"] != "ok":
            print(json.dumps(r, ensure_ascii=False)[:400])
            continue
        shutil.copyfile(r["finals"][0]["path"], ASSETS / f"{name}.png")

    print("\nassets now:", sorted(p.name for p in ASSETS.iterdir()))


if __name__ == "__main__":
    asyncio.run(main())
