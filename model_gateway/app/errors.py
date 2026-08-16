"""自定义异常族:每个异常对应一个 HTTP 状态码与稳定错误码。"""


class GatewayError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BadRequestError(GatewayError):
    status_code = 400
    code = "bad_request"


class NoModelSpecified(GatewayError):
    """既没有 model 也没有 capability。"""
    status_code = 400
    code = "no_model_or_capability"


class ModelNotFound(GatewayError):
    status_code = 404
    code = "model_not_found"


class NoModelForCapability(GatewayError):
    status_code = 400
    code = "no_model_for_capability"


class ProviderDisabled(GatewayError):
    status_code = 503
    code = "provider_disabled"


class QuotaExceeded(GatewayError):
    status_code = 413
    code = "quota_exceeded"


class RateLimitedError(GatewayError):
    status_code = 429
    code = "rate_limited"


class UpstreamTimeout(GatewayError):
    status_code = 504
    code = "upstream_timeout"


class ClientTotalTimeout(GatewayError):
    status_code = 408
    code = "client_timeout"


class UpstreamError(GatewayError):
    status_code = 502
    code = "upstream_error"
