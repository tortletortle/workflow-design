"""Stage 4: 制作 — 生成 HTML5 游戏代码"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .settings import MODELS


BUILD_SYSTEM_PROMPT = """你是一个资深 HTML5 游戏开发者。根据游戏设计文档和现有素材/模板，生成一个完整的单文件 HTML5 Canvas 游戏。

硬性要求:
1. 输出**一个 html 文件**，包含全部 CSS/JS，无外部依赖（素材用 base64 data URI 内嵌或绘制占位图形）
2. 720×1280 竖屏，居中显示，黑色背景
3. 包含以下完整游戏阶段: 主菜单 → 游戏进行 → 游戏结束
4. 顶部 HUD 显示关键数值（生命/分数/波次等）
5. 触摸/键盘操作均可（方便移动端和桌面测试）
6. 代码中注释清晰，方便后续修改
7. 素材加载: 从 'assets/' 相对路径加载 PNG（使用 <img> 预加载），若加载失败自动绘制占位图形回退

输出格式: 直接输出 html 文件内容，不要 markdown 围栏，不要解释。
"""


def get_template_source(core_file: str) -> str:
    """读取模板核心逻辑"""
    try:
        return Path(core_file).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def call_llm(messages: list[dict], model_cfg: dict) -> str:
    from .settings import get_http_client
    
    client = get_http_client(model_cfg)
    
    resp = client.post(
        f"{model_cfg['base_url']}/chat/completions",
        json={
            "model": model_cfg["model"],
            "messages": messages,
            "temperature": model_cfg["temperature"],
            "max_tokens": model_cfg["max_tokens"],
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def run(design_data: dict, template_data: dict, assets_data: list[dict], output_dir: Path) -> list[str]:
    """Stage 4: 制作 HTML5 游戏"""
    print(f"  [Stage 4/5] 制作: 生成游戏代码...")
    sys.stdout.flush()
    
    game_dir = output_dir / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    
    # 汇总上下文
    assets_summary = "\n".join(
        f"  - {a.get('id')}: {a.get('description')} ({a.get('category')}) → {a.get('finals', [])[:1]}"
        for a in (assets_data or [])
    ) or "  (无素材)"
    
    design_summary = json.dumps(design_data, ensure_ascii=False, indent=2)
    template_source = get_template_source(template_data.get("core_file", ""))
    
    prompt = f"""# 游戏设计文档
{design_summary}

# 素材清单
{assets_summary}

# 可用模板逻辑（供参考，可复用核心算法）
```typescript
{template_source[:8000]}
```

请生成完整游戏代码。游戏标题: {design_data.get('title', 'My Game')}
生成后保存到 game/index.html。
"""
    
    user_content = prompt
    
    model_cfg = MODELS["code"]
    html = call_llm([
        {"role": "system", "content": BUILD_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ], model_cfg)
    
    # 清理围栏
    if html.startswith("```"):
        lines = html.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        html = "\n".join(lines)
    
    html_path = game_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"    ✓ 游戏代码: {html_path} ({html_path.stat().st_size} bytes)")
    
    return [str(html_path)]