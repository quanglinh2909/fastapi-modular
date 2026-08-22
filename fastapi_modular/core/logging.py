"""Cấu hình structlog + stdlib logging thành một đường ống duy nhất."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from fastapi_modular.core.config import LogSettings
from fastapi_modular.core.context import get_request_id, get_trace_id, get_user_id


def _inject_context(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Gắn request_id / trace_id / user_id từ contextvars vào mọi dòng log."""
    request_id = get_request_id()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    trace_id = get_trace_id()
    if trace_id:
        # trace_id nối log của dịch vụ này với log của các dịch vụ khác trong
        # cùng một hành trình; request_id chỉ có ý nghĩa trong dịch vụ này.
        event_dict.setdefault("trace_id", trace_id)
    user_id = get_user_id()
    if user_id:
        event_dict.setdefault("user_id", user_id)
    return event_dict


def configure_logging(settings: LogSettings) -> None:
    level = getattr(logging, settings.level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.json_format
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Đưa log của thư viện bên thứ ba (uvicorn, sqlalchemy, ...) qua structlog.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers = []
        lg.propagate = True

    # uvicorn.access bị tắt vì middleware AccessLog đã ghi log request đầy đủ hơn.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
