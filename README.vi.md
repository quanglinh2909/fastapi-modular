> [English](https://github.com/quanglinh2909/fastapi-modular/blob/main/README.md) · **Tiếng Việt**

# fastapi-modular

FastAPI theo kiến trúc module kiểu NestJS: DI container, controller dạng class,
repository chung cho nhiều loại database, gateway WebSocket có phòng, và bốn lớp
hạ tầng tuỳ chọn: RabbitMQ, Redis, MQTT, Kafka.

Nếu bạn từng viết NestJS và ước FastAPI có sẵn cấu trúc đó — module tự đăng ký,
service `@Injectable`, controller dạng class, `@WebSocketGateway`,
`@EventPattern` — thì đây đúng là thứ đó, viết bằng Python.

```bash
pip install fastapi-modular
fam init && fam dev
```

## Từ NestJS sang

| NestJS | fastapi-modular |
|---|---|
| `@Module()` + module scanning | một thư mục dưới `src/api/`, tự quét |
| `@Controller('users')` | `@controller(prefix="/users", tags=["users"])` |
| `@Get()` `@Post()` `@Patch()` `@Delete()` | `@get()` `@post()` `@patch()` `@delete()` |
| `@Injectable()` | `@injectable` |
| `@Injectable({scope: Scope.REQUEST})` | `@injectable(scope=Scope.REQUEST)` |
| `forwardRef(() => X)` | `Lazy[X]` |
| `@InjectRepository(X) repo: Repository<X>` | `repo: Repository[X]` |
| `@UseGuards()` | `guards=[...]` ở controller hoặc từng route |
| `@WebSocketGateway()` | `@gateway(path="/ws/…")` |
| `@SubscribeMessage('x')` | `@subscribe("x")` |
| `@EventPattern('x')` (RabbitMQ) | `@rabbitmq_subscriber("events", "x", queue="…")` |
| `CacheModule` / `CACHE_MANAGER` | `RedisClient.cached(key, factory, ttl=…)` |
| socket.io Redis adapter | `APP_WS__ADAPTER=redis` |

Bảng đối chiếu đầy đủ ở [docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md).

## Bắt đầu

Cần **Python 3.10+**.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install fastapi-modular

fam init          # đổ file vào THƯ MỤC HIỆN TẠI, không tạo thêm cấp
fam dev
```

Mở http://localhost:8000/docs — đã có sẵn một module `health` chạy được.

`fam init` lấy tên dự án theo **tên thư mục hiện tại**; đặt tên khác bằng
`fam init --name ten-khac`. Nó không ghi đè file nào đã có, nên chạy được cả
trong thư mục đang có sẵn code. Muốn nó tự tạo thư mục thì `fam new <tên>`.

Lõi **không kéo theo** driver database hay client hàng đợi nào. Cần cái gì thì
thêm cái đó:

```bash
fam install sqlite      # hoặc postgres, mongodb
fam install rabbitmq    # hoặc redis, mqtt, kafka
fam install all         # tất cả
```

`fam install` vừa cài thư viện vừa ghi biến vào `.env`. Muốn tự cài bằng pip
cũng được: `pip install "fastapi-modular[sqlite,rabbitmq]"`.

## Lệnh

Một chương trình, hai tên: `fastapi-modular` (đầy đủ) và `fam` (gõ tắt). Dưới đây dùng
`fam` cho gọn.

Tên lệnh rút gọn được tới khi nào tiền tố còn chỉ đúng một lệnh — `fam mo alerts`
chạy y hệt `fam module alerts`. Nhập nhằng thì `fam` hỏi lại chứ không đoán:

```
$ fam m
fam: lệnh 'm' chưa rõ — khớp với migrate, module. Gõ thêm vài chữ cho rõ.
```

| Lệnh | Rút gọn | Làm gì |
|---|---|---|
| `fam init [--name <tên>]` | `fam ini` | dựng dự án **trong thư mục hiện tại**, không ghi đè file nào đã có; tên dự án mặc định lấy theo tên thư mục |
| `fam new <tên>` | `fam n` | dựng dự án trong một thư mục mới |
| `fam dev` | `fam d` | chạy kèm autoreload |
| `fam run --workers 4` | `fam r` | chạy chế độ production |
| `fam module <tên>` | `fam mo` | sinh module: controller + service + dto + entity |
| `fam module <tên> --gateway` | | kèm gateway WebSocket (`--consumer` cho RabbitMQ) |
| `fam module <tên> --gateway-only` | | chỉ thêm gateway vào module **đã có** (`--consumer-only` cho RabbitMQ) |
| `fam module <tên> --entity <Tên>` | | đặt tên lớp entity; mặc định đoán từ tên module |
| `fam provider <họ> <tên>` | `fam pr` | sinh provider cắm được: interface năng lực + khung hiện thực |
| `fam env <thành-phần>` | `fam e` | chỉ ghi biến cấu hình vào `.env` (không cài gì) |
| `fam clean` | `fam c` | xoá cache và bản dựng (không đụng `data/`) |
| `fam build` · `fam publish [--test]` | `fam b` · `fam pu` | dựng wheel/sdist · đẩy lên PyPI |
| `fam info` | `fam inf` | đang nối vào đâu, thư viện nào đã cài, cảnh báo cấu hình prod |
| `fam migrate [up\|down\|history\|sql\|create]` | `fam mi` | Alembic |
| `fam test` · `fam lint [--fix]` | `fam t` · `fam l` | pytest · ruff. `fam lint` không tham số soi `src`; truyền đường dẫn để soi chỗ khác |
| **Thêm database** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `fam install sqlite` | `fam ins s` | file `.db`, không cần server |
| `fam install postgres` | `fam ins p` | PostgreSQL |
| `fam install mongodb` | `fam ins mo` | MongoDB |
| **Thêm hàng đợi** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `fam install rabbitmq` | `fam ins ra` | 5 kiểu exchange, hàng đợi bền, TTL, thử lại + DLQ |
| `fam install redis` | `fam ins re` | cache, đếm nguyên tử, pub/sub |
| `fam install mqtt` | `fam ins mq` | thiết bị IoT |
| `fam install kafka` | `fam ins k` | nhật ký sự kiện đọc lại được |
| `fam install ws-redis` | `fam ins w` | phát tin WebSocket xuyên nhiều worker |
| `fam install dev` | `fam ins d` | pytest · pytest-asyncio · httpx · ruff — cần cho `fam test` / `fam lint` |
| `fam install all` | `fam ins a` | tất cả những thứ trên, **trừ** `dev` |

Tham số dạng danh sách cũng rút gọn theo cùng luật đó: `fam ins sq`,
`fam e post`, `fam mi h`. Còn giá trị bạn tự đặt thì không bị đụng tới —
`fam mo ins` tạo module tên đúng là `ins`.

Host và cổng lấy từ `APP_HOST` / `APP_PORT` trong `.env`, nên `fam dev` không cần
tham số. `fam --help` cho danh sách đầy đủ.

## Thêm module

```bash
fam module alerts              # controller + service + dto + entities
fam module alerts --gateway    # kèm gateway WebSocket
fam module alerts --consumer   # kèm consumer RabbitMQ
```

Route xuất hiện ngay, bảng được tạo ngay, validate chạy ngay — chỉ thân hàm là
chưa viết (gọi vào trả 501 kèm tên hàm). Việc của bạn: thêm trường vào entity và
DTO, rồi viết thân hàm trong service.

Không phải sửa file nào khác. Chi tiết: [docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md#thêm-module-mới).

## Chọn database

`fam install sqlite` (hoặc `postgres`, `mongodb`) làm cả hai việc: cài thư viện
của đúng driver đó, rồi ghi biến vào `.env`. Chỉ muốn ghi `.env` mà không cài gì
thì dùng `fam env sqlite`.

`fam env` ghi mỗi biến kèm giải thích, cho biết nó **bắt buộc hay tuỳ chọn** và
**mặc định là gì** nếu xoá dòng đi. `fam info` cho biết hiện đang nối vào đâu.

Chi tiết: [docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md).

## Cấu hình của riêng bạn

Kế thừa `Settings` là thêm được biến vào `.env`, không phải sửa gì trong khung:

```python
# src/core/config.py — fam init sinh sẵn file này
class AppSettings(Settings):
    team_name: str = Field(default="", alias="APP_TEAM_NAME")
    jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")   # -> APP_JWT__SECRET
```

Service nhận `AppSettings` qua DI với gợi ý kiểu đầy đủ.
Chi tiết: [docs/config.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/config.md).

## Điểm vào là file của bạn

`fam init` sinh ra `src/main.py` với từng bước lắp ráp bày ra hết — thêm
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
fam dev
# ws://localhost:8000/ws/chat?client_id=an

fam module alerts --gateway-only   # thêm gateway vào module đã có
fam install ws-redis               # bắt buộc khi chạy nhiều worker
```

Đẩy tin từ REST hay tác vụ nền: nhận `WebSocketServer` qua `__init__` rồi gọi
`to_room` / `to_user` / `to_socket`.

Hướng dẫn đầy đủ (kèm cách dùng bằng **Postman** và client **Next.js**):
[docs/websocket.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/websocket.md).

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

# Đủ 5 kiểu exchange: topic (mặc định), direct, fanout, headers, default
@rabbitmq_subscriber("cache-events", queue=f"xoa-cache-{HOSTNAME}", exchange_type="fanout")
# hostname trong tên hàng đợi -> mỗi worker một bản sao, thay vì chia lượt nhau
async def xoa_cache(self, payload: dict) -> None: ...

# Hạn dùng: cho một tin (ttl), cho cả hàng đợi (message_ttl), cho chính hàng đợi
await self._mq.publish("events", "xe.viTri", {"lat": 21.0}, ttl=5)
```

```bash
fam install rabbitmq            # cài aio-pika + ghi APP_RABBITMQ__* vào .env
fam module alerts --consumer    # module mới kèm consumer
```

Không cài, không bật thì mọi thứ chạy y như chưa từng có nó. Broker rớt thì app
vẫn phục vụ và tự nối lại. Chi tiết: [docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md).

## Redis, MQTT, Kafka (cũng tuỳ chọn)

Cùng một khuôn với RabbitMQ: một package riêng dưới `infrastructure/`, một nhóm
biến `APP_<TÊN>__*`, mặc định **tắt**, thư viện chỉ import khi bật, và luôn tự
nối lại.

```bash
fam install redis    # cache, đếm, pub/sub      -> docs/redis.md
fam install mqtt     # thiết bị IoT             -> docs/mqtt.md
fam install kafka    # nhật ký sự kiện          -> docs/kafka.md
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
fam migrate                           # chạy migration (SQL)
fam info                              # cấu hình đang dùng + cảnh báo prod
```

Chi tiết: [docs/operations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/operations.md).

## Cấu trúc repo này

```
fastapi_modular/          THƯ VIỆN — thứ được đóng gói và cài về
  core/             DI, controller, config, WebSocket, guard, số đo
  infrastructure/   database, rabbitmq, redis, mqtt, kafka (mỗi thứ một package)
  cli/              init · new · module · provider · dev · run · install · env
                    info · migrate · test · lint · clean · build · publish
  factory.py        create_app()
  discovery.py      tự quét package ứng dụng, dựng router
src/                ỨNG DỤNG MẪU — không nằm trong gói cài; xoá thoải mái
  main.py           điểm vào: lắp ráp app — file của bạn, không phải của khung
  core/config.py    AppSettings: kế thừa Settings để thêm biến .env của bạn
  core/lifespan.py  việc lúc khởi động / lúc tắt của riêng ứng dụng
  api/              các module nghiệp vụ; mỗi thư mục con là một module
tests/              391 test chạy không cần hạ tầng, 46 test nữa bật khi có server thật
docs/               tài liệu tra cứu
```

`fastapi_modular/` không import gì từ `src/`. Nó chỉ biết "có một package tên
`src.api`, quét nó đi" — nên dự án xếp khác cũng được, khai một lần trong
`src/main.py`: `register_routes(app, package="cong_ty.dich_vu")`.

## Đóng góp

```bash
git clone <repo> && cd fastapi-modular
pip install -e ".[all,dev]"
fam dev                        # chạy ứng dụng mẫu trong src/
fam test
fam lint fastapi_modular src tests
```

Nhóm test cần hạ tầng thật chỉ chạy khi có biến môi trường tương ứng:

```bash
docker run -d -p 6379:6379 redis:7-alpine
TEST_REDIS_URL=redis://localhost:6379/0 fam test
```

Xem đầu mỗi file `tests/test_<tên>.py` để biết lệnh Docker và biến cần đặt.

## Giấy phép

MIT — xem [LICENSE](https://github.com/quanglinh2909/fastapi-modular/blob/main/LICENSE).

## Tài liệu

- [docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md) — cấu trúc module, DI, đối chiếu NestJS
- [docs/config.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/config.md) — Settings, thứ tự ưu tiên, thêm biến của riêng bạn
- [docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md) — memory / SQLite / PostgreSQL / MongoDB
- [docs/migrations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/migrations.md) — Alembic: sinh, chạy, lùi migration
- [docs/websocket.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/websocket.md) — gateway WebSocket, phòng, Postman, Next.js
- [docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md) — đủ 5 kiểu exchange, hạn dùng (TTL), consumer nền, `.retry` / `.dlq`
- [docs/redis.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/redis.md) — cache, đếm nguyên tử, pub/sub
- [docs/mqtt.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mqtt.md) — QoS, retain, luật khớp topic `+` và `#`
- [docs/kafka.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/kafka.md) — nhóm consumer, phân vùng, `.dlt`
- [docs/providers.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/providers.md) — provider cắm được: chọn bản hiện thực bằng tên lúc chạy
- [docs/operations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/operations.md) — guard, circuit breaker, metrics, trace
