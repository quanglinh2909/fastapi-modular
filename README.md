# pymodular

FastAPI theo kiến trúc module kiểu NestJS: DI container, controller dạng class,
repository chung cho nhiều loại database, gateway WebSocket có phòng, và bốn lớp
hạ tầng tuỳ chọn: RabbitMQ, Redis, MQTT, Kafka.

> **In English:** pymodular brings NestJS-style modular architecture to FastAPI —
> a DI container, class-based controllers, auto-discovered modules, a shared
> repository over four databases, a WebSocket gateway with rooms, and optional
> RabbitMQ / Redis / MQTT / Kafka layers that stay dormant until enabled.
> **Documentation is in Vietnamese**; the public API is English.

## Bắt đầu

Cần **Python 3.10+**.

```bash
pip install pymodular

mkdir du-an-cua-toi && cd du-an-cua-toi
pym init          # đổ file vào ĐÚNG thư mục này, không tạo thêm cấp
pym dev
```

Mở http://localhost:8000/docs — đã có sẵn một module `health` chạy được.

`pym init` không ghi đè file nào đã có, nên chạy được cả trong thư mục đang có
code. Muốn tạo hẳn thư mục mới thì `pym new du-an-cua-toi`.

Lõi **không kéo theo** driver database hay client hàng đợi nào. Cần cái gì thì
thêm cái đó:

```bash
pym install sqlite      # hoặc postgres, mongodb
pym install rabbitmq    # hoặc redis, mqtt, kafka
pym install all         # tất cả
```

`pym install` vừa cài thư viện vừa ghi biến vào `.env`. Muốn tự cài bằng pip
cũng được: `pip install "pymodular[sqlite,rabbitmq]"`.

## Lệnh

Một chương trình, hai tên: `pymodular` (đầy đủ) và `pym` (gõ tắt). Dưới đây dùng
`pym` cho gọn.

Tên lệnh rút gọn được tới khi nào tiền tố còn chỉ đúng một lệnh — `pym mo alerts`
chạy y hệt `pym module alerts`. Nhập nhằng thì `pym` hỏi lại chứ không đoán:

```
$ pym m
pym: lệnh 'm' chưa rõ — khớp với migrate, module. Gõ thêm vài chữ cho rõ.
```

| Lệnh | Rút gọn | Làm gì |
|---|---|---|
| `pym init` | `pym ini` | dựng dự án **trong thư mục hiện tại**, không ghi đè file nào đã có |
| `pym new <tên>` | `pym n` | dựng dự án trong một thư mục mới |
| `pym dev` | `pym d` | chạy kèm autoreload |
| `pym run --workers 4` | `pym r` | chạy chế độ production |
| `pym module <tên>` | `pym mo` | sinh module: controller + service + dto + entity |
| `pym module <tên> --gateway` | | kèm gateway WebSocket (`--consumer` cho RabbitMQ) |
| `pym env <thành-phần>` | `pym e` | chỉ ghi biến cấu hình vào `.env` (không cài gì) |
| `pym clean` | `pym c` | xoá cache và bản dựng (không đụng `data/`) |
| `pym build` · `pym publish [--test]` | `pym b` · `pym p` | dựng wheel/sdist · đẩy lên PyPI |
| `pym info` | `pym inf` | đang nối vào đâu, thư viện nào đã cài, cảnh báo cấu hình prod |
| `pym migrate [up\|down\|history\|sql\|create]` | `pym mi` | Alembic |
| `pym test` · `pym lint [--fix]` | `pym t` · `pym l` | pytest · ruff |
| **Thêm database** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `pym install sqlite` | `pym ins s` | file `.db`, không cần server |
| `pym install postgres` | `pym ins p` | PostgreSQL |
| `pym install mongodb` | `pym ins mo` | MongoDB |
| **Thêm hàng đợi** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `pym install rabbitmq` | `pym ins ra` | hàng đợi bền, thử lại + DLQ |
| `pym install redis` | `pym ins re` | cache, đếm nguyên tử, pub/sub |
| `pym install mqtt` | `pym ins mq` | thiết bị IoT |
| `pym install kafka` | `pym ins k` | nhật ký sự kiện đọc lại được |
| `pym install ws-redis` | `pym ins w` | phát tin WebSocket xuyên nhiều worker |
| `pym install all` | `pym ins a` | tất cả những thứ trên |

Tham số dạng danh sách cũng rút gọn theo cùng luật đó: `pym ins sq`,
`pym e post`, `pym mi h`. Còn giá trị bạn tự đặt thì không bị đụng tới —
`pym mo ins` tạo module tên đúng là `ins`.

Host và cổng lấy từ `APP_HOST` / `APP_PORT` trong `.env`, nên `pym dev` không cần
tham số. `pym --help` cho danh sách đầy đủ.

## Thêm module

```bash
pym module alerts              # controller + service + dto + entities
pym module alerts --gateway    # kèm gateway WebSocket
pym module alerts --consumer   # kèm consumer RabbitMQ
```

Route xuất hiện ngay, bảng được tạo ngay, validate chạy ngay — chỉ thân hàm là
chưa viết (gọi vào trả 501 kèm tên hàm). Việc của bạn: thêm trường vào entity và
DTO, rồi viết thân hàm trong service.

Không phải sửa file nào khác. Chi tiết: [docs/architecture.md](docs/architecture.md#thêm-module-mới).

## Chọn database

`pym install sqlite` (hoặc `postgres`, `mongodb`) làm cả hai việc: cài thư viện
của đúng driver đó, rồi ghi biến vào `.env`. Chỉ muốn ghi `.env` mà không cài gì
thì dùng `pym env sqlite`.

`pym env` ghi mỗi biến kèm giải thích, cho biết nó **bắt buộc hay tuỳ chọn** và
**mặc định là gì** nếu xoá dòng đi. `pym info` cho biết hiện đang nối vào đâu.

Chi tiết: [docs/database.md](docs/database.md).

## Cấu hình của riêng bạn

Kế thừa `Settings` là thêm được biến vào `.env`, không phải sửa gì trong khung:

```python
# src/core/config.py — pym init sinh sẵn file này
class AppSettings(Settings):
    team_name: str = Field(default="", alias="APP_TEAM_NAME")
    jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")   # -> APP_JWT__SECRET
```

Service nhận `AppSettings` qua DI với gợi ý kiểu đầy đủ.
Chi tiết: [docs/config.md](docs/config.md).

## Điểm vào là file của bạn

`pym init` sinh ra `src/main.py` với từng bước lắp ráp bày ra hết — thêm
middleware, đổi CORS, gắn router bên thứ ba thì sửa thẳng ở đó:

```python
settings = bind_settings(AppSettings())
configure_logging(settings.log)

app = new_fastapi(settings, lifespan=lifespan)
add_middleware(app, settings)                       # CORS + request-id + access log
register_error_handlers(app, debug=settings.debug)
register_routes(app, prefix=settings.api_prefix)    # quét src/api/
```

Chưa cần sửa gì thì cả khối rút lại còn `app = create_app(AppSettings())` —
`create_app` chạy đúng dãy trên, không hơn.

Vòng đời cũng vậy: `src/core/lifespan.py` là của bạn, chỉ **bọc** phần hạ tầng
của khung lại:

```python
@asynccontextmanager
async def lifespan(app):
    async with framework_lifespan(app):   # khung mở database, hàng đợi
        await warm_cache()                # việc riêng — database đã dùng được
        try:
            yield
        finally:
            await flush_ledger()          # việc riêng — database VẪN CÒN
```

Đo trên log thật: `db.connected` → `app.started` → **`app.ready`** → …phục vụ… →
**`app.closing`** → `app.stopping` → `app.stopped`.

## Realtime (WebSocket)

Một client một kết nối, vào phòng để nhận tin theo nhóm, hoặc nhận tin gửi
thẳng cho riêng mình:

```python
@gateway(path="/ws/alerts", guards=[WsJwt], client_rooms=True)
class AlertGateway:
    @subscribe("alert.ack")
    async def ack(self, socket: Socket, payload: AlertAck) -> dict:
        return {"ok": True}
```

```bash
pym dev
# ws://localhost:8000/ws/chat?client_id=an

pym module alerts --gateway-only   # thêm gateway vào module đã có
pym install ws-redis               # bắt buộc khi chạy nhiều worker
```

Đẩy tin từ REST hay tác vụ nền: nhận `WebSocketServer` qua `__init__` rồi gọi
`to_room` / `to_user` / `to_socket`.

Hướng dẫn đầy đủ (kèm cách dùng bằng **Postman** và client **Next.js**):
[docs/websocket.md](docs/websocket.md).

## Hàng đợi (RabbitMQ — tuỳ chọn)

```python
await self._mq.publish("events", "alert.created.hanoi", {"id": "A1"})

# Mặc định: đúng MỘT hàng đợi trên broker, hỏng là bỏ (có log).
@rabbitmq_subscriber("events", "alert.created", queue="alert-mailer")
async def gui_mail(self, payload: AlertCreated) -> None: ...

# Tự bật khi tin đáng tiền -> thêm alert-mailer.retry và alert-mailer.dlq
@rabbitmq_subscriber("events", "alert.created", queue="alert-mailer",
                     max_retries=3, dead_letter=True)
async def gui_mail(self, payload: AlertCreated) -> None: ...
```

```bash
pym install rabbitmq            # cài aio-pika + ghi APP_RABBITMQ__* vào .env
pym module alerts --consumer    # module mới kèm consumer
```

Không cài, không bật thì mọi thứ chạy y như chưa từng có nó. Broker rớt thì app
vẫn phục vụ và tự nối lại. Chi tiết: [docs/rabbitmq.md](docs/rabbitmq.md).

## Redis, MQTT, Kafka (cũng tuỳ chọn)

Cùng một khuôn với RabbitMQ: một package riêng dưới `infrastructure/`, một nhóm
biến `APP_<TÊN>__*`, mặc định **tắt**, thư viện chỉ import khi bật, và luôn tự
nối lại.

```bash
pym install redis    # cache, đếm, pub/sub      -> docs/redis.md
pym install mqtt     # thiết bị IoT             -> docs/mqtt.md
pym install kafka    # nhật ký sự kiện          -> docs/kafka.md
```

```python
await redis.cached("bao-cao:A", tinh_that, ttl=30)     # trượt thì tính, trúng thì thôi
await mqtt.publish("thiet-bi/bep/den", "ON", qos=1, retain=True)
await kafka.publish("don-hang", don, key=don.ma_don)   # cùng key = cùng thứ tự

@redis_subscriber("gia:*")                      # Redis: mọi worker một bản sao
@mqtt_subscriber("thiet-bi/+/nhiet-do", qos=1)  # MQTT:  + một tầng, # mọi tầng
@kafka_subscriber("don-hang", group="kho-van")  # Kafka: mỗi nhóm một con trỏ đọc
```

| Cần gì | Dùng gì |
|---|---|
| tin không được mất, chia việc cho worker | RabbitMQ |
| nhanh, mọi worker nhận một bản sao, mất vài tin cũng được | Redis |
| thiết bị, mạng chập chờn, kết nối lâu | MQTT |
| đọc lại được lịch sử, nhiều nhóm đọc độc lập | Kafka |

## Vận hành

```bash
curl localhost:8000/api/health        # liveness
curl localhost:8000/api/health/ready  # readiness, có ping database
curl localhost:8000/api/metrics       # số đo dạng Prometheus
pym migrate                           # chạy migration (SQL)
pym info                              # cấu hình đang dùng + cảnh báo prod
```

Chi tiết: [docs/operations.md](docs/operations.md).

## Cấu trúc repo này

```
pymodular/          THƯ VIỆN — thứ được đóng gói và cài về
  core/             DI, controller, config, WebSocket, guard, số đo
  infrastructure/   database, rabbitmq, redis, mqtt, kafka (mỗi thứ một package)
  cli/              init · new · dev · run · module · env · info · migrate · test · lint
  factory.py        create_app()
  discovery.py      tự quét package ứng dụng, dựng router
src/                ỨNG DỤNG MẪU — không nằm trong gói cài; xoá thoải mái
  main.py           điểm vào: lắp ráp app — file của bạn, không phải của khung
  core/config.py    AppSettings: kế thừa Settings để thêm biến .env của bạn
  core/lifespan.py  việc lúc khởi động / lúc tắt của riêng ứng dụng
  api/              các module nghiệp vụ; mỗi thư mục con là một module
tests/              299 test chạy không cần hạ tầng, 40 test nữa bật khi có server thật
docs/               tài liệu tra cứu
```

`pymodular/` không import gì từ `src/`. Nó chỉ biết "có một package tên
`src.api`, quét nó đi" — nên dự án xếp khác cũng được, khai một lần trong
`src/main.py`: `register_routes(app, package="cong_ty.dich_vu")`.

## Đóng góp

```bash
git clone <repo> && cd pymodular
pip install -e ".[all,dev]"
pym dev                        # chạy ứng dụng mẫu trong src/
pym test
pym lint pymodular app tests
```

Nhóm test cần hạ tầng thật chỉ chạy khi có biến môi trường tương ứng:

```bash
docker run -d -p 6379:6379 redis:7-alpine
TEST_REDIS_URL=redis://localhost:6379/0 pym test
```

Xem đầu mỗi file `tests/test_<tên>.py` để biết lệnh Docker và biến cần đặt.

## Giấy phép

MIT — xem [LICENSE](LICENSE).

## Tài liệu

- [docs/architecture.md](docs/architecture.md) — cấu trúc module, DI, đối chiếu NestJS
- [docs/config.md](docs/config.md) — Settings, thứ tự ưu tiên, thêm biến của riêng bạn
- [docs/database.md](docs/database.md) — memory / SQLite / PostgreSQL / MongoDB
- [docs/migrations.md](docs/migrations.md) — Alembic: sinh, chạy, lùi migration
- [docs/websocket.md](docs/websocket.md) — gateway WebSocket, phòng, Postman, Next.js
- [docs/rabbitmq.md](docs/rabbitmq.md) — exchange, topic, consumer nền, `.retry` / `.dlq`
- [docs/redis.md](docs/redis.md) — cache, đếm nguyên tử, pub/sub
- [docs/mqtt.md](docs/mqtt.md) — QoS, retain, luật khớp topic `+` và `#`
- [docs/kafka.md](docs/kafka.md) — nhóm consumer, phân vùng, `.dlt`
- [docs/operations.md](docs/operations.md) — guard, circuit breaker, metrics, trace
