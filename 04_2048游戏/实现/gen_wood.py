"""生成木纹版2048素材：木纹背景 + 木质卡片模板"""
import asyncio, sys, json, shutil
from pathlib import Path

ROOT = Path(r"D:\core\工作流设计\02_游戏素材生成\实现")
sys.path.insert(0, str(ROOT))
from app.pipeline import generate_asset_pipeline
from app.settings import load_app_config

ASSETS = Path(r"D:\core\工作流设计\04_2048游戏\实现\out\2048-game\assets")
ASSETS.mkdir(parents=True, exist_ok=True)

WOOD_STYLE = "natural wood texture, warm honey oak wood grain, organic wavy grain lines, rich natural wood tones, photorealistic wood material, no text, no watermark"

async def main():
    env, cfg = load_app_config()
    
    # 1. 木纹背景（满幅）
    print("=== 生成木纹背景 ===")
    rb = await generate_asset_pipeline(
        description="natural honey oak wood texture background, warm golden brown wood grain with elegant natural swirls and wavy lines, photorealistic wooden surface, full-bleed seamless pattern, rich warm amber tones, game background, no text no objects",
        category="background",
        style=WOOD_STYLE,
        sizes=[512, 256],
        env=env, cfg=cfg,
    )
    print(f"背景: status={rb.get('status')} review={rb.get('review',{}).get('status')}")
    bg_task = Path(rb.get("task_dir", ""))
    if bg_task.exists():
        src = bg_task / "master.png"
        if src.exists():
            shutil.copy2(str(src), str(ASSETS / "background.png"))
            print(f"  ✓ 背景: {ASSETS / 'background.png'}")
    
    # 2. 木质空卡片模板（圆角方形，木纹，无数字）
    print("\n=== 生成木质卡片模板 ===")
    rt = await generate_asset_pipeline(
        description="empty rounded square game tile made of natural oak wood, warm honey wood grain texture on the tile face, flat front view, square wooden board piece with rounded corners, slightly darker wood edge border, clean simple game UI element, no text no numbers",
        category="item",
        style=WOOD_STYLE,
        sizes=[256, 128],
        env=env, cfg=cfg,
    )
    print(f"卡片: status={rt.get('status')} review={rt.get('review',{}).get('status')}")
    tile_task = Path(rt.get("task_dir", ""))
    if tile_task.exists():
        # 元素类会用rembg抠图，需要master原图（带背景）还是cutout？
        # 这里卡片应该是完整方块，用master（完整图）
        src = tile_task / "master.png"
        if src.exists():
            shutil.copy2(str(src), str(ASSETS / "tile_template.png"))
            print(f"  ✓ 卡片: {ASSETS / 'tile_template.png'}")
    
    # 3. 深色木质卡片（高数值用，比如128+）
    print("\n=== 生成深色木质卡片 ===")
    rd = await generate_asset_pipeline(
        description="empty rounded square game tile made of dark walnut wood, rich dark brown wood grain texture, flat front view, square wooden board piece with rounded corners, elegant dark wood, clean simple game UI element, no text no numbers",
        category="item",
        style=WOOD_STYLE,
        sizes=[256, 128],
        env=env, cfg=cfg,
    )
    print(f"深色卡片: status={rd.get('status')} review={rd.get('review',{}).get('status')}")
    dark_task = Path(rd.get("task_dir", ""))
    if dark_task.exists():
        src = dark_task / "master.png"
        if src.exists():
            shutil.copy2(str(src), str(ASSETS / "tile_template_dark.png"))
            print(f"  ✓ 深色卡片: {ASSETS / 'tile_template_dark.png'}")

if __name__ == "__main__":
    asyncio.run(main())