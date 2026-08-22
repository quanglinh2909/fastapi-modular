
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]

class LogSettings(BaseModel):
    level: str = "INFO"
    json_format: bool = False
    """Bật để log dạng JSON (production); tắt để log màu, dễ đọc (local)."""

class CorsSettings(BaseModel):
    """Mặc định mở cho local. Prod bắt buộc liệt kê origin cụ thể — xem
    `Settings.check_production_safety()`; `["*"]` cộng allow_credentials=True
    khiến Starlette phản chiếu mọi Origin, tức bất kỳ website nào cũng gửi
    được request kèm cookie của người dùng."""

    allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = True

    @property
    def allows_any_origin(self) -> bool:
        return "*" in self.allow_origins


Driver = Literal["memory", "sqlite", "postgres", "mongodb"]
SchemaMode = Literal["off", "create", "sync"]


class DatabaseSettings(BaseModel):
    """Cấu hình database. Chỉ MỘT driver được dùng tại một thời điểm.

    Đặt qua biến môi trường, ví dụ:
        APP_DB__DRIVER=postgres
        APP_DB__DSN=postgresql+asyncpg://user:pass@localhost:5432/app
    """

    driver: Driver = "memory"
    dsn: str | None = None
    name: str = "app"
    echo: bool = False

    schema_mode: SchemaMode = "create"
    """Mức độ tự chỉnh schema lúc khởi động (tương đương `synchronize` của TypeORM).

    - "off"    : không đụng gì. Dùng cho production, đi kèm Alembic.
    - "create" : chỉ tạo bảng còn thiếu. Thêm/xoá trường trong entity KHÔNG có
                 tác dụng lên bảng đã tồn tại.
    - "sync"   : thêm cột mới, và báo cột thừa / cột lệch kiểu. Chỉ dùng ở dev.
    """

    drop_columns: bool = False
    """Cho phép schema_mode="sync" XOÁ cột không còn trong entity.

    Mặc định tắt vì xoá cột là mất dữ liệu vĩnh viễn. Bật khi bạn thật sự muốn
    dọn cột thừa trong lúc phát triển.
    """

    # ---- kết nối & phục hồi khi database rớt --------------------------------
    pool_pre_ping: bool = True
    """Kiểm tra connection còn sống trước khi giao cho request (chỉ SQL).

    Không bật thì sau mỗi lần database restart hoặc firewall cắt kết nối nhàn
    rỗi, request đầu tiên sẽ lỗi 500 vì nhận phải connection đã chết. Bật thì
    pool âm thầm thay bằng connection mới, client không thấy gì.
    """

    pool_size: int = 5
    max_overflow: int = 10
    """Trần connection là (pool_size + max_overflow) x SỐ WORKER, không phải
    tổng của cả app. Mặc định 15 mỗi worker; Postgres mặc định max_connections
    là 100, nên từ 6 worker trở lên phải giảm số này hoặc nâng max_connections."""
    pool_recycle_seconds: int = 1800
    """Đóng và mở lại connection cũ hơn ngưỡng này. Nhiều proxy/firewall cắt
    kết nối nhàn rỗi sau 30–60 phút mà không báo cho hai đầu."""

    connect_timeout_seconds: float = 10.0
    """Chờ tối đa bấy nhiêu giây khi MỞ kết nối. Với MongoDB đây cũng là hạn
    chọn server — để mặc định 30s của driver thì request sẽ treo rất lâu."""

    query_timeout_seconds: float = 15.0
    """Chờ tối đa bấy nhiêu giây cho MỘT câu truy vấn đã gửi đi.

    Khác connect_timeout: nếu database treo giữa lúc đang chạy câu lệnh (mất
    điện, đóng băng, khoá bảng), connection vẫn "mở" nên connect_timeout không
    cứu được — request sẽ treo cho tới khi client bỏ cuộc."""

    startup_retries: int = 10
    startup_retry_delay_seconds: float = 1.0

    circuit_breaker: bool = True
    """Ngắt mạch khi database hỏng liên tiếp, thay vì để mọi request cùng chờ
    hết timeout. Không áp dụng cho driver 'memory'."""

    circuit_failure_threshold: int = 5
    circuit_reset_seconds: float = 10.0
    """Thử lại khi khởi động mà database chưa sẵn sàng (hay gặp với
    docker compose: app lên trước database)."""

    _DEFAULT_DSN: ClassVar[dict[str, str]] = {
        "sqlite": "sqlite+aiosqlite:///./data/{name}.db",
        "postgres": "postgresql+asyncpg://postgres:postgres@localhost:5432/{name}",
        "mongodb": "mongodb://localhost:27017",
    }

    @property
    def resolved_dsn(self) -> str:
        if self.dsn:
            return self.dsn
        template = self._DEFAULT_DSN.get(self.driver, "")
        return template.format(name=self.name)

class WebSocketSettings(BaseModel):
    """Cấu hình lớp WebSocket. Đặt qua APP_WS__*, ví dụ APP_WS__ADAPTER=redis."""

    send_queue_size: int = 100
    """Trần số tin CHỜ GỬI cho mỗi kết nối.

    Client đọc chậm mà server cứ đẩy thì hàng đợi phình cho tới khi hết RAM —
    một client hỏng kéo sập cả worker. Có trần thì hỏng cục bộ ở đúng client đó.
    """

    overflow: Literal["close", "drop_oldest"] = "close"
    """Xử lý khi hàng đợi đầy.

    - "close"      : ngắt kết nối (mã 1013). Client nối lại và tải lại trạng
                     thái — trung thực hơn là âm thầm mất tin.
    - "drop_oldest": bỏ tin cũ nhất. Hợp với dữ liệu chỉ cần bản mới nhất
                     (vị trí, nhiệt độ, tiến độ).
    """

    heartbeat_seconds: float = 25.0
    """Chu kỳ server đẩy `ping`. Dưới 30s vì nhiều proxy (nginx, ALB, Cloudflare)
    cắt kết nối WebSocket nhàn rỗi khoảng 60s mà không báo hai đầu."""

    idle_timeout_seconds: float = 70.0
    """Không nhận được KHUNG TIN NÀO trong bấy nhiêu giây thì đóng.

    Phải lớn hơn heartbeat vài lần: client trả lời `pong` sẽ làm mới đồng hồ
    này, nên chỉ kết nối thật sự chết mới bị cắt. Thiếu nó thì socket "ma"
    (mất mạng đột ngột, TCP chưa kịp biết) tích lại tới hết bộ nhớ."""

    max_message_bytes: int = 64 * 1024
    max_messages_per_second: float = 50.0
    """Trần tần suất mỗi kết nối; 0 để tắt."""

    burst_messages: int = 100
    """Số tin cho phép bùng tức thời trước khi bị siết theo tần suất trên."""

    max_connections: int = 5000
    """Trần kết nối MỖI WORKER."""

    max_connections_per_user: int = 10
    """Trần kết nối đồng thời của một tài khoản (nhiều tab, nhiều thiết bị);
    0 để tắt. Chỉ có tác dụng khi gateway có guard xác thực."""

    max_rooms_per_socket: int = 64

    adapter: Literal["local", "redis"] = "local"
    """`local` chỉ đúng khi chạy MỘT worker: sổ kết nối nằm trong RAM tiến
    trình, worker này không thấy kết nối của worker kia nên broadcast sẽ thiếu
    người nhận. Nhiều worker/nhiều máy thì phải dùng `redis`."""

    redis_url: str = "redis://localhost:6379/0"
    channel: str = "ws:broadcast"


class RabbitSettings(BaseModel):
    """Cấu hình RabbitMQ. Đặt qua APP_RABBITMQ__*, ví dụ APP_RABBITMQ__ENABLED=true.

    MẶC ĐỊNH TẮT. Dự án không dùng RabbitMQ thì không phải cài `aio-pika`,
    không phải đụng gì tới file này, và mọi thứ chạy y như cũ — giống hệt cách
    driver database được tách riêng.
    """

    enabled: bool = False
    url: str = "amqp://guest:guest@localhost:5672/"
    """Mặc định trỏ localhost. Bật RabbitMQ mà quên đặt biến này thì lúc khởi
    động sẽ có cảnh báo `rabbitmq.default_url` — đọc kỹ nó trước khi đi tìm
    xem broker có chết không."""

    publish_timeout_seconds: float = 5.0
    """Chờ broker xác nhận đã nhận tin. Đây là thuộc tính của KẾT NỐI (mạng
    chậm, broker quá tải), không phải quyết định nghiệp vụ — nên nằm ở đây.
    Lời gọi nào cần khác thì truyền `publish(..., timeout=...)`."""
    connect_timeout_seconds: float = 10.0

    # ---- tự nối lại ---------------------------------------------------------
    heartbeat_seconds: int = 30
    """Nhịp tim AMQP. Mất điện đột ngột hay rút cáp thì KHÔNG có gói FIN nào
    cả — hai đầu vẫn tưởng kết nối còn sống. Nhịp tim là thứ duy nhất phát hiện
    được: quá hai nhịp không thấy hồi âm thì client coi như đứt và nối lại.
    Ngưỡng phát hiện vì vậy là khoảng 2 x giá trị này. Hạ xuống thì phát hiện
    nhanh hơn nhưng dễ báo đứt oan khi mạng chớp."""

    reconnect_delay_seconds: float = 2.0
    """Chờ bao lâu trước lần thử nối lại đầu tiên. Các lần sau tăng gấp đôi cho
    tới `max_reconnect_delay_seconds`, để broker vừa hồi sinh không bị hàng
    trăm tiến trình đập vào cùng lúc."""

    max_reconnect_delay_seconds: float = 30.0


    @property
    def dead_letter_exchange(self) -> str:
        return "dlx"


class RedisSettings(BaseModel):
    """Cấu hình Redis. Đặt qua APP_REDIS__*, ví dụ APP_REDIS__ENABLED=true.

    MẶC ĐỊNH TẮT, và độc lập với `APP_WS__REDIS_URL`. Hai thứ đó cố ý tách
    nhau: adapter WebSocket phải chạy được ở dự án không hề bật lớp Redis này,
    và ngược lại. Trỏ cả hai vào cùng một server thì hoàn toàn bình thường.
    """

    enabled: bool = False
    url: str = "redis://localhost:6379/0"
    """Dạng redis://[:MẬT_KHẨU@]HOST:CỔNG/SỐ_DB, hoặc rediss:// nếu có TLS."""

    key_prefix: str = ""
    """Tiền tố ghép vào MỌI khoá. Nhiều ứng dụng dùng chung một Redis thì đặt
    khác nhau ("don-hang:", "chat:") để không ai ghi đè khoá của ai."""

    connect_timeout_seconds: float = 5.0
    command_timeout_seconds: float = 5.0
    """Trần thời gian cho MỘT lệnh. Redis chậm còn tệ hơn Redis chết: không có
    trần thì mọi request đang chờ cache sẽ treo theo."""

    reconnect_delay_seconds: float = 1.0
    max_reconnect_delay_seconds: float = 30.0


class MqttSettings(BaseModel):
    """Cấu hình MQTT. Đặt qua APP_MQTT__*, ví dụ APP_MQTT__ENABLED=true.

    MẶC ĐỊNH TẮT. Dùng cho thiết bị IoT: nhẹ, giữ kết nối lâu, chịu được mạng
    chập chờn.
    """

    enabled: bool = False
    url: str = "mqtt://localhost:1883"
    """Dạng mqtt://[NGƯỜI_DÙNG:MẬT_KHẨU@]HOST:CỔNG, hoặc mqtts:// nếu có TLS."""

    client_id: str = ""
    """Danh tính phiên trên broker. Để trống thì sinh ngẫu nhiên mỗi lần chạy —
    tiện cho dev, nhưng KHÔNG dùng được với clean_session=false vì mỗi lần khởi
    động sẽ là một phiên mới toanh. Chạy nhiều worker thì mỗi worker phải một
    id khác nhau, trùng id là hai bên đá nhau ra khỏi broker liên tục."""

    clean_session: bool = True
    """false = broker GIỮ tin QoS>=1 lại trong lúc client ngắt, nối lại là giao
    tiếp. Cần đi kèm client_id cố định. true = mất tin trong lúc ngắt."""

    keepalive_seconds: int = 30
    """Nhịp tim MQTT: quá 1.5 nhịp không thấy gì thì broker coi như client
    chết. Đây là thứ duy nhất phát hiện được cảnh rút cáp/mất điện."""

    connect_timeout_seconds: float = 10.0
    reconnect_delay_seconds: float = 1.0
    max_reconnect_delay_seconds: float = 30.0


class KafkaSettings(BaseModel):
    """Cấu hình Kafka. Đặt qua APP_KAFKA__*, ví dụ APP_KAFKA__ENABLED=true.

    MẶC ĐỊNH TẮT. Dùng khi cần nhật ký sự kiện ĐỌC LẠI ĐƯỢC: tin không biến mất
    sau khi xử lý mà nằm lại theo thời gian giữ, nhiều nhóm consumer đọc cùng
    một dòng tin ở các vị trí khác nhau.
    """

    enabled: bool = False
    bootstrap_servers: str = "localhost:9092"
    """Danh sách HOST:CỔNG ngăn bằng dấu phẩy. Chỉ cần vài broker để hỏi đường;
    client tự tìm ra phần còn lại của cụm."""

    client_id: str = "fastapi-modular"
    acks: Literal["0", "1", "all"] = "all"
    """Bao nhiêu bản sao phải ghi xong thì mới coi là gửi thành công.
    all = an toàn nhất (chậm hơn) | 1 = chỉ leader | 0 = bắn đi rồi thôi."""

    request_timeout_seconds: float = 20.0
    connect_timeout_seconds: float = 10.0
    reconnect_delay_seconds: float = 1.0
    max_reconnect_delay_seconds: float = 30.0


class Settings(BaseSettings):
    """Cấu hình của khung. **Kế thừa được** để thêm biến của riêng bạn.

        # src/config.py
        from pydantic import BaseModel, Field
        from fastapi_modular import Settings

        class JwtSettings(BaseModel):
            secret: str = ""
            ttl_seconds: int = 3600

        class AppSettings(Settings):
            jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")
            stripe_key: str = Field(default="", alias="APP_STRIPE_KEY")

        # src/main.py
        app = create_app(AppSettings())

    Đọc được ngay từ `.env` hoặc biến môi trường: `APP_JWT__SECRET=...`,
    `APP_STRIPE_KEY=...`. Không phải khai báo ở đâu khác.

    Service nhận nó qua DI bằng CHÍNH lớp con, và vẫn có gợi ý kiểu đầy đủ:

        @injectable
        class TokenService:
            def __init__(self, settings: AppSettings) -> None:
                self._secret = settings.jwt.secret

    `create_app()` đăng ký instance dưới cả `Settings` lẫn mọi lớp con của nó,
    nên thư viện hỏi `Settings` và code của bạn hỏi `AppSettings` đều nhận đúng
    một đối tượng đó.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Các trường cấp app dùng prefix APP_ để tránh đụng biến môi trường hệ thống.
    env: Environment = Field(default="local", alias="APP_ENV")
    debug: bool = Field(default=True, alias="APP_DEBUG")
    name: str = Field(default="fastapi-modular", alias="APP_NAME")
    version: str = Field(default="0.1.0", alias="APP_VERSION")
    api_prefix: str = Field(default="/api", alias="APP_API_PREFIX")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")

    # Nhóm lồng nhau cũng dùng tiền tố APP_ cho đồng bộ; đặt qua biến môi
    # trường bằng dấu ngăn "__", ví dụ APP_DB__DRIVER=postgres.
    log: LogSettings = Field(default_factory=LogSettings, alias="APP_LOG")
    cors: CorsSettings = Field(default_factory=CorsSettings, alias="APP_CORS")
    db: DatabaseSettings = Field(default_factory=DatabaseSettings, alias="APP_DB")
    ws: WebSocketSettings = Field(default_factory=WebSocketSettings, alias="APP_WS")
    rabbitmq: RabbitSettings = Field(default_factory=RabbitSettings, alias="APP_RABBITMQ")
    redis: RedisSettings = Field(default_factory=RedisSettings, alias="APP_REDIS")
    mqtt: MqttSettings = Field(default_factory=MqttSettings, alias="APP_MQTT")
    kafka: KafkaSettings = Field(default_factory=KafkaSettings, alias="APP_KAFKA")


    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def docs_url(self) -> str | None:
        return None if self.is_prod else "/docs"

    @property
    def redoc_url(self) -> str | None:
        return None if self.is_prod else "/redoc"

    @property
    def openapi_url(self) -> str | None:
        return None if self.is_prod else f"{self.api_prefix}/openapi.json"

    def check_production_safety(self) -> list[str]:
        """Những cấu hình không được phép mang lên prod. Trả về danh sách cảnh báo."""
        problems: list[str] = []
        if not self.is_prod:
            return problems

        if self.cors.allows_any_origin:
            problems.append(
                "cors.allow_origins đang là '*' — với allow_credentials=True thì mọi "
                "website đều gọi được API kèm cookie người dùng. Hãy liệt kê domain cụ thể."
            )
        if self.debug:
            problems.append("debug=True ở prod sẽ lộ chi tiết lỗi ra client.")
        if self.db.driver == "memory":
            problems.append(
                "db.driver='memory' mất dữ liệu khi restart và sai khi chạy nhiều worker."
            )
        if self.db.schema_mode != "off":
            problems.append(
                f"db.schema_mode={self.db.schema_mode!r} tự chỉnh schema lúc khởi động; "
                "prod nên đặt 'off' và dùng Alembic."
            )
        if self.db.drop_columns:
            problems.append("db.drop_columns=True có thể xoá cột kèm dữ liệu.")
        if self.ws.adapter == "local":
            problems.append(
                "ws.adapter='local' chỉ đúng với MỘT worker: mỗi worker giữ sổ kết nối "
                "riêng nên broadcast sẽ không tới được client đang nối vào worker khác. "
                "Chạy nhiều worker thì đặt APP_WS__ADAPTER=redis."
            )
        return problems


# Biến môi trường đã đổi tên. Đặt tên cũ thì pydantic lặng lẽ bỏ qua và dùng
# giá trị mặc định — nghĩa là app chạy sai mà không báo gì. Bảng này để phát
# hiện và nói thẳng phải sửa thành gì.
_TEN_CU: dict[str, str] = {
    "APP_MQ__": "APP_RABBITMQ__",
}

# Biến đã bỏ hẳn. Để trong .env thì vô hại nhưng gây hiểu nhầm là nó còn tác
# dụng — nói rõ để người ta xoá đi.
_DA_BO: frozenset[str] = frozenset(
    {
        "BRIDGE_ENABLED",                 # cầu nối RabbitMQ -> WebSocket, đã gỡ
        "MAX_SUBSCRIPTIONS_PER_SOCKET",   # nt
        "FAIL_ON_STARTUP",                # luôn tự nối lại, không còn lựa chọn dừng app
        "EXCHANGES",                      # exchange tự khai lúc dùng, không cần liệt kê
        "DURABLE",                        # chuyển vào @rabbitmq_subscriber(durable=...)
        "CONSUMER_MAX_RETRIES",           # chuyển vào @rabbitmq_subscriber(max_retries=...)
        "CONSUMER_RETRY_DELAY_SECONDS",   # chuyển vào @rabbitmq_subscriber(retry_delay=...)
        "PREFETCH",                       # chuyển vào @rabbitmq_subscriber(prefetch=...)
    }
)


def check_deprecated_env(env_file: str = ".env") -> list[str]:
    """Tìm khai báo mang tên cũ, trong BIẾN MÔI TRƯỜNG lẫn trong .env.

    Phải soi cả file .env chứ không chỉ os.environ: pydantic đọc thẳng file đó,
    còn tên cũ nằm trong file thì không xuất hiện ở os.environ. Bỏ sót chỗ này
    thì cảnh báo vô dụng đúng vào trường hợp cần nó nhất.
    """
    names = set(os.environ)

    path = Path(env_file)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                names.add(line.split("=", 1)[0].strip())

    problems: list[str] = []
    for name in sorted(names):
        # Kiểm tra "đã bỏ" TRƯỚC "đổi tên": một biến vừa mang tên cũ vừa không
        # còn dùng thì lời khuyên đúng là xoá đi, không phải đổi tên.
        if name.rpartition("__")[2] in _DA_BO and name.startswith(("APP_MQ__", "APP_RABBITMQ__")):
            problems.append(f"{name} -> không còn dùng, xoá dòng này")
            continue
        for cu, moi in _TEN_CU.items():
            if name.startswith(cu):
                problems.append(f"{name} -> đổi thành {name.replace(cu, moi, 1)}")
    return problems


_LOP_SETTINGS: type[Settings] = Settings


def use_settings(cls: type[Settings]) -> None:
    """Khai lớp Settings mở rộng, cho những chỗ khung TỰ dựng cấu hình.

    `create_app(AppSettings())` đã tự gọi hàm này. Chỉ phải gọi tay ở những
    đường vào không đi qua create_app — chủ yếu là Alembic (`migrations/env.py`)
    và script chạy một lần:

        from src.core.config import AppSettings
        use_settings(AppSettings)
        settings = get_settings()      # giờ trả về AppSettings
    """
    global _LOP_SETTINGS
    if not (isinstance(cls, type) and issubclass(cls, Settings)):
        raise TypeError(f"{cls!r} phải là lớp con của Settings")
    if cls is not _LOP_SETTINGS:
        _LOP_SETTINGS = cls
        get_settings.cache_clear()


def settings_class() -> type[Settings]:
    """Lớp đang được dùng để dựng cấu hình."""
    return _LOP_SETTINGS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings — cache để không parse .env nhiều lần.

    Trả về lớp đã khai bằng `use_settings()`, mặc định là `Settings`.
    """
    return _LOP_SETTINGS()
