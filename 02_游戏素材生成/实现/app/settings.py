"""配置加载:.env(凭据/模型名)+ config.yaml(流程参数),env 优先。"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class EnvSettings(BaseSettings):
    """凭据与模型名(只进 .env,不进文档)。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="ASSET_", extra="ignore"
    )

    base_url: str = "http://127.0.0.1:3000/v1"
    api_key: str = ""
    chat_model: str = "grok-chat-fast"
    image_model: str = "grok-imagine-image-quality-lite"
    vision_model: str = ""  # 空 = 视觉审核自动降级跳过(当前端点无视觉模型)
    # 视觉模型可挂在不同的 OpenAI 兼容服务商(如智谱/阿里/OpenRouter),
    # 留空则与主端点共用 base_url/api_key
    vision_base_url: str = ""
    vision_api_key: str = ""
    # 图生图编辑通道(指令式改图,StepFun 官方 OpenAI 兼容 /images/edits)。
    # 用于动画帧条件生成(锚帧+改姿势,压帧间漂移)与带反馈重生成的"保形改图"。
    # api_key 留空 = 不启用,动画退回逐帧文生图,行为与未接入时一致
    edit_base_url: str = "https://api.stepfun.com/v1"
    edit_api_key: str = ""
    edit_model: str = "step-image-edit-2"
    # 出网代理(如 http://127.0.0.1:7890)。留空 = 永远直连:
    # 客户端 trust_env=False,无视环境变量与 Windows 注册表系统代理
    # (注册表残留的坏代理曾导致 MCP 子进程 SSL 校验失败)
    http_proxy: str = ""


class ImageConfig(BaseModel):
    size: str = "1024x1024"
    retries: int = 2


class ReviewConfig(BaseModel):
    max_rounds: int = 2
    min_score: int = 8


class CutoutConfig(BaseModel):
    engine: str = "auto"  # auto | rembg | white_key
    white_tolerance: int = 18
    feather: int = 2
    padding_ratio: float = 0.06


class DecomposeConfig(BaseModel):
    temperature: float = 0.3


class StyleConfig(BaseModel):
    anchor: str = ""       # 全局风格锚:所有任务公共前缀,保证跨任务风格一致
    lib_dir: str = "lib"   # 角色卡/风格库目录(相对项目根)


class OutputConfig(BaseModel):
    dir: str = "out"
    max_sizes: int = 6     # 单次任务最多输出尺寸数(防滥用)
    size_bounds: tuple[int, int] = (16, 2048)


class AppConfig(BaseModel):
    image: ImageConfig = ImageConfig()
    review: ReviewConfig = ReviewConfig()
    cutout: CutoutConfig = CutoutConfig()
    decompose: DecomposeConfig = DecomposeConfig()
    style: StyleConfig = StyleConfig()
    output: OutputConfig = OutputConfig()


def load_app_config(config_path: str | None = None) -> tuple[EnvSettings, AppConfig]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return EnvSettings(), AppConfig(**(raw or {}))


def parse_size(size: str) -> tuple[int, int]:
    """'1024x1024' → (1024, 1024)。"""
    w, _, h = size.lower().partition("x")
    return int(w), int(h)


def normalize_sizes(sizes: list[int] | None, cfg: AppConfig) -> list[int]:
    """校验/去重/降序排列输出尺寸,非法值剔除,空则用默认 512/256/128。"""
    if not sizes:
        return [512, 256, 128]
    lo, hi = cfg.output.size_bounds
    valid = sorted({s for s in sizes if isinstance(s, int) and lo <= s <= hi}, reverse=True)
    if not valid:
        raise ValueError(f"no valid sizes in {sizes!r} (bounds {lo}-{hi})")
    return valid[: cfg.output.max_sizes]
