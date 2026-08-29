"""截图验证2048 AI素材版"""
import asyncio
from pathlib import Path

GAME_DIR = Path(r"D:\core\工作流设计\04_2048游戏\实现\out\2048-game")
QA_DIR = GAME_DIR / "qa"
QA_DIR.mkdir(parents=True, exist_ok=True)

async def main():
    from playwright.async_api import async_playwright
    
    url = (GAME_DIR / "game" / "index.html").as_uri()
    print(f"打开: {url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 480, "height": 800})
        
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        
        await page.goto(url)
        await page.wait_for_timeout(1500)
        
        # 截图1：初始状态
        await page.screenshot(path=str(QA_DIR / "01_initial.png"))
        
        # 模拟几次滑动，让数字铺开
        for _ in range(6):
            await page.keyboard.press("ArrowRight")
            await page.wait_for_timeout(150)
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(150)
        
        await page.wait_for_timeout(500)
        # 截图2：游戏中
        await page.screenshot(path=str(QA_DIR / "02_playing.png"))
        
        # 读取当前棋盘状态
        state = await page.evaluate("""() => {
            const cells = document.querySelectorAll('.cell');
            const vals = [];
            cells.forEach(c => { vals.push(c.textContent.trim() || c.querySelector('.num')?.textContent || '0'); });
            return { vals, score: document.getElementById('score').textContent, best: document.getElementById('best').textContent };
        }""")
        print(f"棋盘: {state}")
        
        print(f"JS错误: {errors if errors else '无'}")
        await browser.close()

asyncio.run(main())