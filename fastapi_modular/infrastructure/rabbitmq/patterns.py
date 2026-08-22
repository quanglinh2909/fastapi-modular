"""Kiểm tra routing key và mẫu topic trước khi gửi sang RabbitMQ.

Chuỗi rác đi thẳng vào lệnh bind hoặc publish sẽ làm hỏng kênh AMQP, kéo theo
mọi thứ khác đang dùng chung kênh đó. Chặn sớm ở đây rẻ hơn nhiều.

Luật topic của AMQP:

    routing key : các "từ" ngăn bởi dấu chấm — "alert.created.hanoi"
    *           : khớp ĐÚNG MỘT từ
    #           : khớp KHÔNG hoặc NHIỀU từ

    alert.*        khớp alert.created,  không khớp alert.created.hanoi
    alert.#        khớp alert, alert.created, alert.created.hanoi
    *.created.*    khớp alert.created.hanoi
    #              khớp mọi thứ
"""

from __future__ import annotations

import re

from fastapi_modular.core.exceptions import BadRequestError

MAX_PATTERN_LENGTH = 255

# Ký tự hợp lệ trong một từ của routing key. Cố ý hẹp: tên có dấu cách hay
# dấu ngoặc sẽ khiến việc đọc log và đặt binding thành cực hình.
_WORD = re.compile(r"[A-Za-z0-9_\-]+")


def validate_pattern(pattern: str) -> str:
    """Kiểm tra mẫu do CLIENT gửi lên. Ném BadRequestError nếu sai khuôn.

    Mẫu đi thẳng vào lệnh bind của RabbitMQ nên không được nhận bừa: chuỗi rác
    sẽ làm hỏng kênh AMQP, kéo theo mọi kết nối khác đang dùng chung kênh đó.
    """
    pattern = pattern.strip()
    if not pattern:
        raise BadRequestError("Mẫu routing key không được để trống")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise BadRequestError(f"Mẫu dài quá {MAX_PATTERN_LENGTH} ký tự")

    for word in pattern.split("."):
        if word in ("*", "#"):
            continue
        if not _WORD.fullmatch(word):
            raise BadRequestError(
                f"Từ '{word}' không hợp lệ trong mẫu '{pattern}'. "
                "Chỉ dùng chữ, số, _ và -, ngăn nhau bởi dấu chấm; * cho một từ, # cho nhiều từ."
            )
    return pattern


def validate_routing_key(routing_key: str) -> str:
    """Như trên nhưng cho routing key thật: không được chứa * hay #."""
    routing_key = routing_key.strip()
    if not routing_key:
        raise BadRequestError("Routing key không được để trống")
    if len(routing_key) > MAX_PATTERN_LENGTH:
        raise BadRequestError(f"Routing key dài quá {MAX_PATTERN_LENGTH} ký tự")
    for word in routing_key.split("."):
        if not _WORD.fullmatch(word):
            raise BadRequestError(f"Từ '{word}' không hợp lệ trong routing key '{routing_key}'")
    return routing_key
