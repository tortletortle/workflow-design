"""配置加载:.env(手动注入 os.environ)+ config.yaml + 环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class Settings(BaseSettings):
    """环境变量层(GW_ 前缀),用于覆盖 config.yaml 的对应项。"""

    model_config = SettingsConfigDict(env_prefix="GW_", extra="ignore")

    mode: Literal["mock", "real"] | None = None
    config_path: str = str(DEFAULT_CONFIG_PATH)
    max_concurrency: int | None = None
    request_timeout: float | None = None
    upstream_timeout: float | None = None
    idempotency_ttl: int | None = None


class ProviderConfig(BaseModel):
    base_url: str
    api_key_env: str | None = None
    proxy_env: str | None = None


class ModelConfig(BaseModel):
    name: str
    provider: str
    capabilities: list[str] = Field(default_factory=list)
    max_context: int = 8192
    max_output: int = 4096
    cost_per_1k: float = 0.0
    latency_tier: int = 2
    quality_rank: int = 5
    behavior: str = "ok"
    delay: float = 0.0


class LimitsConfig(BaseModel):
    max_concurrency: int = 8
    request_timeout: float = 60.0
    upstream_timeout: float = 15.0


class GatewayConfig(BaseModel):
    mode: Literal["mock", "real"] = "mock"
    providers: dict[str, ProviderConfig]
    models: list[ModelConfig]
    limits: LimitsConfig = LimitsConfig()
    idempotency_ttl: int = 300


def _load_dotenv() -> None:
    """把 .env 里的 KEY=VALUE 注入 os.environ(已存在的变量不覆盖)。

    pydantic-settings 不会把 env_file 导出进环境,而 provider 的
    api_key_env / proxy_env 查的是真实环境变量,所以这里手动加载一次。
    """
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(settings: Settings | None = None) -> GatewayConfig:
    settings = settings or Settings()
    _load_dotenv()
    raw = yaml.safe_load(Path(settings.config_path).read_text(encoding="utf-8"))
    # 环境变量覆盖 yaml
    if settings.mode is not None:
        raw["mode"] = settings.mode
    limits = raw.setdefault("limits", {})
    if settings.max_concurrency is not None:
        limits["max_concurrency"] = settings.max_concurrency
    if settings.request_timeout is not None:
        limits["request_timeout"] = settings.request_timeout
    if settings.upstream_timeout is not None:
        limits["upstream_timeout"] = settings.upstream_timeout
    if settings.idempotency_ttl is not None:
        raw["idempotency_ttl"] = settings.idempotency_ttl
    return GatewayConfig(**raw)
