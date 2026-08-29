"""Stage 4b: 制作辅助 — 修 bug / 视觉检测后修复"""

from __future__ import annotations

import json
from pathlib import Path

from .settings import MODELS
from .build import call_llm, BUILD_SYSTEM_PROMPT


def fix_code(html_path: Path, issues: list[str], design_title: str) -> bool:
    """根据视觉检测问题修复游戏代码"""
    print(f"    → 修复 {len(issues)} 个问题...")
    
    html = html_path.read_text(encoding="utf-8")
    model_cfg = MODELS["code"]
    
    issues_text = "\n".join(f"  - {i}" for i in issues)
    
    prompt = f"""当前游戏(index.html)存在以下视觉/功能问题:

{issues_text}

请修改代码修复这些问题。直接输出修复后的**完整 html 文件**（不要解释、不要围栏）。
当前文件开头: {html[:200]}
"""
    
    try:
        fixed = call_llm([
            {"role": "system", "content": BUILD_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], model_cfg)
        
        if fixed.startswith("```"):
            lines = fixed.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            fixed = "\n".join(lines)
        
        html_path.write_text(fixed, encoding="utf-8")
        return True
    except Exception as e:
        print(f"    ⚠ 修复失败: {e}")
        return False