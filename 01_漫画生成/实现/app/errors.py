"""结构化错误类型 + 用户友好错误消息。

每个错误携带:
- 技术细节(供日志记录)
- 用户提示(供 CLI 展示)
- 可恢复性标记(是否值得重试)
"""
from __future__ import annotations


class ComicError(Exception):
    """漫画生成错误基类。"""

    def __init__(self, message: str, user_hint: str = "", recoverable: bool = False):
        super().__init__(message)
        self.user_hint = user_hint or message
        self.recoverable = recoverable


class QuotaExhaustedError(ComicError):
    """API 配额耗尽(429/402)。不可通过重试恢复,需换 key/模型。"""

    def __init__(self, message: str):
        super().__init__(
            message,
            user_hint=(
                "❌ API 配额已用尽\n"
                "   解决方法:\n"
                "   1. 检查账户余额并充值\n"
                "   2. 更换 .env 中的 API Key\n"
                "   3. 等待配额重置后再试"
            ),
            recoverable=False,
        )


class NetworkError(ComicError):
    """网络连接/超时错误。可能通过重试恢复。"""

    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(
            message,
            user_hint=(
                f"⚠️  网络连接失败: {message}\n"
                "   建议:\n"
                "   1. 检查网络连接\n"
                "   2. 确认 API 端点可访问\n"
                "   3. 稍后重试"
            ),
            recoverable=True,
        )
        self.original = original


class APIError(ComicError):
    """API 返回错误响应(4xx/5xx,非配额类)。"""

    def __init__(self, status_code: int, message: str, endpoint: str = ""):
        super().__init__(
            f"HTTP {status_code} at {endpoint}: {message}",
            user_hint=(
                f"❌ API 请求失败 (HTTP {status_code})\n"
                f"   端点: {endpoint}\n"
                f"   详情: {message[:100]}\n"
                "   建议检查 API 服务状态或配置"
            ),
            recoverable=status_code >= 500,  # 5xx 可能瞬时,4xx 通常配置问题
        )
        self.status_code = status_code


class ValidationError(ComicError):
    """配置验证/数据格式错误。不可恢复,需修正配置。"""

    def __init__(self, field: str, message: str):
        super().__init__(
            f"Validation failed for {field}: {message}",
            user_hint=(
                f"❌ 配置错误: {field}\n"
                f"   {message}\n"
                "   请检查 config.yaml 或命令行参数"
            ),
            recoverable=False,
        )


class ModelOutputError(ComicError):
    """模型输出格式错误(JSON 解析失败/字段缺失)。可能通过降温重试恢复。"""

    def __init__(self, stage: str, message: str, raw_output: str = ""):
        super().__init__(
            f"{stage} output parsing failed: {message}",
            user_hint=(
                f"⚠️  AI 输出格式异常 ({stage})\n"
                f"   {message}\n"
                "   已自动重试,如持续失败请联系开发者"
            ),
            recoverable=True,
        )
        self.raw_output = raw_output[:500]  # 保留前 500 字符供调试


class TimeoutError(ComicError):
    """操作超时。"""

    def __init__(self, operation: str, timeout_s: float):
        super().__init__(
            f"{operation} timed out after {timeout_s}s",
            user_hint=(
                f"⏱️  操作超时: {operation} (>{timeout_s}秒)\n"
                "   建议:\n"
                "   1. 检查网络连接稳定性\n"
                "   2. 尝试减少格数或简化故事\n"
                "   3. 联系 API 提供商检查服务状态"
            ),
            recoverable=True,
        )


class ImageQualityError(ComicError):
    """图片质检不通过(损坏/纯色/尺寸异常)。可通过重生成恢复。"""

    def __init__(self, panel_index: int, issues: str):
        super().__init__(
            f"Panel {panel_index} quality check failed: {issues}",
            user_hint=f"⚠️  第 {panel_index} 格图片质量不佳,已自动重试",
            recoverable=True,
        )
        self.panel_index = panel_index
