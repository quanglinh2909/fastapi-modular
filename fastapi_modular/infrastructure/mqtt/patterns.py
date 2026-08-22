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
    tang = topic_filter.split("/")
    for i, phan in enumerate(tang):
        if "#" in phan:
            if phan != "#":
                raise BadRequestError(
                    f"Topic {topic_filter!r} sai: '#' phải chiếm trọn một tầng (vd 'nha/#')"
                )
            if i != len(tang) - 1:
                raise BadRequestError(
                    f"Topic {topic_filter!r} sai: '#' phải nằm ở cuối cùng"
                )
        elif "+" in phan and phan != "+":
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
    loc = topic_filter.split("/")
    that = topic.split("/")

    # Theo chuẩn MQTT, ký tự đại diện ở tầng đầu KHÔNG chạm tới topic hệ thống
    # ($SYS/...). Không có luật này thì một handler nghe "#" sẽ hút cả số liệu
    # nội bộ của broker.
    if that and that[0].startswith("$") and loc[0] in ("+", "#"):
        return False

    for i, phan in enumerate(loc):
        if phan == "#":
            return True                      # nuốt mọi tầng còn lại, kể cả không còn tầng nào
        if i >= len(that):
            return False
        if phan != "+" and phan != that[i]:
            return False
    return len(loc) == len(that)


def covers(rong: str, hep: str) -> bool:
    """Bộ lọc `rong` có bao trọn `hep` không — tức mọi topic khớp `hep` đều khớp `rong`.

    Cần để KHÔNG đăng ký hai bộ lọc chồng nhau lên broker. Đăng ký cả
    "thiet-bi/#" lẫn "thiet-bi/+/nhiet-do" thì mosquitto giao MỘT tin thành HAI
    lần (mỗi đăng ký một bản), và handler chạy gấp đôi. Đo được: gửi 1 tin,
    handler chạy 4 lượt.

    Cách sửa là chỉ đăng ký bộ lọc rộng nhất rồi tự chia tin ở trong tiến trình.

    Chú ý không dùng `matches()` để thay hàm này: `matches("a/+", "a/#")` trả về
    True vì nó coi "#" là một tầng chữ thường, trong khi "a/+" hoàn toàn KHÔNG
    bao được "a/#" (thiếu "a/b/c").
    """
    a = rong.split("/")
    b = hep.split("/")
    for i, phan in enumerate(a):
        if phan == "#":
            return True                     # nuốt trọn phần còn lại của b
        if i >= len(b):
            return False                    # a còn đòi thêm tầng, b hết
        if b[i] == "#":
            return False                    # b rộng hơn ở đây (a không phải "#")
        if phan == "+":
            continue                        # + bao được mọi tầng đơn, kể cả "+"
        if phan != b[i]:
            return False
    return len(a) == len(b)


def narrow_filters(loc: dict[str, int]) -> dict[str, int]:
    """Bỏ bộ lọc bị bộ lọc khác bao trọn; QoS dồn về cái còn lại (lấy mức cao nhất).

    Vào:  {"thiet-bi/#": 0, "thiet-bi/+/nhiet-do": 1}
    Ra:   {"thiet-bi/#": 1}
    """
    con: dict[str, int] = {}
    for hep, qos in sorted(loc.items()):
        bao = next(
            (r for r in loc if r != hep and covers(r, hep)),
            None,
        )
        if bao is None:
            con[hep] = max(con.get(hep, 0), qos)
        else:
            con[bao] = max(con.get(bao, 0), qos, loc[bao])
    return con
