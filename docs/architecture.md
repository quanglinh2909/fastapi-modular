# Kiến trúc

Cấu trúc theo module giống NestJS: mỗi thư mục con của `src/api/` là một module
tự chứa, tự khai báo route/service/repository/DTO của mình.

```
fastapi_modular/                     THƯ VIỆN — thứ được đóng gói, cài bằng pip
├── factory.py                 create_app() — không biết module nào cụ thể
├── discovery.py               tự quét package ứng dụng, dựng router
├── core/                      hạ tầng dùng chung
│   ├── config.py              Settings (pydantic-settings) + cảnh báo cấu hình prod
│   ├── container.py           DI container: @injectable, Lazy[X], Scope.REQUEST
│   ├── controller.py          @controller / @get / @post — controller dạng class
│   ├── exceptions.py          cây lỗi nghiệp vụ (AppError, NotFoundError, ...)
│   ├── error_handlers.py      dịch lỗi thành JSON chuẩn
│   ├── logging.py             structlog
│   ├── context.py             request-id theo contextvar
│   ├── clock.py               utcnow()
│   ├── guards.py              Guard + Principal (dùng chung cho HTTP và WebSocket)
│   ├── lifespan.py            mở/đóng database và hạ tầng (ứng dụng BỌC lại, không sửa)
│   ├── schemas.py             Page[T]
│   ├── rpc.py                 khuôn tin emit/send tương thích NestJS (dùng chung mọi hạ tầng)
│   ├── scheduler.py           @interval / @cron / @timeout + SchedulerRunner
│   ├── cron.py                đọc biểu thức cron 5 trường
│   ├── jobs.py                @job + JobQueue: hàng đợi việc trong tiến trình
│   ├── workers.py             @worker: vòng lặp sống mãi, N bản theo tham số
│   ├── events.py              @on_event + EventBus: fanout trong tiến trình
│   ├── locks.py               khoá "chỉ một tiến trình chạy" (flock hoặc Redis)
│   └── websocket/             @gateway / @subscribe, phòng, adapter nhiều worker
├── infrastructure/            mỗi hạ tầng MỘT package, không biết nhau
│   ├── database/              base · memory · sql · mongo · factory · repository · query (builder)
│   ├── rabbitmq/              (tuỳ chọn) broker · consumers · responders · patterns
│   ├── redis/                 (tuỳ chọn) client (cache, đếm) · pubsub
│   ├── mqtt/                  (tuỳ chọn) client · consumers · patterns (+ và #)
│   └── kafka/                 (tuỳ chọn) broker · consumers
├── middleware/                request-id, access log (ASGI thuần)
└── cli/                       init · new · module · provider · dev · run · install · env
                               info · migrate · test · lint · clean · build · publish

src/                           ỨNG DỤNG — code của bạn, KHÔNG nằm trong gói cài
├── main.py                    lắp ráp app: settings, middleware, route — sửa thoải mái
├── core/
│   ├── config.py              AppSettings — thêm biến .env của riêng bạn
│   └── lifespan.py            việc lúc khởi động / lúc tắt của riêng bạn
├── providers/                 provider cắm được, mỗi họ một thư mục (tuỳ chọn)
└── api/                       mỗi thư mục con là một module
    ├── health/
    ├── users/
    │   ├── user_controller.py @controller — HTTP
    │   ├── user_service.py    @injectable — business rule
    │   ├── dto/               DTO vào/ra
    │   └── entities/          dataclass entity
    ├── devices/
    └── chat/
        ├── chat_gateway.py    @gateway — WebSocket
        └── chat_controller.py @controller — REST đẩy tin xuống WebSocket
```

Ranh giới giữa hai khối là điều quan trọng nhất trong sơ đồ trên: `fastapi_modular/`
không import bất cứ thứ gì từ `src/`. Nó chỉ biết "có một package tên `src.api`,
quét nó đi". Xếp khác thì nói ra một lần trong `src/main.py`:
`register_routes(app, package="cong_ty.dich_vu")`.

## Đối chiếu với NestJS

| NestJS | fastapi-modular |
|---|---|
| `@Module()` + module scanning | thư mục dưới `src/api/`, tự quét ở `fastapi_modular/discovery.py` |
| `@UseGuards()` | `guards=[...]` ở `@controller` hoặc từng route |
| `@Controller('users')` | `@controller(prefix="/users", tags=["users"])` |
| `@Get()` `@Post()` `@Patch()` `@Delete()` | `@get()` `@post()` `@patch()` `@delete()` |
| `@Injectable()` | `@injectable` |
| `@Injectable({scope: Scope.REQUEST})` | `@injectable(scope=Scope.REQUEST)` |
| `forwardRef(() => X)` | `Lazy[X]` |
| `@InjectRepository(X) repo: Repository<X>` | `repo: Repository[X]` |
| `repo.createQueryBuilder()` (TypeORM) | `repo.query()` — `join`/`left_join`/`right_join`/`outer_join`, `where`/`or_where`, `group_by`/`having`; xem [database.md](database.md#truy-vấn-phức-tạp--join-lớnbé-null) |
| `find({relations: …})` (TypeORM) | `.include(Event, fields=…, exclude=…)` · `.nest_under(Camera)` — dữ liệu lồng nhau, hai chiều |
| `@ManyToOne(…, {onDelete})` (TypeORM) | `reference(X, on_delete=…)` — xem [database.md](database.md#khoá-ngoại-nối-hai-bảng-với-nhau) |
| `@Entity()` | `@entity` |
| `extends BaseEntity` | `class X(Entity)` — chỉ để lọc bằng toán tử: `.where(X.score >= 0.8)` |
| `overrideProvider()` | `container.override()` |
| `@WebSocketGateway()` | `@gateway(path="/ws/…")` |
| `@SubscribeMessage('x')` | `@subscribe("x")` |
| `@WebSocketServer() server` | `server: WebSocketServer` qua `__init__` |
| `handleConnection` / `handleDisconnect` | `on_connect` / `on_disconnect` |
| `client.join(room)` / `server.to(room).emit()` | `socket.join(room)` / `server.to_room(room, ...)` |
| `@nestjs/platform-socket.io` Redis adapter | `APP_WS__ADAPTER=redis` |
| Hai gói `websockets` / `microservices` độc lập | `core/websocket` không biết `infrastructure/rabbitmq` |
| `Transport.RMQ` là một lựa chọn riêng | mỗi hạ tầng một package riêng dưới `infrastructure/` |
| `@EventPattern('x')` (microservices) | `@rabbitmq_subscriber("events", "x", queue="...")` |
| `@MessagePattern('x')` | `@rabbitmq_responder("x", queue="...")` — xem [rpc.md](rpc.md) |
| `@Interval` / `@Cron` / `@Timeout` (`@nestjs/schedule`) | `@interval` / `@cron` / `@timeout` — xem [background.md](background.md) |
| `@OnEvent('x')` + `EventEmitter2` (`@nestjs/event-emitter`) | `@on_event("x")` + `EventBus.emit()` — fanout trong tiến trình |
| `ClientProxy.emit/send` | `broker.emit(...)` / `await broker.send(...)`, khuôn tin tương thích NestJS |
| `@EventPattern('x')` (transport Kafka) | `@kafka_subscriber("x", group="...")` |
| `@EventPattern('x')` (transport MQTT) | `@mqtt_subscriber("x/+/y", qos=1)` |
| `@EventPattern('x')` (transport Redis) | `@redis_subscriber("x:*")` |
| `CacheModule` / `@Inject(CACHE_MANAGER)` | `RedisClient.cached(key, factory, ttl=...)` |
| `ClientProxy.emit()` | `broker.publish(exchange, routing_key, data)` |

## Năm quy tắc

**1. Module chỉ lộ ra controller.** Module khác muốn dùng thì đi qua service, không
đụng thẳng repository.

**2. Route thuộc về module sở hữu dữ liệu trả về, không phải module trùng URL.**
`GET /api/users/{owner_id}/devices` do module `devices` khai báo, vì nó trả về
thiết bị. Khai báo bên `users` sẽ tạo vòng tròn import.

**3. Service ném lỗi nghiệp vụ, không ném HTTP.** `NotFoundError` / `ConflictError`
từ `core/exceptions.py`; `error_handlers` dịch sang status code. Nhờ vậy service
tái dùng được ngoài HTTP (worker, CLI, gRPC).

**4. Mỗi hạ tầng một package, không có ô chung.** `database/`, `rabbitmq/`,
`redis/`, `mqtt/`, `kafka/` — mỗi thứ một thư mục, một nhóm biến `APP_<TÊN>__*`,
một `fam env <tên>`, và thư viện chỉ được import khi bật. Không nhét chung
vào một gói "messaging". Chúng không biết nhau, và `core/` không biết
chúng: `core/websocket/` không có một chữ nào là `exchange`, `queue` hay
`broker`. Muốn nối hai cơ chế thì viết ở tầng ứng dụng, đúng cách NestJS bắt
bạn tự nối `@nestjs/websockets` với `@nestjs/microservices`.

**5. Phụ thuộc hai chiều cắt bằng `Lazy[...]` ở một phía.** `DeviceService` phụ
thuộc `UserService` bình thường; `UserService` phụ thuộc `Lazy[DeviceService]`.
Import thật đặt trong `if TYPE_CHECKING` nên runtime không có vòng tròn, mà IDE
vẫn gợi ý được method.

## DTO

Ba DTO của cùng một entity để chung một file (`dto/user_dto.py`) vì chúng kế
thừa lẫn nhau; tách theo thao tác chỉ tạo import chéo. Module nhiều entity thì
tách theo **entity**, không theo thao tác.

```python
class UserBase(InputSchema):          # field ghi được, khai báo MỘT lần
    email: str = Field(pattern=EMAIL_PATTERN)
    full_name: str = Field(min_length=1, max_length=100)

class UserCreate(UserBase):           # POST: mọi field bắt buộc
    pass

class UserUpdate(partial_of(UserBase)):   # PATCH: mọi field optional, ràng buộc giữ nguyên
    is_active: bool | None = None         # field chỉ sửa được, không đặt lúc tạo

class UserOut(OutputSchema):          # liệt kê tường minh, không sinh từ entity
    ...
```

| Lớp nền (`core/schemas.py`) | Cho | Đặt sẵn |
|---|---|---|
| `InputSchema` | body request | `extra="forbid"`, cắt khoảng trắng thừa |
| `OutputSchema` | response | `from_attributes=True` |
| `partial_of(M)` | biến thể PATCH | mọi field optional, **giữ nguyên** pattern/min_length/ge/le |
| `apply_changes(entity, dto)` | áp PATCH | chỉ chép field client thực sự gửi (`exclude_unset`) |

Sinh bản PATCH thay vì chép tay là để hai bản **không thể lệch nhau**: sửa
`min_length` ở `UserBase` là bản PATCH đổi theo.

`UserOut` cố ý liệt kê tường minh chứ không sinh từ entity — entity về sau có
thể thêm trường nội bộ (mật khẩu băm, cờ hệ thống) mà không được lộ ra API.

## Thêm module mới

```bash
fam module alerts            # entity đoán là Alert
fam module people --entity person   # đè khi đoán sai
fam module alerts --gateway       # kèm gateway WebSocket
fam module alerts --consumer       # kèm consumer RabbitMQ
fam module alerts --gateway-only           # thêm gateway vào module đã có
fam module alerts --consumer-only          # thêm consumer vào module đã có
```

Sinh ra đúng cấu trúc của các module có sẵn:

```
src/api/alerts/
├── __init__.py
├── alert_controller.py  @controller + 5 route CRUD, đã nối DI
├── alert_service.py     @injectable + 5 method, thân để trống kèm TODO
├── dto/__init__.py
├── dto/alert_dto.py     AlertBase / AlertCreate / AlertUpdate / AlertOut
├── entities/__init__.py
└── entities/alert_model.py   @entity dataclass kế thừa Entity: id, created_at, updated_at
```

Khởi động lại là route đã có trong OpenAPI, bảng đã được tạo, validate đã chạy —
chỉ thân hàm là chưa viết, gọi vào trả **501** kèm tên hàm:

```json
{"code": "not_implemented", "message": "AlertService.list_alerts chưa được viết"}
```

501 chứ không phải 500, để phân biệt "chưa viết" với "có bug".

Ba việc còn lại của bạn:

1. Thêm trường vào `entities/<tên>_model.py` và `dto/<tên>_schema.py`
2. Khai `unique=` / `indexes=` trong `@entity` nếu cần
3. Viết thân hàm trong `<tên>_service.py` — mỗi hàm có sẵn gợi ý dạng chú thích

Không phải đăng ký ở đâu cả: không sửa `main.py`, không sửa `api/app.py`.

Thêm `--gateway` thì có thêm `<tên>_gateway.py` và `dto/<tên>_ws_dto.py`: gateway
WebSocket đã nối DI, có hook vòng đời và hai handler mẫu — cũng để trống thân,
gọi vào trả khung `error` mang code `not_implemented`. Xem
[websocket.md](websocket.md).

### Viết tay thay vì dùng lệnh

Tạo thư mục `src/api/<tên>/` với `__init__.py` (chỉ docstring) và một file controller:

```python
@controller(prefix="/alerts", tags=["alerts"])
class AlertController:
    def __init__(self, service: AlertService) -> None:
        self._service = service

    @get("", response_model=Page[AlertOut])
    async def list_alerts(self, limit: Annotated[int, Query(le=100)] = 20) -> Page[AlertOut]:
        ...
```

Không phải sửa `main.py`, không phải sửa `api/app.py`, không phải export biến
`router`. Khởi động lại là thấy route.

Hai lỗi hay gặp, cả hai đều được log cảnh báo lúc boot:

- `api.module_without_controller` — thư mục không có `@controller` nào.
- `controller.no_routes` — có controller nhưng chưa method nào mang `@get`/`@post`.

## Thứ tự route

Route khớp theo thứ tự đăng ký, mà thứ tự đó là thứ tự khai báo method trong class.
Route tĩnh phải viết **trước** route động:

```python
@get("/me")            # đúng: /users/me khai báo trước
async def me(self): ...

@get("/{user_id}")     # nếu đảo lại, "me" sẽ bị nuốt thành user_id
async def detail(self, user_id: str): ...
```

## Hiệu năng

Đo trên máy phát triển:

| | |
|---|---|
| `container.resolve` lúc nóng | ~97 ns (tra dict) |
| `Lazy` proxy | +217 ns mỗi lần gọi |
| Lớp `@controller` so với hàm thuần cùng độ sâu router | trong biên độ nhiễu |
| Boot (quét module + dựng route) | ~220 ms |

Phần phụ trội đo được đến từ việc FastAPI khớp route qua router lồng nhau
(~15–23 µs/request), mà bất kỳ kiến trúc module nào cũng phải trả. So với một
truy vấn database thật (1–10 ms) thì dưới 2%.

## Chất lượng mã

```bash
fam lint                      # ruff trên `src` (mặc định): F, E, W, I, B, UP, SIM, RUF, BLE
fam lint fastapi_modular src tests  # soi cả thư viện và test — dùng cái này khi phát triển repo
fam lint --fix                # tự sửa phần sửa được
fam test       # 1048 test trên backend memory (277 test nữa cần hạ tầng hoặc driver thật)
```

Cấu hình ở [`ruff.toml`](../ruff.toml). Rule `BLE` được bật có chủ ý: mỗi
`except Exception` trần phải kèm `# noqa: BLE001` giải thích vì sao ở đó nuốt
lỗi là đúng.

## Còn thiếu

- **Xác thực thật** — khung guard đã có ([operations.md](operations.md#guard)),
  nhưng chưa có JWT/API key. Chỗ cắm: một guard gọi `principal.assume(...)`.
- **Rate limiting** — viết được dưới dạng guard.
- **Cache phân tán** — có rồi: `RedisClient.cached()`, xem [redis.md](redis.md).
  Chưa có là cache trong RAM tiến trình và decorator `@cache` ở tầng route.
- **Lưu lịch sử tin realtime** — WebSocket ở đây là kênh truyền, không phải hộp
  thư: người nhận offline thì tin trôi mất. Cần lịch sử thì tự lưu database
  (RabbitMQ giữ tin cho *consumer nền*, không giữ cho client WebSocket).
- **Redis Streams** — `infrastructure/redis/` hiện làm cache và pub/sub; Streams
  (hàng đợi có ack, đọc lại được) thì chưa. Cần ngay bây giờ thì dùng
  [Kafka](kafka.md) hoặc [RabbitMQ](rabbitmq.md).
- **Outbox pattern** — `publish()` và ghi database là hai thao tác riêng: ghi
  xong mà broker rớt thì sự kiện không đi đâu cả. Cần bảo đảm tuyệt đối thì
  lưu sự kiện vào một bảng outbox trong cùng transaction rồi đẩy sau.
- **Span cho tracing** — mới truyền `trace_id`, chưa sinh span và chưa gửi đi đâu.
