"""Stage 1: 认知 — LLM 生成结构化游戏设计文档（GDD）"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .models import GameDesign
from .settings import MODELS


SYSTEM_PROMPT = """你是一个游戏设计专家。根据用户描述，生成结构化游戏设计文档。

输出严格 JSON 格式（不要 markdown 围栏），字段：
{
  "title": "游戏名称",
  "game_type": "游戏类型(如 towerdefense/snake/puzzle/2048/platformer/shooter/racing)",
  "description": "一句话描述",
  "core_mechanic": "核心机制",
  "game_loop": ["子玩法1", "子玩法2", ...],
  "controls": ["操作方式1", ...],
  "style": "美术风格",
  "resolution": [720, 1280],
  "assets": [
    {"id": "enemy_1", "name": "敌人1", "description": "中文描述", "category": "character", "style": "风格", "count": 1},
    {"id": "tower_1", "name": "炮塔1", "description": "中文描述", "category": "item", "style": "风格", "count": 1},
    ...
  ],
  "ui_layout": [
    {"element": "score_label", "type": "label", "position": "top-left"},
    ...
  ],
  "tech_stack": "html5 canvas"
}

category 必须是以下之一: character / item / icon / tile / effect / ui / background
assets 中的每个素材都要有 category 和 style。
"""


def call_llm(messages: list[dict], model_cfg: dict) -> str:
    """调用 LLM API"""
    from .settings import get_http_client
    
    client = get_http_client(model_cfg)
    payload = {
        "model": model_cfg["model"],
        "messages": messages,
        "temperature": model_cfg["temperature"],
        "max_tokens": model_cfg["max_tokens"],
    }
    
    resp = client.post(
        f"{model_cfg['base_url']}/chat/completions",
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def run(idea: str, output_dir: Path) -> GameDesign:
    """Stage 1: 认知阶段"""
    print(f"  [Stage 1/5] 认知: 分析游戏想法...")
    sys.stdout.flush()
    
    model_cfg = MODELS["design"]
    
    raw = call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"我想做一个游戏：{idea}"},
    ], model_cfg)
    
    # 解析 JSON
    data = json.loads(raw)
    design = GameDesign(
        title=data.get("title", "未命名游戏"),
        game_type=data.get("game_type", "unknown"),
        description=data.get("description", ""),
        core_mechanic=data.get("core_mechanic", ""),
        game_loop=data.get("game_loop", []),
        controls=data.get("controls", []),
        style=data.get("style", "cartoon"),
        resolution=tuple(data.get("resolution", [720, 1280])),
        assets=data.get("assets", []),
        ui_layout=data.get("ui_layout", []),
        tech_stack=data.get("tech_stack", "html5 canvas"),
    )
    
    # 保存设计文档
    design_path = output_dir / "design.json"
    design_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"    ✓ 游戏设计文档: {design_path}")
    print(f"    类型: {design.game_type} | 风格: {design.style}")
    print(f"    素材清单: {len(design.assets)} 项")
    
    return design