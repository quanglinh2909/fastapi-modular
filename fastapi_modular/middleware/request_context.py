"""Middleware gán request-id và ghi access log.

Cố ý viết dưới dạng **ASGI middleware thuần**, không dùng
`starlette.middleware.base.BaseHTTPMiddleware`. Lý do không chỉ là hiệu năng:

`BaseHTTPMiddleware` chạy ứng dụng bên trong một task group riêng và trả về từ
`call_next` ngay khi response *bắt đầu* stream. Hệ quả là phần dọn dẹp của
dependency dạng `yield` — nơi session database COMMIT — có thể chạy **sau** khi
client đã nhận response. Client ghi xong, đọc lại ngay thì thấy dữ liệu cũ.

Với ASGI thuần, ta chỉ quan sát message đi qua; thứ tự "chạy xong handler ->
commit -> gửi response" của Starlette được giữ nguyên.
"""

from __future__ import annotations

import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_modular.core.context import (
    new_request_id,
    new_trace_id,
    parse_traceparent,
    reset_request_id,
    reset_trace_id,
    set_request_id,
    set_trace_id,
)
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.metrics import http_duration, http_in_flight, http_requests

log = get_logger("http")

REQUEST_ID_HEADER = "x-request-id"
RESPONSE_TIME_HEADER = "x-response-time-ms"
TRACEPARENT_HEADER = "traceparent"
TRACE_ID_HEADER = "x-trace-id"


class RequestContextMiddleware:
    """Lấy request-id từ header nếu có (trace xuyên service), không thì sinh mới."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(REQUEST_ID_HEADER) or new_request_id()

        # trace_id nối các dịch vụ lại với nhau: nếu bên gọi đã có traceparent
        # thì dùng lại, chưa có thì sinh mới và truyền tiếp cho bên dưới.
        trace_id = parse_traceparent(headers.get(TRACEPARENT_HEADER)) or new_trace_id()

        request_token = set_request_id(request_id)
        trace_token = set_trace_id(trace_id)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["trace_id"] = trace_id

        async def send_with_ids(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.append(REQUEST_ID_HEADER, request_id)
                response_headers.append(TRACE_ID_HEADER, trace_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_ids)
        finally:
            reset_trace_id(trace_token)
            reset_request_id(request_token)


UNMATCHED_ROUTE = "unmatched"


def route_template(scope: Scope) -> str:
    """Khuôn đường dẫn ĐẦY ĐỦ của route, ví dụ /api/users/{user_id}.

    Bắt buộc dùng khuôn chứ không phải đường dẫn thật khi làm nhãn số đo:
    /api/users/abc và /api/users/def là hai nhãn khác nhau, mỗi bản ghi tạo một
    chuỗi số đo mới và làm nổ bộ nhớ Prometheus.

    `route.path_format` chỉ là đường dẫn TRONG router con ("/users/{user_id}"),
    mất tiền tố "/api" do include_router thêm vào. Ghép lại bằng cách lấy phần
    đầu của đường dẫn thật: số đoạn dư ra chính là tiền tố.
    """
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    if not template:
        return UNMATCHED_ROUTE

    template_parts = [part for part in template.split("/") if part]
    actual_parts = [part for part in scope.get("path", "").split("/") if part]
    if len(actual_parts) < len(template_parts):
        return template

    prefix = actual_parts[: len(actual_parts) - len(template_parts)]
    parts = [*prefix, *template_parts]
    return "/" + "/".join(parts) if parts else "/"


class AccessLogMiddleware:
    """Ghi access log VÀ cập nhật số đo cho mỗi request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        http_in_flight.inc_gauge(1)

        async def send_with_timing(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                MutableHeaders(scope=message).append(RESPONSE_TIME_HEADER, str(duration_ms))
            await send(message)

        client = scope.get("client")
        try:
            await self.app(scope, receive, send_with_timing)
        except Exception:
            log.exception(
                "http.request_failed",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            self._record(scope, started, 500)
            raise
        finally:
            http_in_flight.inc_gauge(-1)

        self._record(scope, started, status_code)
        log.info(
            "http.request",
            method=scope.get("method"),
            path=scope.get("path"),
            status_code=status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            client=client[0] if client else None,
        )

    @staticmethod
    def _record(scope: Scope, started: float, status_code: int) -> None:
        method = scope.get("method", "?")
        template = route_template(scope)
        http_requests.inc(method=method, path=template, status=status_code)
        http_duration.observe(
            time.perf_counter() - started, method=method, path=template
        )
