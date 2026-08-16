"""配置加载:.env(凭据/模型名)+ config.yaml(流程参数),env 优先。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class EnvSettings(BaseSettings):
    """凭据与模型名(只进 .env,不进文档)。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="COMIC_", extra="ignore"
    )

    base_url: str = "http://127.0.0.1:3000/v1"
    api_key: str = ""
    chat_model: str = "grok-chat-fast"
    image_model: str = "grok-imagine-image-quality-lite"


class PanelsConfig(BaseModel):
    count: int = 4
    language: str = "zh"


class ImageConfig(BaseModel):
    size: str = "1024x1024"
    style_anchor: str = ""
    extra: str = ""


class CharactersConfig(BaseModel):
    lock: bool = True


class ConcurrencyConfig(BaseModel):
    design: int = 4
    image: int = 2
    retries: int = 2


class AssembleConfig(BaseModel):
    caption_height: int = 64
    gap: int = 12
    background: str = "#ffffff"
    format: str = "JPEG"       # JPEG(体积约 1/5)| PNG(无损)
    quality: int = 90          # JPEG 质量


class PolishConfig(BaseModel):
    enabled: bool = True       # 台词优化:每格产出简洁漫画式字幕
    max_chars: int = 24


class CriticConfig(BaseModel):
    prompt_qa: bool = True     # 生成前:提示词 vs 分镜质检,不过带反馈重设计
    image_qa: bool = True      # 生成后:启发式质检(损坏/尺寸/空白),不过自动重生成
    vision_model: str = ""     # 视觉评分模型名(端点支持时填;grok-chat-fast 看不了图)


class OutputConfig(BaseModel):
    dir: str = "out"
    comic_name: str = "comic.png"   # 后缀按 assemble.format 自动纠正


class AppConfig(BaseModel):
    panels: PanelsConfig = PanelsConfig()
    image: ImageConfig = ImageConfig()
    characters: CharactersConfig = CharactersConfig()
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    assemble: AssembleConfig = AssembleConfig()
    polish: PolishConfig = PolishConfig()
    critic: CriticConfig = CriticConfig()
    output: OutputConfig = OutputConfig()


def load_app_config(config_path: str | None = None) -> tuple[EnvSettings, AppConfig]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return EnvSettings(), AppConfig(**(raw or {}))


def parse_size(size: str) -> tuple[int, int]:
    """'1024x1024' → (1024, 1024)。"""
    w, _, h = size.lower().partition("x")
    return int(w), int(h)
