"""Stage 5: 视觉检测 — Playwright 截图 + 视觉模型审核"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import shutil

from .models import QAResult
from .settings import MODELS


QA_SYSTEM_PROMPT = """你是一个游戏视觉 QA 专家。你收到一张游戏截图，请从以下维度评分（1-10）并列出问题:

1. 布局完整性: 所有 UI 元素是否可见、无重叠
2. 视觉清晰度: 文字可读、颜色协调
3. 游戏状态正常: 分数/生命/波次等关键数值显示正确
4. 交互反馈: 按钮/点击区域有视觉反馈

输出 JSON（不要 markdown 围栏）:
{
  "score": 8.5,
  "issues": ["问题1", "问题2"],
  "passed": true,
  "suggestions": "可选修复建议"
}
评分 < 7 时 passed=false。
"""


def try_playwright_screenshot(html_path: Path, output_path: Path) -> bool:
    """用 Playwright 截取游戏截图"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("    ⚠ Playwright 未安装，尝试安装...")
        import subprocess
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], 
                          capture_output=True, timeout=60)
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                          capture_output=True, timeout=120)
            from playwright.sync_api import sync_playwright
        except Exception:
            return False
    
    try:
        html_abs = html_path.resolve()
        html_url = html_abs.as_uri()
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 720, "height": 1280})
            page.goto(html_url, timeout=15000, wait_until="networkidle")
            page.wait_for_timeout(1000)  # 等渲染
            page.screenshot(path=str(output_path), full_page=True)
            browser.close()
        return output_path.exists()
    except Exception as e:
        print(f"    ⚠ Playwright 截图失败: {e}")
        return False


def try_desktop_screenshot(html_path: Path, output_path: Path) -> bool:
    """尝试用桌面工具截图"""
    # 先尝试用浏览器打开
    try:
        import subprocess
        import webbrowser
        import time
        
        html_abs = html_path.resolve()
        webbrowser.open(html_abs.as_uri())
        time.sleep(3)
        # 尝试用桌面-agent 截图
        try:
            from hermes_tools import terminal
            # 截图桌面
            r = terminal("powershell -Command \"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('{F13}');\"", timeout=5)
            # 实际上 desktop-agent 可以截图，但先跳过
            pass
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"    ⚠ 桌面截图失败: {e}")
        return False


def call_vision(screenshot_path: Path) -> dict:
    """调用视觉模型审核截图"""
    from .settings import get_http_client
    import base64
    
    model_cfg = MODELS["vision"]
    client = get_http_client(model_cfg)
    
    img_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()
    data_uri = f"data:image/png;base64,{img_b64}"
    
    resp = client.post(
        f"{model_cfg['base_url']}/chat/completions",
        json={
            "model": model_cfg["model"],
            "messages": [
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请审核此游戏截图，给出评分和问题列表。"},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "temperature": model_cfg["temperature"],
            "max_tokens": model_cfg["max_tokens"],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    
    # 解析 JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试从文本中提取 JSON
        import re
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            data = {"score": 5, "issues": ["无法解析审核结果"], "passed": False}
    
    return data


def run(game_files: list[str], output_dir: Path) -> list[dict]:
    """Stage 5: 视觉检测"""
    print(f"  [Stage 5/5] 视觉检测: 截图 + 审核...")
    sys.stdout.flush()
    
    qa_dir = output_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    
    for gf in game_files:
        html_path = Path(gf)
        if html_path.suffix not in (".html", ".htm"):
            continue
        
        screenshot_path = qa_dir / f"{html_path.stem}.png"
        
        # 截图
        if not try_playwright_screenshot(html_path, screenshot_path):
            print(f"    ⚠ 无法截图，跳过视觉检测")
            results.append({
                "file": gf,
                "screenshot": "",
                "score": 0,
                "issues": ["截图失败: Playwright 不可用"],
                "passed": False,
                "skipped": True,
            })
            continue
        
        if not screenshot_path.exists():
            results.append({
                "file": gf,
                "screenshot": "",
                "score": 0,
                "issues": ["截图文件未生成"],
                "passed": False,
                "skipped": True,
            })
            continue
        
        print(f"    ✓ 截图: {screenshot_path} ({screenshot_path.stat().st_size} bytes)")
        
        # 视觉审核
        try:
            review = call_vision(screenshot_path)
            score = review.get("score", 5)
            issues = review.get("issues", [])
            passed = review.get("passed", score >= 7)
            
            print(f"    ✓ 审核评分: {score}/10")
            if issues:
                for issue in issues[:3]:
                    print(f"      - {issue}")
            if not passed:
                print(f"    ⚠ 未通过，问题: {len(issues)} 个")
            
            results.append({
                "file": gf,
                "screenshot": str(screenshot_path),
                "score": score,
                "issues": issues,
                "passed": passed,
                "suggestions": review.get("suggestions", ""),
            })
        except Exception as e:
            print(f"    ⚠ 视觉审核失败: {e}")
            results.append({
                "file": gf,
                "screenshot": str(screenshot_path),
                "score": 0,
                "issues": [f"审核API调用失败: {e}"],
                "passed": False,
            })
    
    # 保存 QA 报告
    qa_report = qa_dir / "qa_report.json"
    qa_report.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"    ✓ QA 报告: {qa_report}")
    
    return results