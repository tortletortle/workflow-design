"""Stage 2: 模板检索 — 从源码库匹配游戏模板"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .models import TemplateMatch
from .settings import MODELS, list_local_templates, load_index


def run(design_data: dict, output_dir: Path) -> TemplateMatch:
    """Stage 2: 模板检索"""
    print(f"  [Stage 2/5] 模板检索: 匹配源码库...")
    sys.stdout.flush()
    
    game_type = design_data.get("game_type", "")
    title = design_data.get("title", "").lower()
    description = design_data.get("description", "").lower()
    
    # 1. 搜 local/ 模板
    local_templates = list_local_templates()
    keywords = f"{game_type} {title} {description}"
    
    best_match = None
    best_score = 0
    
    for tmpl in local_templates:
        name = tmpl["name"].lower()
        score = 0
        # 类型关键词匹配
        type_keywords = {
            "towerdefense": ["tower", "defense", "td", "塔防"],
            "snake": ["snake", "贪吃蛇"],
            "tetris": ["tetris", "tetris", "俄罗斯方块"],
            "2048": ["2048"],
            "mine": ["mine", "扫雷"],
            "gobang": ["gobang", "五子棋"],
            "reversi": ["reversi", "黑白棋"],
            "jump": ["jump", "跳一跳"],
            "puzzle": ["puzzle", "拼图"],
            "brick": ["brick", "打砖块"],
            "get47": ["get47"],
            "link": ["link", "连连看"],
        }
        for gt, kws in type_keywords.items():
            if any(kw in keywords for kw in kws) and any(kw in name for kw in kws):
                score += 3
        # 关键词模糊匹配
        for kw in keywords.split():
            if kw in name and len(kw) > 2:
                score += 1
        if score > best_score:
            best_score = score
            best_match = tmpl
    
    if best_match and best_score >= 2:
        result = TemplateMatch(
            matched=True,
            name=best_match["name"],
            path=best_match["path"],
            core_file=best_match.get("core_file", ""),
            game_type=game_type,
            notes=f"local/ 模板匹配 (score={best_score})",
        )
        print(f"    ✓ 匹配模板: {result.name}")
        print(f"    核心文件: {result.core_file}")
    else:
        print(f"    ⚠ 未匹配到本地模板，将使用通用骨架")
        result = TemplateMatch(matched=False, notes="no local template matched")
    
    # 保存结果
    result_path = output_dir / "template_match.json"
    result_path.write_text(
        json.dumps({
            "matched": result.matched,
            "name": result.name,
            "path": result.path,
            "core_file": result.core_file,
            "game_type": result.game_type,
            "notes": result.notes,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    return result