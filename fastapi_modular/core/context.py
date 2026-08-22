"""Context của request, truyền ngầm qua contextvars.

Nhờ vậy logger/repository ở tầng sâu vẫn lấy được ``request_id`` mà không phải
truyền tham số xuyên suốt các lớp.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


# ------------------------------------------------------------------- trace id
#
# `request_id` là của riêng dịch vụ này; `trace_id` đi xuyên qua mọi dịch vụ
# trong một hành trình, theo chuẩn W3C Trace Context. Header `traceparent` có
# dạng:  00-<trace_id 32 hex>-<span_id 16 hex>-<cờ 2 hex>


def new_trace_id() -> str:
    return uuid.uuid4().hex          # 32 ký tự hex, đúng khuôn W3C


def parse_traceparent(header: str | None) -> str | None:
    """Lấy trace_id từ header traceparent; trả None nếu header sai khuôn."""
    if not header:
        return None
    parts = header.split("-")
    if len(parts) < 4 or len(parts[1]) != 32:
        return None
    trace_id = parts[1]
    if not all(c in "0123456789abcdef" for c in trace_id) or trace_id == "0" * 32:
        return None
    return trace_id


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(value: str) -> Token[str | None]:
    return _trace_id.set(value)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_user_id() -> str | None:
    return _user_id.get()


def set_user_id(value: str | None) -> Token[str | None]:
    return _user_id.set(value)


def reset_user_id(token: Token[str | None]) -> None:
    _user_id.reset(token)
