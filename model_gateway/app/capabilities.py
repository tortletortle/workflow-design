"""能力表 + 评分选型:capability → 在启用模型中按对应维度排序取最优。"""
from __future__ import annotations

from app.settings import ModelConfig

# 每个 capability 的匹配规则描述(GET /v1/capabilities 直接返回这份表)
CAPABILITY_RULES: dict[str, str] = {
    "low_latency": "在启用模型中取 latency_tier 最小者;平手取 cost_per_1k 更小者",
    "low_cost": "在启用模型中取 cost_per_1k 最小者;平手取 latency_tier 更小者",
    "high_quality": "在启用模型中取 quality_rank 最大者;平手取 latency_tier 更小者",
    "long_context": "在启用模型中取 max_context 最大者;平手取 cost_per_1k 更小者",
    "vision": "在声明 vision 能力的启用模型中取 quality_rank 最大者",
}

# 排序键:(主维度, 次维度),均为"越小越优"
_SORT_KEYS: dict[str, tuple] = {
    "low_latency": (lambda m: m.latency_tier, lambda m: m.cost_per_1k),
    "low_cost": (lambda m: m.cost_per_1k, lambda m: m.latency_tier),
    "high_quality": (lambda m: -m.quality_rank, lambda m: m.latency_tier),
    "long_context": (lambda m: -m.max_context, lambda m: m.cost_per_1k),
    "vision": (lambda m: -m.quality_rank, lambda m: m.latency_tier),
}


def rank_models(models: list[ModelConfig], capability: str) -> list[ModelConfig]:
    """按 capability 对应的评分维度排序(最优在前);不匹配的模型已被调用方过滤。"""
    primary, secondary = _SORT_KEYS[capability]
    return sorted(models, key=lambda m: (primary(m), secondary(m)))
