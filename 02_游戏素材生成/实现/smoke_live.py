"""live 冒烟脚本(真实 API,花真实额度):跑一次主流程 + rembg 验证。

用法:
    uv run python smoke_live.py                # 主流程: 一把像素风火焰剑
    uv run python smoke_live.py "描述" 类别     # 自定义
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from PIL import Image

from app.pipeline import generate_asset_pipeline, list_assets
from app.settings import load_app_config


def report(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "ok":
        sys.exit(1)
    # 透明度抽查: 终稿四角应为透明
    for item in result["finals"]:
        img = Image.open(item["path"])
        corners = [img.split()[-1].getpixel(p) for p in
                   [(0, 0), (img.width - 1, 0), (0, img.height - 1), (img.width - 1, img.height - 1)]]
        print(f"check {item['path']}: size={img.size} mode={img.mode} corner_alpha={corners}")


async def main() -> None:
    description = sys.argv[1] if len(sys.argv) > 1 else "一把像素风格的火焰剑,剑身缠绕火焰"
    category = sys.argv[2] if len(sys.argv) > 2 else "item"
    env, cfg = load_app_config()
    print(f"endpoint: {env.base_url}  image_model: {env.image_model}  vision: {env.vision_model or '(none)'}")
    result = await generate_asset_pipeline(description, category, "pixel art", [512, 256, 128],
                                           env=env, cfg=cfg)
    report(result)
    summary = list_assets(cfg=cfg)
    print(f"total tasks in out/: {summary['count']}")


if __name__ == "__main__":
    Path("out").mkdir(exist_ok=True)
    asyncio.run(main())
