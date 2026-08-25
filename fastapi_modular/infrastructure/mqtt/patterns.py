"""Khớp topic MQTT: `+` một tầng, `#` mọi tầng còn lại.

Khác hẳn routing key của AMQP dù nhìn na ná:

    AMQP    "alert.*"     * = đúng một từ,   # = nhiều từ,  ngăn bằng dấu chấm
    MQTT    "alert/+"     + = đúng một tầng, # = nhiều tầng, ngăn bằng dấu /

`#` phải là ký tự CUỐI và chiếm trọn một tầng: "nha/#" hợp lệ, "nha/#/den" và
"nha#" thì không. Broker sẽ từ chối, nhưng nó từ chối lúc chạy còn hàm dưới đây
từ chối ngay lúc khai báo decorator.
"""

from __future__ import annotations

from fastapi_modular.core.exceptions import BadRequestError


def validate_topic_filter(topic_filter: str) -> None:
    if not topic_filter:
        raise BadRequestError("Topic MQTT không được rỗng")
    levels = topic_filter.split("/")
    for i, level in enumerate(levels):
        if "#" in level:
            if level != "#":
                raise BadRequestError(
                    f"Topic {topic_filter!r} sai: '#' phải chiếm trọn một tầng (vd 'nha/#')"
                )
            if i != len(levels) - 1:
                raise BadRequestError(
                    f"Topic {topic_filter!r} sai: '#' phải nằm ở cuối cùng"
                )
        elif "+" in level and level != "+":
            raise BadRequestError(
                f"Topic {topic_filter!r} sai: '+' phải chiếm trọn một tầng (vd 'nha/+/den')"
            )


def validate_topic(topic: str) -> None:
    """Topic để GỬI thì không được chứa ký tự đại diện."""
    if not topic:
        raise BadRequestError("Topic MQTT không được rỗng")
    if "+" in topic or "#" in topic:
        raise BadRequestError(
            f"Topic {topic!r} chứa ký tự đại diện — chỉ dùng được khi ĐĂNG KÝ NGHE, "
            "không dùng để gửi"
        )


def matches(topic_filter: str, topic: str) -> bool:
    """Topic cụ thể có khớp bộ lọc không."""
    wanted = topic_filter.split("/")
    actual = topic.split("/")

    # Theo chuẩn MQTT, ký tự đại diện ở tầng đầu KHÔNG chạm tới topic hệ thống
    # ($SYS/...). Không có luật này thì một handler nghe "#" sẽ hút cả số liệu
    # nội bộ của broker.
    if actual and actual[0].startswith("$") and wanted[0] in ("+", "#"):
        return False

    for i, level in enumerate(wanted):
        if level == "#":
            return True                      # nuốt mọi tầng còn lại, kể cả không còn tầng nào
        if i >= len(actual):
            return False
        if level != "+" and level != actual[i]:
            return False
    return len(wanted) == len(actual)


def covers(wider: str, narrower: str) -> bool:
    """Bộ lọc `wider` có bao trọn `narrower` không — mọi topic khớp `narrower` đều khớp `wider`.

    Cần để KHÔNG đăng ký hai bộ lọc chồng nhau lên broker. Đăng ký cả
    "thiet-bi/#" lẫn "thiet-bi/+/nhiet-do" thì mosquitto giao MỘT tin thành HAI
    lần (mỗi đăng ký một bản), và handler chạy gấp đôi. Đo được: gửi 1 tin,
    handler chạy 4 lượt.

    Cách sửa là chỉ đăng ký bộ lọc rộng nhất rồi tự chia tin ở trong tiến trình.

    Chú ý không dùng `matches()` để thay hàm này: `matches("a/+", "a/#")` trả về
    True vì nó coi "#" là một tầng chữ thường, trong khi "a/+" hoàn toàn KHÔNG
    bao được "a/#" (thiếu "a/b/c").
    """
    a = wider.split("/")
    b = narrower.split("/")
    for i, level in enumerate(a):
        if level == "#":
            return True                     # nuốt trọn phần còn lại của b
        if i >= len(b):
            return False                    # a còn đòi thêm tầng, b hết
        if b[i] == "#":
            return False                    # b rộng hơn ở đây (a không phải "#")
        if level == "+":
            continue                        # + bao được mọi tầng đơn, kể cả "+"
        if level != b[i]:
            return False
    return len(a) == len(b)


def narrow_filters(subscriptions: dict[str, int]) -> dict[str, int]:
    """Bỏ bộ lọc bị bộ lọc khác bao trọn; QoS dồn về cái còn lại (lấy mức cao nhất).

    Vào:  {"thiet-bi/#": 0, "thiet-bi/+/nhiet-do": 1}
    Ra:   {"thiet-bi/#": 1}
    """
    narrowed: dict[str, int] = {}
    for narrower, qos in sorted(subscriptions.items()):
        covering = next(
            (r for r in subscriptions if r != narrower and covers(r, narrower)),
            None,
        )
        if covering is None:
            narrowed[narrower] = max(narrowed.get(narrower, 0), qos)
        else:
            narrowed[covering] = max(narrowed.get(covering, 0), qos, subscriptions[covering])
    return narrowed
