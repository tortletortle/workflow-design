"""数据模型定义"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GameDesign:
    """游戏设计文档"""
    title: str
    game_type: str
    description: str
    core_mechanic: str
    game_loop: list[str]
    controls: list[str]
    style: str
    resolution: tuple[int, int] = (720, 1280)
    assets: list[dict] = field(default_factory=list)
    ui_layout: list[dict] = field(default_factory=list)
    tech_stack: str = "html5 canvas"


@dataclass
class TemplateMatch:
    """模板匹配结果"""
    matched: bool
    name: str = ""
    path: str = ""
    core_file: str = ""
    game_type: str = ""
    notes: str = ""


@dataclass
class AssetResult:
    """单个素材生成结果"""
    description: str
    category: str
    style: str
    success: bool
    draft_path: str = ""
    finals: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class QAResult:
    """视觉检测结果"""
    screenshot: str
    score: float
    issues: list[str] = field(default_factory=list)
    passed: bool = True


@dataclass
class Checkpoint:
    """工作流检查点"""
    stage: str  # design / template / assets / build / qa / done
    idea: str
    output_dir: str
    design: dict | None = None
    template: dict | None = None
    assets: list[dict] | None = None
    game_files: list[str] | None = None
    qa_results: list[dict] | None = None
    error: str | None = None

    def save(self, path: Path | str):
        import json
        Path(path).write_text(
            json.dumps({"__stage__": self.stage, **self.__dict__}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path | str) -> Checkpoint:
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            stage=data.get("__stage__", data.get("stage", "design")),
            idea=data["idea"],
            output_dir=data["output_dir"],
            design=data.get("design"),
            template=data.get("template"),
            assets=data.get("assets"),
            game_files=data.get("game_files"),
            qa_results=data.get("qa_results"),
            error=data.get("error"),
        )