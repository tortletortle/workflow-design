"""验证2048渲染后的实际视觉状态 - 检查图片元素和背景"""
import asyncio
from pathlib import Path

GAME_DIR = Path(r"D:\core\工作流设计\04_2048游戏\实现\out\2048-game")

async def main():
    from playwright.async_api import async_playwright
    
    url = (GAME_DIR / "game" / "index.html").as_uri()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 480, "height": 800})
        errors = []
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        
        await page.goto(url)
        await page.wait_for_timeout(2000)
        
        # 检查图片是否加载成功
        img_state = await page.evaluate("""() => {
            const result = {bg: '', tiles: {}, images: []};
            const wrap = document.getElementById('wrap');
            result.bg = getComputedStyle(wrap).backgroundImage;
            const cells = document.querySelectorAll('.cell');
            cells.forEach((c, i) => {
                const bg = getComputedStyle(c).backgroundImage;
                result.tiles[i] = bg;
                if (bg.includes('url')) result.images.push(bg);
            });
            return result;
        }""")
        print(f"背景: {img_state['bg'][:100]}")
        print(f"卡片背景: {list(img_state['tiles'].values())[:2]}")
        print(f"有图片的卡片数: {len(img_state['images'])}")
        
        # 截高清图
        await page.screenshot(path=str(GAME_DIR / "qa" / "03_verify.png"))
        
        print(f"JS错误: {errors if errors else '无'}")
        await browser.close()

asyncio.run(main())