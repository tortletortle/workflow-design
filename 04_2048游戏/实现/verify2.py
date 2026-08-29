"""详细检查2048卡片渲染效果"""
import asyncio
from pathlib import Path

GAME_DIR = Path(r"D:\core\工作流设计\04_2048游戏\实现\out\2048-game")

async def main():
    from playwright.async_api import async_playwright
    
    url = (GAME_DIR / "game" / "index.html").as_uri()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 480, "height": 800})
        
        await page.goto(url)
        await page.wait_for_timeout(2000)
        
        # 详细检查每个卡片
        info = await page.evaluate("""() => {
            const cells = document.querySelectorAll('.cell');
            return Array.from(cells).map((c, i) => {
                const cs = getComputedStyle(c);
                const num = c.querySelector('.num');
                return {
                    idx: i,
                    text: c.textContent.trim() || '(empty)',
                    hasNum: !!num,
                    numText: num?.textContent || '',
                    bgImage: cs.backgroundImage.slice(0, 80),
                    bgColor: cs.backgroundColor,
                    cellW: c.offsetWidth,
                    cellH: c.offsetHeight,
                };
            });
        }""")
        
        for c in info:
            if c.text != '(empty)':
                print(f"  卡片#{c.idx}: val={c.numText} 尺寸={c.cellW}x{c.cellH} bg={c.bgImage}")
        
        # 强制模拟合并到2048查看效果
        await page.evaluate("""() => {
            // 模拟棋盘接近满，显示各种数字
            import { grid, score, render } from './index.html';
        }""")
        
        await browser.close()

asyncio.run(main())