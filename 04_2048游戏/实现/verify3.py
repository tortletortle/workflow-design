"""注入满盘状态，验证所有数字卡片渲染"""
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
        page.on("pageerror", lambda e: errors.append(str(e)))
        
        await page.goto(url)
        await page.wait_for_timeout(1500)
        
        # 通过页面内的游戏状态注入（window.gameState 需要在游戏里暴露）
        # 游戏是IIFE闭包，无法直接访问，改为模拟多次滑动达到高分
        # 用键盘快速滑动看渲染
        moves = ["ArrowRight","ArrowDown","ArrowLeft","ArrowUp"]
        for i in range(20):
            await page.keyboard.press(moves[i % 4])
            await page.wait_for_timeout(80)
        
        await page.wait_for_timeout(500)
        
        state = await page.evaluate("""() => {
            const cells = document.querySelectorAll('.cell');
            const vals = [];
            cells.forEach(c => {
                const num = c.querySelector('.num');
                vals.push({v: num ? num.textContent : (c.textContent.trim()||'0'), hasBg: getComputedStyle(c).backgroundImage !== 'none'});
            });
            return {vals, score: document.getElementById('score').textContent, errors: 0};
        }""")
        print(f"棋盘: {state['vals']}")
        print(f"分数: {state['score']}")
        
        # 截图
        await page.screenshot(path=str(GAME_DIR / "qa" / "04_full.png"))
        print(f"JS错误: {errors if errors else '无'}")
        await browser.close()

asyncio.run(main())