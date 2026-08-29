"""2048素材生成 - 先试水数字2卡片+背景图"""
import asyncio, sys, json
from pathlib import Path

ROOT = Path(r"D:\core\工作流设计\02_游戏素材生成\实现")
sys.path.insert(0, str(ROOT))
from app.pipeline import generate_asset_pipeline
from app.settings import load_app_config

async def main():
    env, cfg = load_app_config()
    
    # 1. 数字2卡片试水
    print("=== 生成数字2卡片 ===")
    r2 = await generate_asset_pipeline(
        description="2048 game number tile showing the digit 2, flat minimal UI style, cream beige background #eee4da, rounded square card, dark brown bold number centered, subtle shadow, soft rounded corners, no text besides the number, clean crisp digit",
        category="ui",
        style="flat minimal 2048 game style, solid color tile, rounded corners, clean UI",
        sizes=[256, 128],
        save_style_as="2048_flat_style",
        env=env, cfg=cfg,
    )
    print(json.dumps(r2, ensure_ascii=False, indent=2))
    
    # 2. 背景图
    print("\n=== 生成背景图 ===")
    rb = await generate_asset_pipeline(
        description="2048 game background, warm beige #faf8ef, subtle wood grain texture, flat minimal design, clean light background, no text, no objects, seamless subtle pattern",
        category="background",
        style="flat minimal 2048 game style",
        sizes=[512, 256],
        env=env, cfg=cfg,
    )
    print(json.dumps(rb, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())