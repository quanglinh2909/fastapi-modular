# Redis

Tuỳ chọn. Không cài, không bật thì không ảnh hưởng gì tới phần còn lại.

```bash
fam install redis     # cài thư viện + ghi APP_REDIS__* vào .env
```

Hai việc làm được: **cache/khoá-giá trị/đếm** (`RedisClient`) và **phát tin tới
mọi worker** (`publish` + `@redis_subscriber`).

> Lớp này **không phải** adapter WebSocket. `APP_WS__ADAPTER=redis` là một thứ
> khác, cấu hình riêng, bật bằng `fam env ws-redis`, và chạy được kể cả khi
> `APP_REDIS__ENABLED=false`. Trỏ cả hai vào cùng một server thì hoàn toàn bình
> thường.

---


> Cần gửi rồi **chờ trả lời** (kiểu `client.send` / `@MessagePattern` của
> NestJS)? Đó là `redis.send()` và `@redis_responder` — [docs/rpc.md](rpc.md).

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Kết quả tính chậm, muốn **cache 30 giây**" | [Cache](#cache) |
| "Lưu / đọc một giá trị theo khoá" | [Khoá / giá trị](#khoá--giá-trị) |
| "**Đếm lượt** — rate limit, lượt xem" | [`incr`](#khoá--giá-trị) |
| "Xoá cả cụm khoá `bao-cao:*`" | [`delete_prefix`](#khoá--giá-trị) |
| "**Báo cho mọi worker** một tin" | [Pub/sub](#pubsub) |
| "Gửi rồi **chờ trả lời**" | [rpc.md](rpc.md) — `redis.send()` + `@redis_responder` |
| "Scale WebSocket nhiều worker" | KHÔNG phải trang này — `fam env ws-redis`, xem [websocket.md](websocket.md#8-chạy-nhiều-worker) |
| "Tin không được phép mất" | KHÔNG dùng Redis pub/sub — [RabbitMQ](rabbitmq.md) |
| "Redis chết thì app có chết theo không" | [Khi Redis chưa lên](#khi-redis-chưa-lên) |
| "Redis **đứt thì cảnh báo**, nối lại thì làm nóng cache" | [Biết khi nào mất kết nối](#biết-khi-nào-mất-kết-nối) |
| "Hỏi **Redis có đang nối không** — gọi liên tục cũng nhẹ" | [Hỏi trạng thái lúc này](#hỏi-trạng-thái-lúc-này) |
| "Bảng biến, số đo" | [Tra cứu](#tra-cứu) |

---

## Cache

Tiêm `RedisClient` qua `__init__` như mọi provider:

```python
# src/api/bao_cao/report_service.py
from fastapi_modular import injectable
from fastapi_modular.infrastructure.redis import RedisClient


@injectable
class ReportService:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def report(self, code: str) -> dict:
        return await self._redis.cached(f"bao-cao:{code}", lambda: self._compute(code), ttl=30)

    async def _compute(self, code: str) -> dict:
        return {"code": code}               # việc chậm thật của bạn ở đây

    def is_online(self) -> bool:
        return self._redis.connected        # đọc RAM, gọi liên tục cũng được
```

**Tiêm, đừng tự dựng.** `RedisClient(settings)` tự tạo là một client chưa bao
giờ được mở: `connected` luôn `False`, mọi lệnh ném `ServiceUnavailableError`.
Container đưa đúng bản mà app đã mở lúc khởi động.

```python
await redis.cached(key, factory, *, ttl=60.0) -> Any
```

| Tham số | Không truyền thì | Truyền khi nào |
|---|---|---|
| `key` | *bắt buộc* | — |
| `factory` | *bắt buộc* — hàm `async` tính giá trị thật khi trượt cache | — |
| `ttl` | `60.0` giây | dữ liệu đổi chậm thì để lớn; đổi nhanh thì nhỏ |

Đo thật trên ví dụ `/api/redis-test/bao-cao/ABC` (việc chậm 0.4s):

```
lần 1:  408ms  tu_cache=False  lan_tinh_that=1  ttl=30
lần 2:    0ms  tu_cache=True   lan_tinh_that=1  ttl=30
```

**`cached()` là hàm DUY NHẤT trong lớp này chịu hỏng.** Redis chết thì nó ghi
cảnh báo `redis.cache_bypass` rồi gọi thẳng `factory()`, request vẫn xong —
đúng, vì cache chỉ để nhanh hơn, mất cache là chậm đi chứ không phải sai đi.
Mọi hàm khác **ném lỗi**, vì ở đó Redis là nguồn dữ liệu chứ không phải bộ đệm.

---

## Khoá / giá trị

| Hàm | Chữ ký | Trả về |
|---|---|---|
| `get` | `(key, default=None)` | giá trị đã giải mã JSON, hoặc `default` nếu không có khoá |
| `set` | `(key, value, *, ttl=None, if_not_exists=False)` | `True`, hoặc `False` khi `if_not_exists` mà khoá đã có |
| `delete` | `(*keys)` | số khoá thật sự bị xoá |
| `delete_prefix` | `(prefix)` | số khoá bị xoá; duyệt bằng `SCAN` |
| `exists` | `(key)` | `True`/`False` |
| `ttl` | `(key)` | số giây còn lại, `None` nếu không hết hạn hoặc không có khoá |
| `incr` | `(key, amount=1, *, ttl=None)` | giá trị sau khi cộng |
| `publish` | `(channel, payload=None)` | **số người nghe** đã nhận |
| `raw` | `()` | client redis-py thô, cho lệnh khung chưa bọc (ZSET, stream) |

Vài điểm không hiển nhiên:

- **`set(ttl=None)` nghĩa là không bao giờ hết hạn.** Với dữ liệu cache thì hầu
  như luôn nên đặt một con số — khoá không hạn chỉ xoá được bằng tay, và Redis
  đầy RAM là cả hệ thống dừng.
- **`delete_prefix` dùng `SCAN`, không dùng `KEYS`.** `KEYS` quét toàn bộ không
  gian khoá trong một lệnh và khoá chặt server suốt lúc đó; trên Redis lớn là đủ
  để làm cả ứng dụng đứng hình.
- **`incr` là nguyên tử.** Đọc-rồi-ghi từ nhiều worker sẽ đếm thiếu; `INCR` thì
  không bao giờ. Test chạy 50 lệnh song song ra đúng 50.
- **`incr(ttl=...)` chỉ đặt hạn ở lần cộng ĐẦU TIÊN**, nên cửa sổ đếm không bị
  gia hạn vô hạn mỗi lần có thêm một lượt — đúng thứ cần cho "tối đa N lần trong
  60 giây".

Lỗi ném ra:

| Lỗi | Khi nào | Mã HTTP |
|---|---|---|
| `ComponentNotEnabledError` | `APP_REDIS__ENABLED=false`, hoặc chưa cài thư viện | 503 |
| `ServiceUnavailableError` | chưa nối được, hoặc lệnh lỗi/quá hạn | 503 |

---

## Pub/sub

```python
@injectable
class GiaListener:
    @redis_subscriber("gia:*")
    async def doi_gia(self, payload: GiaMoi, meta: dict) -> None:
        print(meta["channel"], payload.gia)
```

```python
@redis_subscriber(channel)
```

Tham số duy nhất là tên kênh, và đó là **chủ ý**: Redis pub/sub không có hàng
đợi để mà bền, không có ack để mà thử lại, nên không có gì khác để chỉnh.

| Khai | Nghĩa |
|---|---|
| `"gia:vang"` | khớp đúng kênh đó (`SUBSCRIBE`) |
| `"gia:*"` | có `*`, `?` hoặc `[` → khớp theo mẫu (`PSUBSCRIBE`) |

| Chữ ký handler | Nhận được |
|---|---|
| `async def f(self, payload: MyModel)` | `payload` đã validate bằng pydantic |
| `async def f(self, payload: dict)` | dữ liệu thô |
| `async def f(self, payload, meta: dict)` | thêm `meta` |

`meta` gồm `{"channel": "<kênh thật, đã có prefix>", "pattern": "<mẫu đã khai hoặc None>"}`.

### Điều phải biết trước khi dùng

**Tin phát ra lúc không ai nghe là mất luôn.** Không hàng đợi, không ack, không
thử lại, không DLQ. `publish()` trả về số người nghe — trả `0` nghĩa là tin vừa
rồi rơi vào hư không.

| Cần gì | Dùng gì |
|---|---|
| mọi worker nhận một bản sao, mất vài tin cũng không sao | **Redis pub/sub** |
| tin không được mất, chia việc cho worker | [RabbitMQ](rabbitmq.md) |
| đọc lại được lịch sử, nhiều nhóm đọc độc lập | [Kafka](kafka.md) |

Handler ném lỗi thì khung ghi log `redis.handler_failed` rồi **đi tiếp** — không
có chỗ nào để hoãn tin lại. Payload sai khuôn model thì log
`redis.payload_invalid` và bỏ tin.

Vòng đọc tự đăng ký lại kênh sau mỗi lần đứt: pool của redis-py tự mở lại
connection cho *lệnh* kế tiếp, nhưng một pubsub đứt thì **mất danh sách kênh đã
đăng ký**, đọc tiếp sẽ không bao giờ có tin nào nữa.

---

## Kiểm xem nó chạy chưa

`src/api/redis_test/` có sẵn, không cần viết gì thêm:

```bash
curl localhost:8002/api/redis-test/bao-cao/ABC        # cache 30s
curl -X DELETE localhost:8002/api/redis-test/bao-cao  # xoá bằng SCAN
curl -X POST localhost:8002/api/redis-test/dem/luot-xem
curl -X POST localhost:8002/api/redis-test/phat -H 'Content-Type: application/json' \
     -d '{"ma":"SJC","gia":78.5}'
curl localhost:8002/api/redis-test/da-nhan
```

Kết quả đo được:

```
đếm:     [1, 2, 3]
phát:    {"kenh": "gia:vang", "nguoi_nghe": 1, "canh_bao": null}
đã nhận: {"channel": "vd:gia:vang", "ma": "SJC", "gia": 78.5}
```

`nguoi_nghe: 0` sẽ kèm `canh_bao: "không ai đang nghe, tin đã mất"`.

---

## Khi Redis chưa lên

App **vẫn khởi động**: log `redis.starting_degraded`, HTTP và WebSocket phục vụ
bình thường, và một vòng nối lại chạy ngầm với backoff `1s → 2s → 4s → ... → 30s`.
Không có lựa chọn nào để tắt hành vi này — một dịch vụ phụ chưa sẵn sàng không
đáng để cả API nằm im.

`/api/health/ready` cho biết trạng thái nhưng **không** vì Redis rớt mà trả 503.

---

## Biết khi nào mất kết nối

"Redis đứt thì ghi cảnh báo", "nối lại thì ghi đứt bao lâu" — viết thế này:

```python
# src/api/bao_cao/redis_status.py
from fastapi_modular import get_logger, injectable
from fastapi_modular.infrastructure.redis import redis_on_connect, redis_on_disconnect

log = get_logger(__name__)


@injectable
class RedisStatus:
    @redis_on_connect
    async def online(self, info: dict) -> None:
        if info["reconnect"]:
            log.info("redis_back", downtime=info["downtime_seconds"])

    @redis_on_disconnect
    async def offline(self, info: dict) -> None:
        log.warning("redis_down", error=info["error"])
```

Không cần `info` thì bỏ đi: `async def online(self) -> None:` cũng chạy.

**Khi Redis bật, khung PING nó 5 giây một lần.** redis-py tự nối lại ở lệnh kế
tiếp mà không báo gì, nên không PING thì app đang rảnh sẽ không bao giờ biết
mình đứt. Chỉnh nhịp bằng `APP_REDIS__HEALTH_CHECK_SECONDS` ([Tra cứu](#tra-cứu)).

### Khi nào handler được gọi

| Chuyện xảy ra | Handler chạy |
|---|---|
| App khởi động, nối được Redis | `on_connect`, `info["reconnect"]` là `False` |
| Đang chạy thì đứt | `on_disconnect` — **một lần**; biết qua lần PING kế tiếp, hoặc ngay khi một lệnh hỏng vì mất kết nối |
| Nối lại được | `on_connect`, `info["reconnect"]` là `True` |
| Redis chưa lên lúc khởi động | **không gọi gì**; lúc Redis lên thì `on_connect` với `reconnect=False` |
| Tắt app | **không** gọi `on_disconnect` |

### `info` có gì

| Khoá | Có ở | Ý nghĩa |
|---|---|---|
| `url` | cả hai | URL Redis, đã che mật khẩu |
| `reconnect` | `on_connect` | `False` ở lần nối đầu, `True` ở mọi lần sau |
| `downtime_seconds` | `on_connect` | đứt bao nhiêu giây; `None` ở lần nối đầu |
| `error` | `on_disconnect` | lỗi làm đứt, ví dụ `"ConnectionError: Error 111 connecting to ..."`; có thể là `None` |

- **Đừng chờ `on_disconnect` cho lỗi của lệnh.** Sai kiểu khoá (`WRONGTYPE`),
  sai cú pháp vẫn ném `ServiceUnavailableError`, nhưng đường truyền còn sống nên
  không phải "mất kết nối".
- **Đừng dùng handler làm chốt trước khi gọi Redis.** Handler chạy trong một
  task riêng, SAU khi trạng thái đã đổi — và có thể trễ tới một nhịp PING. Cần
  "Redis chết thì đi đường vòng" thì dùng `cached()`.
- **Đặt timeout cho việc chậm trong handler.** Các handler nối/đứt chạy lần
  lượt: một `on_connect` treo thì `on_disconnect` phía sau phải chờ.

### Hỏi trạng thái lúc này

Không cần khai handler — hỏi thẳng, ở bất cứ đâu:

```python
from fastapi_modular import broker_status

status = broker_status("redis")
if not status["connected"]:
    print("Redis đang đứt từ", status["since"], "vì", status["last_error"])
```

**Gọi liên tục cũng được** (mỗi request, mỗi vòng lặp): hàm chỉ đọc biến trong
RAM, không gửi gì qua mạng, không cần `await`. Việc PING chạy ngầm, không nằm
trong lời gọi này.

| Khoá | Ý nghĩa |
|---|---|
| `enabled` | `False` khi `APP_REDIS__ENABLED=false` — lúc đó các khoá khác là mặc định |
| `connected` | đang nối hay không |
| `since` | `datetime` (UTC) của lần đổi trạng thái gần nhất; `None` nếu chưa từng nối |
| `last_error` | lỗi của lần đứt gần nhất; `None` nếu chưa đứt lần nào |
| `disconnects` | số lần đứt từ lúc app khởi động |
| `url` | Redis đang nhắm tới, đã che mật khẩu |

Không truyền tên thì trả mọi hạ tầng **đang bật**: `broker_status()` →
`{"redis": {...}, "mqtt": {...}}`.

Đã tiêm `RedisClient` vào service ([Cache](#cache)) thì đọc thẳng trên nó —
cũng chỉ đọc RAM:

| Gọi | Trả về |
|---|---|
| `self._redis.connected` | `bool` — cùng trạng thái với `broker_status("redis")["connected"]` |
| `self._redis.enabled` | `bool` — `APP_REDIS__ENABLED` |
| `self._redis.stats()` | `{"enabled", "connected", "url", "key_prefix"}` |

- **Chấp nhận trễ tối đa một nhịp PING.** Redis vừa chết thì `connected` còn
  `True` thêm tới `HEALTH_CHECK_SECONDS` giây (trừ khi một lệnh hỏng trước đó).
  Cần chắc hơn thì hạ nhịp, hoặc bắt lỗi ngay tại lệnh.
- **Gõ đúng tên.** `broker_status("reddis")` ném `ValueError` kèm danh sách tên
  hợp lệ: `kafka`, `mqtt`, `rabbitmq`, `redis`.

### Kiểm xem handler nối/đứt đã được nhận chưa

Lúc khởi động phải thấy:

```
redis.connection_hooks_registered  on_connect=['RedisStatus.online'] on_disconnect=['RedisStatus.offline']
```

Không thấy dòng này nghĩa là class thiếu `@injectable`, hoặc file không nằm dưới
`src/api/`. Thử đứt thật: `docker stop <container redis>` — trong vòng một nhịp
PING sẽ thấy `redis.connection_lost` rồi `on_disconnect` chạy; `docker start`
lại thì thấy `redis.reconnected` và `on_connect` với `reconnect=True`.

---

## Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân |
|---|---|
| `ComponentNotEnabledError` (HTTP 503) | `APP_REDIS__ENABLED=false`, hoặc chưa `fam install redis` |
| `ServiceUnavailableError` (HTTP 503) | không nối được server, hoặc một lệnh quá `COMMAND_TIMEOUT_SECONDS` |
| `publish` trả `0`, bên nghe không thấy gì | không ai đang `SUBSCRIBE` lúc đó — tin pub/sub không xếp hàng chờ |
| Bên nghe im lặng dù `publish` trả `> 0` | hai bên khác `KEY_PREFIX` — prefix ghép vào cả tên kênh |
| log `redis.payload_invalid` | payload không khớp model pydantic của handler — tin bị bỏ |
| log `redis.handler_failed` | handler ném lỗi; tin KHÔNG được phát lại |
| log `redis.cache_bypass` | Redis đang chết, `cached()` gọi thẳng `factory()` — app vẫn chạy, chỉ chậm |
| log `redis.starting_degraded` lúc khởi động | server chưa lên; vòng nối lại chạy ngầm, backoff tới 30s |
| Đếm bằng `incr` không bao giờ reset | `ttl=` chỉ đặt ở lần cộng ĐẦU; khoá tạo từ trước khi thêm `ttl` thì không có hạn — xoá khoá đi |
| `on_connect`/`on_disconnect` không bao giờ chạy, không có log `redis.connection_hooks_registered` | class thiếu `@injectable`, hoặc file không nằm dưới `src/api/` |
| Redis đã tắt mà vài giây sau `on_disconnect` mới chạy | chờ lần PING kế tiếp — nhịp là `HEALTH_CHECK_SECONDS` (mặc định 5s) |
| `get`/`set` ném `ServiceUnavailableError` mà `on_disconnect` không chạy | lỗi của lệnh (sai kiểu khoá, sai cú pháp), không phải mất kết nối |
| `on_disconnect` không bao giờ chạy dù Redis tắt hẳn | `HEALTH_CHECK_SECONDS=0` tắt vòng PING — chỉ còn biết khi một lệnh hỏng |
| log `redis.connection_hook_failed` | handler nối/đứt ném lỗi; handler khác và các lần sau vẫn chạy |
| `broker_status("redis")["connected"]` vẫn `True` vài giây sau khi Redis tắt | chờ lần PING kế tiếp — nhịp là `HEALTH_CHECK_SECONDS` |
| `broker_status("redis")` trả `enabled: False` dù đã bật | gọi trước khi app khởi động xong, hoặc `APP_REDIS__ENABLED` chưa đọc được — soi log `redis.disabled` |
| `ValueError: broker_status: không có hạ tầng ...` | gõ nhầm tên — dùng `kafka`, `mqtt`, `rabbitmq`, `redis` |
| `self._redis.connected` luôn `False`, mọi lệnh ném `ServiceUnavailableError` dù Redis sống | tự dựng `RedisClient(settings)` thay vì tiêm qua `__init__` — xem [Cache](#cache) |

---

## Tra cứu

### Cấu hình

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_REDIS__ENABLED` | không | `false` | bật/tắt toàn bộ lớp này |
| `APP_REDIS__URL` | **có** | `redis://localhost:6379/0` | `redis://[:pass@]host:port/db`, hoặc `rediss://` nếu có TLS |
| `APP_REDIS__KEY_PREFIX` | không | *(trống)* | ghép vào **mọi khoá và mọi kênh** |
| `APP_REDIS__CONNECT_TIMEOUT_SECONDS` | không | `5.0` | chờ mở kết nối |
| `APP_REDIS__COMMAND_TIMEOUT_SECONDS` | không | `5.0` | trần cho **một** lệnh |
| `APP_REDIS__RECONNECT_DELAY_SECONDS` | không | `1.0` | chờ trước lần nối lại đầu tiên |
| `APP_REDIS__MAX_RECONNECT_DELAY_SECONDS` | không | `30.0` | trần thời gian chờ (tăng gấp đôi mỗi lần) |
| `APP_REDIS__HEALTH_CHECK_SECONDS` | không | `5.0` | nhịp PING để biết [đứt/nối lại](#biết-khi-nào-mất-kết-nối); `0` = tắt (chỉ còn biết đứt khi một lệnh hỏng) |

Chỉ đọc nếu bạn đang cân nhắc chỉnh `HEALTH_CHECK_SECONDS`: đo trên máy dev với
`HEALTH_CHECK_SECONDS=1`, `docker stop` Redis thì `on_disconnect` chạy sau
**1,0 giây**, `docker start` lại thì `on_connect` chạy sau **0,5 giây**. Server
treo (không từ chối mà cũng không trả lời) thì cộng thêm tối đa
`COMMAND_TIMEOUT_SECONDS`.

`KEY_PREFIX` đáng đặt khi nhiều ứng dụng dùng chung một Redis: đặt `"don-hang:"`
thì khoá `bao-cao:A` nằm ở `don-hang:bao-cao:A`, không ai ghi đè của ai. Nó ghép
vào cả tên kênh pub/sub, nên hai ứng dụng cũng không nghe nhầm của nhau.

`COMMAND_TIMEOUT_SECONDS` là thứ hay bị bỏ qua: Redis **chậm** còn tệ hơn Redis
**chết**, vì không có trần thì mọi request đang chờ cache sẽ treo theo.

### Số đo

| Tên | Ý nghĩa |
|---|---|
| `redis_cache_hit_total` | đọc trúng cache |
| `redis_cache_miss_total` | đọc trượt, phải tính lại |
| `redis_error_total` | lệnh lỗi (kể cả lúc `cached()` tự bỏ qua) |
| `redis_published_total` | tin đã phát |
| `redis_received_total` | tin nhận được |
| `redis_handler_failed_total` | handler xử lý lỗi |

---

### Chạy thử bằng Docker

```bash
docker run -d --name redis-test -p 6389:6379 redis:7-alpine
fam install redis
TEST_REDIS_URL=redis://localhost:6389/0 fam test    # bật nhóm test cần Redis thật
```
