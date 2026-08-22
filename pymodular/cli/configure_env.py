"""Ghi sẵn biến môi trường của thành phần vừa cài vào .env.

Chạy qua `pym env <thành-phần>` (chỉ ghi .env) hoặc `pym install <thành-phần>`
(cài thư viện rồi ghi .env).

Mỗi thành phần có một KHỐI riêng, đánh dấu giữa hai mốc BEGIN/END. Chỉ khối đó
bị đụng tới, nên mọi biến khác trong .env (APP_HOST, APP_PORT, khoá bí mật...)
được giữ nguyên. Chạy lại với driver khác sẽ THAY khối cũ chứ không chồng thêm.
Các khối độc lập nhau: đổi driver database không đụng tới khối rabbitmq.

Mỗi biến sinh ra luôn có ba thứ:

    # <giải thích biến này làm gì>
    # tuỳ chọn · mặc định: false          <- xoá dòng dưới là quay về giá trị này
    APP_RABBITMQ__ENABLED=true

Giá trị mặc định KHÔNG gõ tay: nó được đọc thẳng từ model Settings tương ứng,
nên không thể lệch với code. Khai một biến không còn tồn tại trong model thì
script báo lỗi ngay — đó cũng là cách bắt những biến đã bị xoá khỏi code mà
quên xoá ở đây.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from pymodular.core.config import (
    DatabaseSettings,
    KafkaSettings,
    MqttSettings,
    RabbitSettings,
    RedisSettings,
    WebSocketSettings,
)

RONG = 78


def begin_marker(section: str) -> str:
    return f"# >>> {section} (sinh bởi pym env) >>>"


def _moc_cu(section: str) -> str:
    """Mốc thời CLI còn là `make install-*`.

    Vẫn phải nhận ra, để `pym env` THAY đúng khối trong .env đã có thay vì ghi
    thêm một khối thứ hai bên dưới — người dùng sẽ có hai `APP_DB__DRIVER` và
    cái nằm dưới lặng lẽ thắng.
    """
    return f"# >>> {section} (sinh bởi make install-*) >>>"


def end_marker(section: str) -> str:
    return f"# <<< {section} <<<"


@dataclass(frozen=True)
class Bien:
    """Một biến môi trường sẽ được ghi ra .env."""

    key: str
    value: str
    mo_ta: str
    bat_buoc: bool = False
    """True = xoá dòng này đi thì app chạy SAI một cách im lặng.

    Không có nghĩa là "không có mặc định" — `APP_DB__DRIVER` có mặc định
    (`memory`), nhưng xoá nó sau khi cài SQLite thì dữ liệu bốc hơi mỗi lần
    restart mà không ai báo gì.
    """


@dataclass(frozen=True)
class Khoi:
    """Một khối cấu hình: thuộc mục nào, đọc mặc định từ model nào."""

    section: str
    model: type[BaseModel]
    prefix: str
    items: list[Bien | str]
    """Phần tử là chuỗi thì in ra làm tiêu đề nhóm."""


def _mac_dinh(khoi: Khoi, key: str) -> str:
    """Đọc giá trị mặc định từ model, để .env và code không bao giờ lệch nhau."""
    ten_truong = key.removeprefix(khoi.prefix).lower()
    field = khoi.model.model_fields.get(ten_truong)
    if field is None:
        raise KeyError(
            f"{key} không còn tồn tại trong {khoi.model.__name__}. "
            "Xoá nó khỏi pymodular/cli/configure_env.py, hoặc thêm lại trường vào model."
        )
    default = field.default
    if default is None and ten_truong == "dsn":
        # DSN không có mặc định tĩnh: nó được suy ra từ driver lúc chạy. Lấy
        # đúng giá trị đó thay vì nói "(trống)" — người đọc cần biết app sẽ nối
        # vào đâu nếu họ xoá dòng này.
        driver = next(
            (b.value for b in khoi.items if isinstance(b, Bien) and b.key.endswith("__DRIVER")),
            "memory",
        )
        return DatabaseSettings(driver=driver).resolved_dsn or "(trống)"
    if default is None:
        return "(trống)"
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, str):
        return default or "(trống)"
    return str(default)


def render(khoi: Khoi) -> str:
    """Dựng nội dung khối .env, mỗi biến kèm giải thích và mặc định."""
    dong: list[str] = []
    for item in khoi.items:
        if isinstance(item, str):
            dong.append("" if not item else f"# --- {item} ---")
            continue
        dong.extend(f"# {d}" for d in textwrap.wrap(item.mo_ta, RONG - 2))
        if item.bat_buoc:
            canh_bao = (
                f"BẮT BUỘC — xoá dòng này thì app quay về {_mac_dinh(khoi, item.key)}, "
                "gần như chắc chắn không phải thứ bạn muốn"
            )
            dong.extend(f"# {d}" for d in textwrap.wrap(canh_bao, RONG - 2))
        else:
            dong.append(f"# tuỳ chọn · mặc định: {_mac_dinh(khoi, item.key)}")
        dong.append(f"{item.key}={item.value}")
    return "\n".join(dong)


# --------------------------------------------------------------------- các khối
BLOCKS: dict[str, Khoi] = {
    "sqlite": Khoi(
        "database",
        DatabaseSettings,
        "APP_DB__",
        [
            Bien(
                "APP_DB__DRIVER",
                "sqlite",
                "Backend database đang dùng. Xoá dòng này thì app chạy bằng bộ nhớ tạm "
                "và mất sạch dữ liệu mỗi lần restart.",
                bat_buoc=True,
            ),
            Bien(
                "APP_DB__DSN",
                "sqlite+aiosqlite:///./data/app.db",
                "Đường dẫn file .db. Thư mục chứa nó phải tồn tại — lệnh này tự tạo ./data.",
            ),
            Bien(
                "APP_DB__SCHEMA_MODE",
                "create",
                "Mức tự chỉnh schema lúc khởi động. off = không đụng gì (dùng cho "
                "production kèm Alembic) | create = chỉ tạo bảng còn thiếu | "
                "sync = thêm cột mới theo entity và báo cột thừa (chỉ dev).",
            ),
            Bien(
                "APP_DB__DROP_COLUMNS",
                "false",
                "Cho phép sync XOÁ cột không còn trong entity. Xoá cột là mất dữ liệu.",
            ),
            Bien("APP_DB__ECHO", "false", "In câu SQL ra log khi cần soi."),
        ],
    ),
    "postgres": Khoi(
        "database",
        DatabaseSettings,
        "APP_DB__",
        [
            Bien(
                "APP_DB__DRIVER",
                "postgres",
                "Backend database đang dùng. Xoá dòng này thì app chạy bằng bộ nhớ tạm "
                "và mất sạch dữ liệu mỗi lần restart.",
                bat_buoc=True,
            ),
            Bien(
                "APP_DB__DSN",
                "postgresql+asyncpg://postgres:postgres@localhost:5432/app",
                "Dạng postgresql+asyncpg://NGƯỜI_DÙNG:MẬT_KHẨU@HOST:CỔNG/TÊN_DB.",
                bat_buoc=True,
            ),
            Bien(
                "APP_DB__SCHEMA_MODE",
                "create",
                "off | create | sync — xem docs/database.md.",
            ),
            Bien(
                "APP_DB__DROP_COLUMNS",
                "false",
                "Cho phép sync XOÁ cột không còn trong entity. Xoá cột là mất dữ liệu.",
            ),
            Bien("APP_DB__ECHO", "false", "In câu SQL ra log khi cần soi."),
            "kết nối & phục hồi khi database rớt",
            Bien(
                "APP_DB__POOL_PRE_PING",
                "true",
                "Thử connection còn sống trước khi giao cho request. Tắt đi thì mỗi "
                "lần database restart sẽ có đúng một request lỗi.",
            ),
            Bien(
                "APP_DB__POOL_SIZE",
                "5",
                "Trần connection = (POOL_SIZE + MAX_OVERFLOW) x số worker. Mặc định "
                "15 mỗi worker; Postgres cho tối đa 100 nên trên 6 worker phải giảm.",
            ),
            Bien("APP_DB__MAX_OVERFLOW", "10", "Số connection mượn thêm lúc cao điểm."),
            Bien(
                "APP_DB__POOL_RECYCLE_SECONDS",
                "1800",
                "Mở lại connection cũ hơn ngần này giây; proxy hay cắt kết nối nhàn rỗi.",
            ),
            Bien("APP_DB__CONNECT_TIMEOUT_SECONDS", "10", "Chờ tối đa khi MỞ kết nối."),
            Bien(
                "APP_DB__QUERY_TIMEOUT_SECONDS",
                "15",
                "Chờ tối đa cho MỘT câu truy vấn đã gửi đi. Khác CONNECT_TIMEOUT: "
                "database treo giữa chừng thì connection vẫn mở.",
            ),
            Bien(
                "APP_DB__STARTUP_RETRIES",
                "5",
                "Thử lại mấy lần khi khởi động mà database chưa sẵn sàng.",
            ),
            Bien("APP_DB__STARTUP_RETRY_DELAY_SECONDS", "1", "Chờ giữa các lần thử."),
            "ngắt mạch khi database hỏng",
            Bien(
                "APP_DB__CIRCUIT_BREAKER",
                "true",
                "Hỏng liên tiếp quá ngưỡng thì trả 503 ngay, không chạm database nữa.",
            ),
            Bien("APP_DB__CIRCUIT_FAILURE_THRESHOLD", "5", "Số lần hỏng liên tiếp để ngắt."),
            Bien("APP_DB__CIRCUIT_RESET_SECONDS", "10", "Bao lâu thì thử đóng mạch lại."),
        ],
    ),
    "mongodb": Khoi(
        "database",
        DatabaseSettings,
        "APP_DB__",
        [
            Bien(
                "APP_DB__DRIVER",
                "mongodb",
                "Backend database đang dùng. Xoá dòng này thì app chạy bằng bộ nhớ tạm "
                "và mất sạch dữ liệu mỗi lần restart.",
                bat_buoc=True,
            ),
            Bien(
                "APP_DB__DSN",
                "mongodb://localhost:27017",
                "Dạng mongodb://HOST:CỔNG hoặc mongodb+srv://NGƯỜI_DÙNG:MẬT_KHẨU@CỤM.",
                bat_buoc=True,
            ),
            Bien(
                "APP_DB__NAME",
                "app",
                "Tên database bên trong Mongo. Collection lấy theo tên entity.",
            ),
            Bien(
                "APP_DB__CONNECT_TIMEOUT_SECONDS",
                "10",
                "Hạn chọn server. Mặc định của driver là 30s, quá lâu cho một request.",
            ),
            Bien("APP_DB__QUERY_TIMEOUT_SECONDS", "15", "Hạn cho MỘT câu truy vấn."),
            Bien("APP_DB__STARTUP_RETRIES", "5", "Thử lại khi khởi động mà database chưa lên."),
            Bien("APP_DB__STARTUP_RETRY_DELAY_SECONDS", "1", "Chờ giữa các lần thử."),
            "ngắt mạch khi database hỏng",
            Bien("APP_DB__CIRCUIT_BREAKER", "true", "Hỏng liên tiếp quá ngưỡng thì trả 503 ngay."),
            Bien("APP_DB__CIRCUIT_FAILURE_THRESHOLD", "5", "Số lần hỏng liên tiếp để ngắt."),
            Bien("APP_DB__CIRCUIT_RESET_SECONDS", "10", "Bao lâu thì thử đóng mạch lại."),
        ],
    ),
    "ws-redis": Khoi(
        "websocket",
        WebSocketSettings,
        "APP_WS__",
        [
            Bien(
                "APP_WS__ADAPTER",
                "redis",
                "Cách phát tin WebSocket xuyên worker. local = mỗi worker một sổ kết "
                "nối riêng, chỉ đúng khi chạy MỘT worker. redis = mọi worker cùng nhận.",
            ),
            Bien("APP_WS__REDIS_URL", "redis://localhost:6379/0", "Redis dùng làm kênh chung."),
            Bien(
                "APP_WS__CHANNEL",
                "ws:broadcast",
                "Kênh pub/sub. Nhiều ứng dụng chung một Redis thì đặt tên khác nhau.",
            ),
            "giới hạn cho mỗi kết nối",
            Bien(
                "APP_WS__SEND_QUEUE_SIZE",
                "100",
                "Trần số tin chờ gửi. Client đọc chậm mà vượt trần thì bị ngắt, thay vì "
                "để hàng đợi phình tới lúc hết RAM.",
            ),
            Bien(
                "APP_WS__OVERFLOW",
                "close",
                "Khi hàng đợi đầy: close = ngắt client chậm | drop_oldest = bỏ tin cũ "
                "(hợp với dữ liệu chỉ cần bản mới nhất như vị trí, nhiệt độ).",
            ),
            Bien(
                "APP_WS__HEARTBEAT_SECONDS",
                "25",
                "Chu kỳ server gửi ping. Để dưới 30s vì nhiều proxy cắt kết nối nhàn rỗi ~60s.",
            ),
            Bien(
                "APP_WS__IDLE_TIMEOUT_SECONDS",
                "70",
                "Không nhận được khung nào trong ngần này giây thì đóng — cách duy nhất "
                "phát hiện client đã chết mà TCP chưa biết.",
            ),
            Bien("APP_WS__MAX_MESSAGE_BYTES", "65536", "Khung tin dài hơn bị từ chối."),
            Bien("APP_WS__MAX_MESSAGES_PER_SECOND", "50", "Trần tần suất mỗi kết nối; 0 để tắt."),
            Bien("APP_WS__MAX_CONNECTIONS", "5000", "Trần kết nối mỗi worker."),
            Bien(
                "APP_WS__MAX_CONNECTIONS_PER_USER",
                "10",
                "Trần kết nối đồng thời của một tài khoản; 0 để tắt.",
            ),
        ],
    ),
    "redis": Khoi(
        "redis",
        RedisSettings,
        "APP_REDIS__",
        [
            Bien(
                "APP_REDIS__ENABLED",
                "true",
                "Bật/tắt lớp Redis (cache, đếm, pub/sub). Đặt false thì phần còn lại "
                "của app chạy bình thường, không cần gỡ thư viện hay sửa code. Không "
                "liên quan tới APP_WS__ADAPTER — adapter WebSocket có cấu hình riêng.",
            ),
            Bien(
                "APP_REDIS__URL",
                "redis://localhost:6379/0",
                "Dạng redis://[:MẬT_KHẨU@]HOST:CỔNG/SỐ_DB, hoặc rediss:// nếu có TLS.",
                bat_buoc=True,
            ),
            Bien(
                "APP_REDIS__KEY_PREFIX",
                "",
                "Tiền tố ghép vào mọi khoá và mọi kênh. Nhiều ứng dụng dùng chung một "
                "Redis thì đặt khác nhau để không ai ghi đè khoá của ai.",
            ),
            "thời gian chờ",
            Bien(
                "APP_REDIS__CONNECT_TIMEOUT_SECONDS",
                "5",
                "Chờ mở kết nối. Hết giờ thì app vẫn chạy và nối lại ngầm.",
            ),
            Bien(
                "APP_REDIS__COMMAND_TIMEOUT_SECONDS",
                "5",
                "Trần thời gian cho MỘT lệnh. Redis chậm còn tệ hơn Redis chết: không "
                "có trần thì mọi request đang chờ cache sẽ treo theo.",
            ),
            "tự nối lại",
            Bien(
                "APP_REDIS__RECONNECT_DELAY_SECONDS",
                "1",
                "Chờ trước lần thử lại đầu tiên; các lần sau tăng gấp đôi.",
            ),
            Bien(
                "APP_REDIS__MAX_RECONNECT_DELAY_SECONDS",
                "30",
                "Trần thời gian chờ giữa hai lần thử. Có trần thì server hồi sinh sau "
                "nhiều giờ vẫn được nối lại trong vòng ngần này giây.",
            ),
        ],
    ),
    "mqtt": Khoi(
        "mqtt",
        MqttSettings,
        "APP_MQTT__",
        [
            Bien(
                "APP_MQTT__ENABLED",
                "true",
                "Bật/tắt lớp MQTT. Đặt false thì phần còn lại của app chạy bình thường.",
            ),
            Bien(
                "APP_MQTT__URL",
                "mqtt://localhost:1883",
                "Dạng mqtt://[NGƯỜI_DÙNG:MẬT_KHẨU@]HOST:CỔNG, hoặc mqtts:// nếu có TLS.",
                bat_buoc=True,
            ),
            "phiên làm việc",
            Bien(
                "APP_MQTT__CLIENT_ID",
                "",
                "Danh tính phiên trên broker. Để trống thì sinh ngẫu nhiên mỗi lần chạy. "
                "Chạy nhiều worker thì mỗi worker phải một id khác nhau — trùng id là "
                "hai bên đá nhau ra khỏi broker liên tục.",
            ),
            Bien(
                "APP_MQTT__CLEAN_SESSION",
                "true",
                "false = broker GIỮ tin QoS>=1 lại trong lúc client ngắt và giao tiếp khi "
                "nối lại; cần đi kèm CLIENT_ID cố định. true = mất tin trong lúc ngắt.",
            ),
            Bien(
                "APP_MQTT__KEEPALIVE_SECONDS",
                "30",
                "Nhịp tim MQTT. Đây là thứ duy nhất phát hiện được cảnh rút cáp hay mất "
                "điện, vì lúc đó không có gói ngắt kết nối nào được gửi đi.",
            ),
            "tự nối lại",
            Bien("APP_MQTT__CONNECT_TIMEOUT_SECONDS", "10", "Chờ lần bắt tay đầu tiên."),
            Bien(
                "APP_MQTT__RECONNECT_DELAY_SECONDS",
                "1",
                "Chờ trước lần thử lại đầu tiên; các lần sau tăng gấp đôi.",
            ),
            Bien(
                "APP_MQTT__MAX_RECONNECT_DELAY_SECONDS",
                "30",
                "Trần thời gian chờ giữa hai lần thử. Có trần thì server hồi sinh sau "
                "nhiều giờ vẫn được nối lại trong vòng ngần này giây.",
            ),
        ],
    ),
    "kafka": Khoi(
        "kafka",
        KafkaSettings,
        "APP_KAFKA__",
        [
            Bien(
                "APP_KAFKA__ENABLED",
                "true",
                "Bật/tắt lớp Kafka. Đặt false thì phần còn lại của app chạy bình thường.",
            ),
            Bien(
                "APP_KAFKA__BOOTSTRAP_SERVERS",
                "localhost:9092",
                "Danh sách HOST:CỔNG ngăn bằng dấu phẩy. Chỉ cần vài broker để hỏi "
                "đường; client tự tìm ra phần còn lại của cụm.",
                bat_buoc=True,
            ),
            Bien(
                "APP_KAFKA__CLIENT_ID",
                "pymodular",
                "Tên ứng dụng hiện trong log và số đo của cụm Kafka.",
            ),
            Bien(
                "APP_KAFKA__ACKS",
                "all",
                "Bao nhiêu bản sao phải ghi xong mới coi là gửi thành công. "
                "all = an toàn nhất (chậm hơn) | 1 = chỉ leader | 0 = bắn đi rồi thôi.",
            ),
            "thời gian chờ",
            Bien("APP_KAFKA__REQUEST_TIMEOUT_SECONDS", "20", "Trần cho một lần gửi/nhận."),
            Bien("APP_KAFKA__CONNECT_TIMEOUT_SECONDS", "10", "Chờ lần nối đầu tiên."),
            "tự nối lại",
            Bien(
                "APP_KAFKA__RECONNECT_DELAY_SECONDS",
                "1",
                "Chờ trước lần thử lại đầu tiên; các lần sau tăng gấp đôi.",
            ),
            Bien(
                "APP_KAFKA__MAX_RECONNECT_DELAY_SECONDS",
                "30",
                "Trần thời gian chờ giữa hai lần thử. Có trần thì server hồi sinh sau "
                "nhiều giờ vẫn được nối lại trong vòng ngần này giây.",
            ),
        ],
    ),
    "rabbitmq": Khoi(
        "rabbitmq",
        RabbitSettings,
        "APP_RABBITMQ__",
        [
            Bien(
                "APP_RABBITMQ__ENABLED",
                "true",
                "Bật/tắt toàn bộ lớp RabbitMQ. Đặt false thì phần còn lại của app chạy "
                "bình thường, không cần gỡ thư viện hay sửa code.",
            ),
            Bien(
                "APP_RABBITMQ__URL",
                "amqp://guest:guest@localhost:5672/",
                "Dạng amqp://NGƯỜI_DÙNG:MẬT_KHẨU@HOST:CỔNG/VHOST. Đặt sai TÊN BIẾN thì "
                "app dùng mặc định và báo lỗi localhost.",
                bat_buoc=True,
            ),
            "thời gian chờ",
            Bien(
                "APP_RABBITMQ__PUBLISH_TIMEOUT_SECONDS",
                "5",
                "Chờ broker xác nhận một lần đăng tin. Đè từng lời gọi bằng "
                "publish(..., timeout=...).",
            ),
            Bien("APP_RABBITMQ__CONNECT_TIMEOUT_SECONDS", "10", "Chờ tối đa khi mở kết nối."),
            Bien(
                "APP_RABBITMQ__HEARTBEAT_SECONDS",
                "30",
                "Nhịp tim AMQP. Mất mạng đột ngột không có gói FIN nào, đây là thứ duy "
                "nhất phát hiện được; ngưỡng phát hiện khoảng 2 lần giá trị này.",
            ),
            "tự nối lại (luôn bật, không tắt được)",
            Bien("APP_RABBITMQ__RECONNECT_DELAY_SECONDS", "2", "Chờ trước lần thử nối lại đầu."),
            Bien(
                "APP_RABBITMQ__MAX_RECONNECT_DELAY_SECONDS",
                "30",
                "Trần thời gian chờ; mỗi lần hỏng lại tăng gấp đôi cho tới mức này.",
            ),
            "",
            (Bien.__doc__ and "") or "",
        ],
    ),
}
# Chính sách của từng consumer (thử lại, hàng đợi chết, prefetch...) KHÔNG nằm
# trong .env — khai ngay tại @rabbitmq_subscriber, xem docs/rabbitmq.md.
BLOCKS["rabbitmq"].items[:] = [i for i in BLOCKS["rabbitmq"].items if i != ""]


def strip_managed_block(text: str, section: str = "database") -> str:
    end = end_marker(section)
    begin = next(
        (m for m in (begin_marker(section), _moc_cu(section)) if m in text),
        begin_marker(section),
    )
    if begin not in text:
        return text.rstrip("\n")
    head, _, rest = text.partition(begin)
    _, _, tail = rest.partition(end)
    return (head.rstrip("\n") + "\n" + tail.lstrip("\n")).rstrip("\n")


def main(driver: str, env_path: Path) -> int:
    khoi = BLOCKS.get(driver)
    if khoi is None:
        print(f"Thành phần không hợp lệ: {driver}. Chọn một trong {sorted(BLOCKS)}.")
        return 1

    begin, end = begin_marker(khoi.section), end_marker(khoi.section)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    had_block = begin in existing
    body = strip_managed_block(existing, khoi.section)

    # SQLite ghi thẳng ra file: thiếu thư mục là lần chạy đầu tiên chết ngay,
    # với một lỗi nói về "unable to open database file" chứ không nói thiếu gì.
    if driver == "sqlite":
        (env_path.parent / "data").mkdir(parents=True, exist_ok=True)

    noi_dung = render(khoi)
    parts = [p for p in (body, begin, noi_dung, end) if p]
    env_path.write_text("\n".join(parts) + "\n", encoding="utf-8")

    print(f"{'Đã thay' if had_block else 'Đã thêm'} khối {khoi.section} trong {env_path}:")
    for item in khoi.items:
        if isinstance(item, Bien):
            mac = _mac_dinh(khoi, item.key)
            ghi_chu = f"BẮT BUỘC, đừng xoá — mặc định là {mac}" if item.bat_buoc else f"tuỳ chọn, mặc định {mac}"
            print(f"    {item.key}={item.value}   ({ghi_chu})")
    print("Sửa lại giá trị cho khớp máy bạn rồi chạy: pym dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else ".env")))
