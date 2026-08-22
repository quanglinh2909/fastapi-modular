"""Khuôn tin nhắn và mã đóng kết nối.

Một kết nối WebSocket là một ống byte trần: không có URL, không có status code,
không có content-type. Muốn nhiều loại nghiệp vụ đi chung một kết nối thì phải
tự quy ước, và quy ước đó chính là "giao thức" dưới đây — tương đương cặp
`event` + `data` của socket.io mà NestJS dùng.

Mọi khung tin là JSON một tầng:

    {"event": "room.join", "data": {"room": "alerts"}, "id": "c1"}

- `event` : tên nghiệp vụ, quyết định handler nào chạy. Bắt buộc.
- `data`  : tham số, tuỳ handler. Thiếu thì hiểu là `null`.
- `id`    : mã do CLIENT tự đặt để ghép câu trả lời với câu hỏi. Tuỳ chọn.

Server trả lời bằng cùng khuôn, thêm `ack` = `id` của khung hỏi:

    {"event": "room.join", "data": {"room": "alerts", "size": 3}, "ack": "c1"}
    {"event": "error", "data": {"code": "forbidden", "message": "..."}, "ack": "c1"}

Server đẩy chủ động thì không có `ack`:

    {"event": "alert.created", "data": {...}}

Khung có thêm ô `meta` tuỳ chọn, nội dung do bên gửi tự quyết. Lõi không đọc
tới nó; nó dành cho các lớp bên ngoài muốn kèm nguồn gốc của tin, ví dụ:

    {"event": "alert.created", "data": {...}, "meta": {"nguon": "..."}}

Vì sao cần `id`/`ack`: WebSocket không ghép cặp request-response như HTTP. Gửi
ba lệnh liên tiếp rồi nhận ba câu trả lời thì không có cách nào biết cái nào
của cái nào, trừ khi tự đánh số.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from pymodular.core.exceptions import AppError


class CloseCode(IntEnum):
    """Mã đóng kết nối.

    1000–2999 do chuẩn WebSocket định nghĩa; 4000–4999 dành cho ứng dụng tự
    đặt. Ở đây dải 44xx cố ý ánh xạ 1-1 với HTTP status quen thuộc (4401 ~ 401,
    4403 ~ 403...) để đọc log không phải tra bảng.
    """

    NORMAL = 1000
    GOING_AWAY = 1001            # server tắt/khởi động lại
    PROTOCOL_ERROR = 1002
    TOO_BIG = 1009               # khung tin vượt ngưỡng
    TRY_AGAIN_LATER = 1013       # client đọc quá chậm, hàng đợi gửi đã đầy

    BAD_REQUEST = 4400
    UNAUTHORIZED = 4401
    FORBIDDEN = 4403
    NOT_FOUND = 4404
    IDLE_TIMEOUT = 4408          # im lặng quá lâu, coi như đã chết
    CONFLICT = 4409
    TOO_MANY = 4429              # vượt hạn mức kết nối hoặc tần suất gửi
    INTERNAL = 4500


# AppError mang status_code HTTP; đổi sang mã đóng tương ứng để client dùng
# chung một bảng lỗi cho cả REST lẫn WebSocket.
_HTTP_TO_CLOSE: dict[int, CloseCode] = {
    400: CloseCode.BAD_REQUEST,
    401: CloseCode.UNAUTHORIZED,
    403: CloseCode.FORBIDDEN,
    404: CloseCode.NOT_FOUND,
    409: CloseCode.CONFLICT,
    422: CloseCode.BAD_REQUEST,
    429: CloseCode.TOO_MANY,
}


def close_code_for(exc: BaseException) -> CloseCode:
    if isinstance(exc, AppError):
        return _HTTP_TO_CLOSE.get(exc.status_code, CloseCode.INTERNAL)
    return CloseCode.INTERNAL


# Tên sự kiện do framework giữ chỗ; gateway không được đăng ký trùng.
EVENT_CONNECTED = "connected"
EVENT_ERROR = "error"
EVENT_PING = "ping"
EVENT_PONG = "pong"
EVENT_ROOM_JOIN = "room.join"
EVENT_ROOM_LEAVE = "room.leave"

RESERVED_EVENTS = frozenset(
    {EVENT_CONNECTED, EVENT_ERROR, EVENT_PONG, EVENT_ROOM_JOIN, EVENT_ROOM_LEAVE}
)


class ProtocolError(AppError):
    """Khung tin sai khuôn — không phải JSON, thiếu `event`, hoặc quá dài."""

    status_code = 400
    error_code = "ws_protocol_error"
    message = "Khung tin không hợp lệ"


@dataclass(slots=True)
class Frame:
    event: str
    data: Any = None
    id: str | None = None
    ack: str | None = None
    meta: dict[str, Any] | None = None

    def to_json(self) -> str:
        payload: dict[str, Any] = {"event": self.event, "data": self.data}
        if self.ack is not None:
            payload["ack"] = self.ack
        if self.meta is not None:
            payload["meta"] = self.meta
        # default=str để datetime/UUID/Decimal trong data không làm chết vòng
        # gửi. Mất kiểu còn hơn mất kết nối.
        return json.dumps(payload, ensure_ascii=False, default=str)


def parse_frame(raw: str) -> Frame:
    """Đọc khung tin từ client. Ném ProtocolError nếu sai khuôn."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"Không phải JSON hợp lệ: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("Khung tin phải là một object JSON")

    event = payload.get("event")
    if not isinstance(event, str) or not event:
        raise ProtocolError("Thiếu trường 'event'")

    frame_id = payload.get("id")
    if frame_id is not None and not isinstance(frame_id, str):
        raise ProtocolError("Trường 'id' phải là chuỗi")

    return Frame(event=event, data=payload.get("data"), id=frame_id)


def error_frame(exc: BaseException, *, ack: str | None = None, debug: bool = False) -> Frame:
    """Đổi exception thành khung `error` cùng hình dạng với lỗi HTTP."""
    if isinstance(exc, AppError):
        return Frame(event=EVENT_ERROR, data=exc.to_dict(), ack=ack)

    data: dict[str, Any] = {"code": "internal_error", "message": "Internal server error"}
    if debug:
        data["details"] = f"{type(exc).__name__}: {exc}"
    return Frame(event=EVENT_ERROR, data=data, ack=ack)
