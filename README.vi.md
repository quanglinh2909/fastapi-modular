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
| `@Get()` `@Post()` `@Put()` `@Patch()` `@Delete()` | `@get()` `@post()` `@put()` `@patch()` `@delete()` |
| handler đồng bộ chạy ngay luồng chính (Nest) | `def` thường chạy ở thread pool, `async def` chạy trên vòng lặp — đúng luật FastAPI |
| `@Injectable()` | `@injectable` |
| `@Injectable({scope: Scope.REQUEST})` | `@injectable(scope=Scope.REQUEST)` |
| `forwardRef(() => X)` | `Lazy[X]` |
| `@InjectRepository(X) repo: Repository<X>` | `repo: Repository[X]` |
| `@Transaction()` / `queryRunner.startTransaction()` | `async with db.transaction():` — khối lồng nhau thành SAVEPOINT |
| `queryRunner.rollbackTransaction()` | tự động khi có exception; `await tx.rollback()` để huỷ mà không ném lỗi |
| `repo.update(criteria, partialEntity)` (TypeORM) | `repo.update("cam-01", payload)` — nhận thẳng DTO, **trả về bản ghi đã sửa** trong 1 câu SQL; `repo.update_where({"zone": "T1"}, status="off")` cho nhiều dòng |
| `repo.createQueryBuilder()` (TypeORM) | `repo.query().join(X).where(Event.score >= …)` — SQL thật, xem bằng `.sql()` |
| `Repository.find({where: {score: MoreThan(…)}})` (TypeORM) | `class Event(Entity)` rồi `.where(Event.score >= …)`, hoặc `.where(score__gte=…)` |
| `.groupBy().having()` (TypeORM) | `.group_by(Event.camera_id).select(n=count()).having(count() > 5)` |
| `.leftJoin()` / `.orWhere()` (TypeORM) | `.left_join(X)` / `.or_where(…)` — mỗi kiểu nối một method |
| `.orderBy('x', 'DESC')` (TypeORM) | `.order_by_desc("x")` — chiều nằm trong tên hàm |
| `Like()` / `In()` / `IsNull()` (TypeORM) | `.like(X.name, "a%")` · `.in_(X.zone, [...])` · `.is_null(X.ip)` — ngay trên builder |
| `select([...])` / `AS` (TypeORM) | `.select(fields=…, exclude=…, rename={"tên mới": "cột"})` — dùng chung tên với `include` |
| `addSelect()` (TypeORM) | `.select(add={"ten_camera": Camera.name})` — giữ đủ cột, thêm một cột |
| `find({relations: {events: true}})` (TypeORM) | `.include(Event)` — dữ liệu lồng nhau, thêm ĐÚNG một câu lệnh |
| `relations: {camera: {logs: {items: true}}}` (TypeORM) | `.nest_under(Camera, CameraLog, ItemLog)` — mỗi mức một câu lệnh |
| *(TypeORM không có)* | `.nest_under(Camera)` — lọc theo sự kiện, nhận về camera kèm sự kiện bên trong |
| `@ManyToOne(…, {onDelete: 'CASCADE'})` (TypeORM) | `field(metadata=reference(Camera, on_delete="CASCADE"))` — khoá ngoại THẬT dưới database |
| `@Column({length: 50})` / `@Column({type: 'text'})` (TypeORM) | `field(metadata=column(length=50))` / `column(text=True)` — chặn ngay lúc ghi, trên mọi backend |
| `@UseGuards()` | `guards=[...]` ở controller hoặc từng route |
| `@WebSocketGateway()` | `@gateway(path="/ws/…")` |
| `@SubscribeMessage('x')` | `@subscribe("x")` |
| `@EventPattern('x')` (RabbitMQ) | `@rabbitmq_subscriber("events", "x", queue="…")` |
| `@MessagePattern('x')` | `@rabbitmq_responder("x", queue="…")` — giá trị trả về được gửi ngược |
| `@Interval()` / `@Cron()` / `@Timeout()` | `@interval(seconds=5)` / `@cron("0 3 * * *")` / `@timeout(seconds=10)` |
| `@OnEvent('x')` + `EventEmitter2` | `@on_event("x")` + `EventBus.emit()` — fanout trong tiến trình |
| `client.emit(p, d)` / `client.send(p, d)` | `broker.emit(p, d, queue=…)` / `await broker.send(p, d, queue=…)` |
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

`fam install` làm ba việc: cài thư viện, ghi biến vào `.env`, và **ghi nhớ thành
phần vào `requirements.txt`** để đồng nghiệp clone repo về chỉ cần
`pip install -r requirements.txt` — đúng việc `package.json` làm cho `npm i`.

```
# requirements.txt, sau khi `fam install sqlite` rồi `fam install redis`
fastapi-modular[redis,sqlite]>=0.3.1
```

Nó ghi extras chứ không liệt kê từng gói con: khoảng phiên bản của
`sqlalchemy`, `motor`... là chuyện của fastapi-modular và đổi theo từng bản, nên
chép phẳng ra là đóng băng một bản chụp sẽ lạc hậu trong im lặng. Dự án dùng
`pyproject.toml` mà đã khai fastapi-modular trong đó thì lệnh sửa ngay dòng ấy,
không đẻ thêm `requirements.txt`. `fam install dev` đi vào `requirements-dev.txt`
— production không việc gì phải cài pytest.

Muốn tự cài bằng pip cũng được: `pip install "fastapi-modular[sqlite,rabbitmq]"`.

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
| `fam run` | `fam r` | chạy chế độ production (1 tiến trình; `--workers 4` để nhiều hơn) |
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

Chi tiết: [docs/entity.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/entity.md) · [docs/repository.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/repository.md) · [docs/query.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/query.md) · [docs/mongodb.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mongodb.md).

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

## Việc chạy nền

Bốn thứ khác nhau, không cần hạ tầng gì:

```python
# theo LỊCH — @nestjs/schedule
@interval(seconds=5)
async def cap_nhat_camera(self) -> None: ...

@cron("0 3 * * *", timezone="Asia/Ho_Chi_Minh")     # mặc định là UTC!
async def don_log(self) -> None: ...

# theo YÊU CẦU — hàng đợi asyncio.Queue trong tiến trình, xử lý tuần tự
@job("detect", thread=True)            # thread: chạy trong thread cho YOLO
def nhan_dang(self, payload: dict, ctx: WorkerContext) -> None:
    ctx.run(self._db.save(...))        # ghi database từ trong thread

await self._jobs.submit("detect", {"path": p})      # trả về ngay

# VÒNG LẶP SỐNG MÃI — N bản, mỗi camera một khoá
@worker("camera")
async def watch(self, data: dict, ctx: WorkerContext) -> None:
    cap = await ctx.blocking(cv2.VideoCapture, data["ip"])   # dựng, NGOÀI vòng lặp
    while ctx.running:
        frame = await ctx.blocking(cap.read)                 # hàm chặn -> thread khác
        await self._db.save(...)                             # await thẳng

for camera in cameras:
    await service.watch(camera.id, {"ip": camera.ip})   # khoá + data lúc gọi

await self.watch.stop(camera.id)       # dừng MỘT bản, chờ nó dọn dẹp xong

# FANOUT trong tiến trình — một sự kiện, N nơi nghe, chạy SONG SONG
@on_event("order.paid")                       # nhận cả "order.*" / "camera.#"
async def gui_bien_lai(self, data: dict) -> None: ...

@on_event("order.paid")                       # nơi nghe thứ hai là chuyện bình thường
async def cap_nhat_thong_ke(self, data: dict) -> None: ...

await self._events.emit("order.paid", {"id": id})   # CHỜ mọi nơi nghe xong
self._events.dispatch("order.paid", {"id": id})     # trả về NGAY
```

Cả năm decorator đều có hai dạng: `async def` (mặc định) và `thread=True` cho
thân hàm toàn lời gọi chặn. `ctx` là tuỳ chọn — khai khi cần `ctx.running` để
dừng vòng lặp, `ctx.blocking(...)` để gọi hàm chặn, hoặc `ctx.run(...)` để ghi
database từ trong thread.

`@on_event` là chỗ `@job` không với tới: `@job` là một tên việc, **một**
handler, xếp hàng chạy tuần tự — đó là hàng đợi. `@on_event` là một sự kiện,
**nhiều** handler, chạy song song — không ai "sở hữu" việc, và bên phát không
biết ai đang nghe. Một nơi nghe ném lỗi thì những nơi khác vẫn chạy. Nó là
`fanout` / `EventEmitter`, nhưng **chỉ trong một tiến trình**: `fam run
--workers 4` thì sự kiện không sang được ba tiến trình kia.

`@worker` là chỗ `@interval` và `@job` không với tới: nó có phần **dựng ở trước
vòng lặp** (mở camera, nạp model) và chạy tới khi bạn bảo dừng. Hỏng thì tự dựng
lại, chờ tăng dần. Gọi lại cùng `key` không mở thêm bản.

`stop()` chờ `finally:` của vòng lặp chạy xong, nên dòng viết sau nó chạy khi
camera đã đóng — tài nguyên dọn trong `finally:`, việc nghiệp vụ dọn sau lời gọi.

Viết `while ctx.running:`, đừng viết `while True:` — vòng lặp không kiểm cờ dừng
làm Ctrl+C trông như chết suốt cả thời gian chờ tắt. Khung kêu ngay lúc khởi
động (`worker.endless_loop`) chứ không để bạn phát hiện lúc 2 giờ sáng.

`fam run --workers 4` chạy 4 tiến trình, nên một vòng `while True: sleep(5)` viết tay sẽ chạy
**bốn lần**. Mặc định `single=True` khoá lại: đo được 5 lượt / 1 tiến trình,
so với 20 lượt / 4 tiến trình khi tắt khoá. Khoá dùng `flock` (một máy) hoặc
Redis (nhiều máy), tự chọn.

Hàng đợi `@job` nằm trong RAM — **app tắt là mất phần chưa chạy**, và khung nói
thẳng con số đó ra lúc tắt. Việc không được phép mất thì dùng `@rabbitmq_subscriber`.
Chi tiết: [docs/background.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/background.md).

## Gửi rồi chờ trả lời — khuôn tin của NestJS

`publish`/`emit` bắn đi rồi quên. `send` thì **chờ trả lời** — đúng cặp
`client.send()` / `@MessagePattern()` của NestJS, cùng một khuôn tin:

```python
# bên trả lời — service bình thường, chỉ khác là nó `return`
@rabbitmq_responder("sum", queue="math")
async def cong(self, data: list[int]) -> int:
    return sum(data)

# bên gọi
tong = await self._mq.send("sum", [1, 2, 3, 4], queue="math")   # -> 10
```

Có ở **cả bốn hạ tầng**: `@rabbitmq_responder`, `@redis_responder`,
`@mqtt_responder`, `@kafka_responder`.

Khuôn gói tin lấy từ chính mã nguồn `@nestjs/microservices`, nên một
microservice NestJS và một service viết bằng khung này nói chuyện được với nhau
mà không cần lớp dịch — đã chạy thật hai chiều, trên cả bốn hạ tầng, với NestJS
11.2.1, kể cả pattern dạng object (`{"cmd": "sum"}`) và luật sắp xếp khoá.

`send` biến hàng đợi thành lời gọi qua mạng, kéo theo đúng những thứ hàng đợi
vốn dựng lên để tránh — [docs/rpc.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rpc.md)
nói rõ khi nào KHÔNG nên dùng.

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
tests/              1149 test chạy không cần hạ tầng, 376 test nữa cần driver/server thật
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
- [docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md) — SQL: chọn driver (memory / SQLite / PostgreSQL), kết nối, tự chỉnh schema
- [docs/entity.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/entity.md) — khai bảng: `@entity`, khoá ngoại + `on_delete`, unique/index, độ dài cột chữ
- [docs/repository.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/repository.md) — đọc/ghi trong service: `find`, `save`, `update`, `delete`
- [docs/query.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/query.md) — query builder: JOIN, lớn/bé, NULL, gộp nhóm, dữ liệu lồng nhau
- [docs/transaction.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/transaction.md) — ghi nhiều bảng: cùng thành công hoặc cùng không
- [docs/mongodb.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mongodb.md) — MongoDB: truy vấn, dữ liệu lồng nhau, và những thứ bên đó không có (không JOIN, không transaction)
- [docs/migrations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/migrations.md) — Alembic: sinh, chạy, lùi migration
- [docs/websocket.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/websocket.md) — gateway WebSocket, phòng, Postman, Next.js
- [docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md) — đủ 5 kiểu exchange, hạn dùng (TTL), consumer nền, `.retry` / `.dlq`
- [docs/background.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/background.md) — việc theo lịch (`@interval`/`@cron`/`@timeout`), hàng đợi việc trong tiến trình (`@job`), vòng lặp sống mãi (`@worker`) và fanout trong tiến trình (`@on_event`)
- [docs/rpc.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rpc.md) — `emit` / `send` / `@rabbitmq_responder`, khuôn tin tương thích NestJS
- [docs/redis.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/redis.md) — cache, đếm nguyên tử, pub/sub
- [docs/mqtt.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mqtt.md) — QoS, retain, luật khớp topic `+` và `#`
- [docs/kafka.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/kafka.md) — nhóm consumer, phân vùng, `.dlt`
- [docs/providers.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/providers.md) — provider cắm được: chọn bản hiện thực bằng tên lúc chạy
- [docs/operations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/operations.md) — guard, circuit breaker, metrics, trace
