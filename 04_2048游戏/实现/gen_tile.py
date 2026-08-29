"""生成2048空白卡片模板 + 复制背景图到assets"""
import asyncio, sys, json, shutil
from pathlib import Path

ROOT = Path(r"D:\core\工作流设计\02_游戏素材生成\实现")
sys.path.insert(0, str(ROOT))
from app.pipeline import generate_asset_pipeline, cutout_image_pipeline
from app.settings import load_app_config

ASSETS = Path(r"D:\core\工作流设计\04_2048游戏\实现\out\2048-game\assets")
ASSETS.mkdir(parents=True, exist_ok=True)

async def main():
    env, cfg = load_app_config()
    
    # 生成1张空白卡片模板（无数字，纯圆角方块，卡片形状）
    print("=== 生成空白卡片模板 ===")
    r = await generate_asset_pipeline(
        description="2048 game empty tile, flat minimal UI style, cream beige #cdc1b4, rounded square card, no text, no numbers, clean flat UI element, subtle shadow, rounded corners, solid color square card",
        category="item",
        style="2048_flat_style",
        sizes=[256, 128],
        env=env, cfg=cfg,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2))
    
    # 复制到assets
    task_dir = Path(r.get("task_dir", ""))
    if task_dir and task_dir.exists():
        # 用master或draft复制
        src = task_dir / "master.png"
        if not src.exists():
            src = task_dir / "draft.png"
        if src.exists():
            dst = ASSETS / "tile_template.png"
            shutil.copy2(str(src), str(dst))
            print(f"  已复制卡片模板: {dst}")
    
    # 复制背景图
    bg_src = r"D:\core\工作流设计\02_游戏素材生成\实现\out\20260818_194450_seamless-warm-beige-2048-game-ba\master.png"
    bg = Path(bg_src)
    if bg.exists():
        dst = ASSETS / "background.png"
        shutil.copy2(str(bg_src), str(dst))
        print(f"  已复制背景图: {dst}")
    else:
        # 找背景图目录
        print("  ⚠ 背景图未找到，跳过")

if __name__ == "__main__":
    asyncio.run(main())