"""配置加载:.env(凭据/模型名)+ config.yaml(流程参数),env 优先。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class EnvSettings(BaseSettings):
    """凭据与模型名(只进 .env,不进文档)。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="COMIC_", extra="ignore"
    )

    base_url: str = "http://127.0.0.1:3000/v1"
    api_key: str = ""
    chat_model: str = "step-2-16k"
    image_model: str = "step-1-32k"
    # 视觉质检直连端点(不走 newapi;留空则复用 base_url)
    vision_base_url: str = ""
    vision_api_key: str = ""
    # 图生图编辑通道(StepFun 官方 step-image-edit-2,OpenAI 兼容 /images/edits)。
    # 视觉质检不过时"保形改图":以上一格成品为参考图,只修问题不推倒重来,
    # 角色/风格天然延续(与 02_游戏素材生成 同一套已验证通道)。
    # api_key 留空 = 不启用,视觉重生成退回原"重设计+文生图"路径
    edit_base_url: str = "https://api.stepfun.com/step_plan/v1"
    edit_api_key: str = ""
    edit_model: str = "step-image-edit-2"


class PanelsConfig(BaseModel):
    count: int = 4
    language: str = "zh"

    @field_validator('count')
    @classmethod
    def validate_count(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError("panels.count 必须在 1-20 之间")
        return v


class ImageConfig(BaseModel):
    size: str = "1024x1024"
    style_anchor: str = ""
    extra: str = ""
    fallback_models: list[str] = Field(default_factory=list)
    consistency: dict = Field(default_factory=dict)

    @field_validator('size')
    @classmethod
    def validate_size(cls, v: str) -> str:
        try:
            w, _, h = v.lower().partition("x")
            width, height = int(w), int(h)
            if width < 256 or height < 256 or width > 2048 or height > 2048:
                raise ValueError("尺寸必须在 256-2048 之间")
            return v
        except (ValueError, AttributeError) as e:
            raise ValueError(f"image.size 格式无效 (应为 'WIDTHxHEIGHT'): {v}") from e


class CharactersConfig(BaseModel):
    lock: bool = True


class ConcurrencyConfig(BaseModel):
    design: int = 4
    image: int = 2
    retries: int = 2

    @field_validator('design', 'image')
    @classmethod
    def validate_concurrency(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("并发数必须在 1-10 之间")
        return v

    @field_validator('retries')
    @classmethod
    def validate_retries(cls, v: int) -> int:
        if v < 0 or v > 5:
            raise ValueError("重试次数必须在 0-5 之间")
        return v


class AssembleConfig(BaseModel):
    caption_height: int = 64
    gap: int = 12
    background: str = "#ffffff"
    format: str = "JPEG"
    quality: int = 90

    @field_validator('format')
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v.upper() not in ("JPEG", "PNG"):
            raise ValueError("assemble.format 必须是 JPEG 或 PNG")
        return v.upper()

    @field_validator('quality')
    @classmethod
    def validate_quality(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("assemble.quality 必须在 1-100 之间")
        return v


class PolishConfig(BaseModel):
    enabled: bool = True
    max_chars: int = 24


class CriticConfig(BaseModel):
    prompt_qa: bool = True
    image_qa: bool = True
    vision_model: str = ""


class OutputConfig(BaseModel):
    dir: str = "out"
    comic_name: str = "comic.png"


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
    """加载配置,带验证。"""
    # 加载 .env
    try:
        env = EnvSettings()
    except Exception as e:
        raise ValidationError(".env", f"环境变量加载失败: {e}")
    
    if not env.api_key:
        raise ValidationError("COMIC_API_KEY", ".env 中缺少 API Key")
    
    # 加载 config.yaml
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ValidationError(str(path), f"配置文件不存在: {path}")
    
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValidationError(str(path), f"YAML 解析失败: {e}")
    
    try:
        config = AppConfig(**raw)
    except Exception as e:
        raise ValidationError("config.yaml", f"配置验证失败: {e}")
    
    return env, config


def parse_size(size: str) -> tuple[int, int]:
    """'1024x1024' → (1024, 1024)。"""
    w, _, h = size.lower().partition("x")
    return int(w), int(h)
