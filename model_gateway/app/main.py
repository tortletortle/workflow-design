"""FastAPI 装配:路由、异常 handler、Swagger 元信息。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.capabilities import CAPABILITY_RULES
from app.errors import GatewayError
from app.router import Gateway
from app.schemas import ChatRequest
from app.settings import Settings, load_config

DESCRIPTION = """
轻量 LLM 网关:对外一个 OpenAI 兼容接口,内部自动分发。

- **显式路由**:请求带 `model` → 直接路由到该模型
- **智能路由**:不带 `model` 但带 `capability`(low_cost / low_latency / high_quality / long_context / vision)→ 按能力表选最优模型
- **双模式**:默认 Mock(网络无关、错误可控);配置 API key 后 `GW_MODE=real` 切真实 provider
- 响应的 `metadata` 字段附带实际路由结果(routed_model / routed_provider / mode / latency_ms / request_id)
"""


def _error_body(exc: GatewayError) -> dict:
    return {
        "error": {
            "message": exc.message,
            "type": "gateway_error",
            "code": exc.code,
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    gateway = Gateway(load_config(settings))

    app = FastAPI(
        title="model-gateway",
        version="0.1.0",
        description=DESCRIPTION,
    )
    app.state.gateway = gateway

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"invalid request body: {exc.errors()[0].get('msg', 'validation failed')}",
                    "type": "gateway_error",
                    "code": "bad_request",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"internal error: {exc}",
                    "type": "gateway_error",
                    "code": "internal_error",
                }
            },
        )

    @app.post("/v1/chat/completions", summary="聊天补全(显式 model 或智能 capability 路由)")
    async def chat_completions(body: ChatRequest) -> dict:
        return await gateway.handle_chat(body)

    @app.get("/v1/models", summary="列出全部模型 + 能力标签 + 上限与启用状态")
    async def list_models() -> dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": m.name,
                    "object": "model",
                    "owned_by": m.provider,
                    "enabled": gateway.provider_enabled(m.provider),
                    "capabilities": m.capabilities,
                    "max_context": m.max_context,
                    "max_output": m.max_output,
                    "cost_per_1k": m.cost_per_1k,
                    "latency_tier": m.latency_tier,
                    "quality_rank": m.quality_rank,
                }
                for m in gateway.config.models
            ],
        }

    @app.get("/v1/capabilities", summary="列出可用 capability 取值及匹配规则")
    async def list_capabilities() -> dict:
        return {"capabilities": CAPABILITY_RULES}

    @app.get("/health", summary="健康检查(mode + 各 provider 启用状态)")
    async def health() -> dict:
        return {
            "status": "ok",
            "mode": gateway.config.mode,
            "providers": {
                name: gateway.provider_enabled(name)
                for name in gateway.config.providers
            },
        }

    return app


app = create_app()
