"""Provider 统一接口:路由层只依赖这里的抽象,不感知具体厂商。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.schemas import Message
from app.settings import ModelConfig


@dataclass
class ProviderResult:
    content: str
    model: str
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0


class BaseProvider(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def call(
        self,
        model: ModelConfig,
        messages: list[Message],
        max_tokens: int | None,
        temperature: float | None,
    ) -> ProviderResult:
        """调用模型;失败时抛 app.errors 中的 GatewayError 子类。"""

    async def aclose(self) -> None:
        """释放底层连接等资源;默认无操作。"""
