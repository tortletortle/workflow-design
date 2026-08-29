"""结构化日志系统 - 使用标准库 logging，无需额外依赖。

支持:
- JSON 格式日志（便于解析和检索）
- 上下文字段（stage、panel、attempt 等）
- 日志级别控制（DEBUG/INFO/WARNING/ERROR）
- 环境变量配置
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON 格式化器，将日志记录转换为 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # 添加额外字段（通过 extra= 传递）
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """文本格式化器，便于人类阅读。"""

    def format(self, record: logging.LogRecord) -> str:
        # 基础信息
        base = f"[{record.levelname}] {record.name}: {record.getMessage()}"

        # 添加额外字段
        if hasattr(record, "extra_fields"):
            extras = " | ".join(f"{k}={v}" for k, v in record.extra_fields.items())
            base += f" ({extras})"

        return base


def setup_logger(
    name: str = "comic",
    level: str | None = None,
    format: str | None = None,
) -> logging.Logger:
    """设置日志记录器。

    Args:
        name: 日志记录器名称
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)，默认从环境变量读取
        format: 日志格式 (json/text)，默认从环境变量读取

    Returns:
        配置好的 Logger 实例

    环境变量:
        COMIC_LOG_LEVEL: 日志级别，默认 INFO
        COMIC_LOG_FORMAT: 日志格式，默认 text
    """
    import os

    # 从环境变量读取配置
    level = level or os.getenv("COMIC_LOG_LEVEL", "INFO").upper()
    format_type = format or os.getenv("COMIC_LOG_FORMAT", "text").lower()

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    # 选择格式化器
    if format_type == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter()

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


class LoggerAdapter(logging.LoggerAdapter):
    """日志适配器，支持附加上下文字段。"""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        # 将 extra 字典合并到 extra_fields
        extra = kwargs.get("extra", {})
        if extra:
            kwargs["extra"] = {"extra_fields": extra}
        return msg, kwargs


def get_logger(name: str = "comic") -> LoggerAdapter:
    """获取日志记录器（带适配器）。

    Example:
        >>> logger = get_logger("comic.pipeline")
        >>> logger.info("stage_start", extra={"stage": "splitter", "count": 4})
        >>> logger.error("api_failed", extra={"endpoint": "chat", "status": 500})
    """
    base_logger = logging.getLogger(name)
    return LoggerAdapter(base_logger, {})


# 全局默认 logger
logger = get_logger("comic")
