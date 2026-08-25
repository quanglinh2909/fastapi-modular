"""Kiểm tra routing key, mẫu topic và kiểu exchange trước khi gửi sang RabbitMQ.

Chuỗi rác đi thẳng vào lệnh bind hoặc publish sẽ làm hỏng kênh AMQP, kéo theo
mọi thứ khác đang dùng chung kênh đó. Chặn sớm ở đây rẻ hơn nhiều.

Năm kiểu exchange của AMQP, khác nhau ở CÁCH CHỌN hàng đợi nhận tin:

    topic     so routing key với mẫu có * và #      "alert.*"
    direct    routing key phải TRÙNG KHÍT           "alert.created"
    fanout    không nhìn routing key, ai bind cũng nhận một bản
    headers   không nhìn routing key, so HEADER của tin
    default   exchange tên rỗng, có sẵn: route thẳng theo TÊN hàng đợi

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
from typing import Any, Literal

from fastapi_modular.core.exceptions import BadRequestError

MAX_PATTERN_LENGTH = 255

ExchangeKind = Literal["topic", "direct", "fanout", "headers", "default"]

EXCHANGE_KINDS: tuple[str, ...] = ("topic", "direct", "fanout", "headers", "default")

# Kiểu nào KHÔNG nhìn tới routing key. Khai routing key cho chúng thì hoặc
# bạn hiểu nhầm cách nó chọn hàng đợi, hoặc bạn chọn nhầm kiểu — cả hai đều
# đáng báo lỗi ngay lúc khởi động, thay vì để tin lặng lẽ đi sai chỗ.
_KHONG_DUNG_ROUTING_KEY = {
    "fanout": "fanout phát cho MỌI hàng đợi đã bind, không lọc gì cả",
    "headers": "headers lọc bằng `headers_match`, không phải routing key",
    "default": "exchange mặc định route theo đúng TÊN hàng đợi",
}

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


def normalize_binding(
    exchange: str,
    routing_key: str,
    *,
    kind: str | None = None,
    headers_match: dict[str, Any] | None = None,
    match: str = "all",
) -> tuple[str, str, dict[str, Any] | None]:
    """Chốt kiểu exchange, rồi soi routing key/header xem có hợp kiểu đó không.

    Trả về `(kiểu, routing_key đã chuẩn hoá, arguments cho lệnh bind)`.

    Vì sao phải chặn ở đây: RabbitMQ KHÔNG báo lỗi khi bạn bind sai kiểu. Đưa
    routing key `"alert.*"` cho một exchange fanout thì broker vui vẻ nhận rồi
    lờ nó đi — hàng đợi nhận MỌI tin, không phải tin bạn tưởng. Sai kiểu này
    chỉ lộ ra khi có người hỏi "sao consumer này nhận cả tin của module khác".
    """
    exchange = exchange.strip()
    kind = (kind or ("default" if exchange == "" else "topic")).strip().lower()
    if kind not in EXCHANGE_KINDS:
        raise BadRequestError(
            f"Kiểu exchange '{kind}' không có. Chọn một trong: {', '.join(EXCHANGE_KINDS)}."
        )

    # "default" và tên rỗng là MỘT thứ, không phải hai lựa chọn ghép được tuỳ ý.
    if kind == "default" and exchange != "":
        raise BadRequestError(
            f"exchange_type='default' là exchange tên rỗng có sẵn của AMQP, "
            f"nhưng bạn đưa tên '{exchange}'. Bỏ tên đi (exchange=\"\"), hoặc "
            "chọn kiểu khác cho exchange này."
        )
    if kind != "default" and exchange == "":
        raise BadRequestError(
            f"Exchange tên rỗng là exchange MẶC ĐỊNH của AMQP — nó có sẵn, không "
            f"khai báo lại được nên không thể là kiểu '{kind}'. Đặt tên cho "
            "exchange, hoặc dùng exchange_type='default'."
        )

    routing_key = routing_key.strip()
    ly_do = _KHONG_DUNG_ROUTING_KEY.get(kind)
    if ly_do and routing_key:
        raise BadRequestError(
            f"Exchange kiểu '{kind}' không dùng routing key, nhưng bạn đưa "
            f"'{routing_key}'. Bỏ nó đi: {ly_do}."
        )
    if kind != "headers" and headers_match:
        raise BadRequestError(
            f"`headers_match` chỉ có tác dụng với exchange kiểu 'headers', "
            f"không phải '{kind}'. Tin vẫn mang header đó, nhưng không ai lọc theo."
        )

    if kind == "headers":
        if not headers_match:
            raise BadRequestError(
                "Exchange kiểu 'headers' lọc bằng header, nên phải khai "
                "`headers_match={...}`. Không có nó thì binding này không khớp tin nào."
            )
        if match not in ("all", "any"):
            raise BadRequestError(f"`match` phải là 'all' hoặc 'any', không phải '{match}'.")
        if "x-match" in headers_match:
            raise BadRequestError(
                "'x-match' là khoá riêng của AMQP, đừng đặt trong `headers_match` — "
                "dùng tham số `match='all'|'any'`."
            )
        return kind, "", {"x-match": match, **headers_match}

    if kind == "topic":
        return kind, validate_pattern(routing_key), None
    if kind == "direct":
        return kind, validate_routing_key(routing_key), None
    return kind, "", None
