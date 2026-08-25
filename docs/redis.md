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

## Cấu hình

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_REDIS__ENABLED` | không | `false` | bật/tắt toàn bộ lớp này |
| `APP_REDIS__URL` | **có** | `redis://localhost:6379/0` | `redis://[:pass@]host:port/db`, hoặc `rediss://` nếu có TLS |
| `APP_REDIS__KEY_PREFIX` | không | *(trống)* | ghép vào **mọi khoá và mọi kênh** |
| `APP_REDIS__CONNECT_TIMEOUT_SECONDS` | không | `5.0` | chờ mở kết nối |
| `APP_REDIS__COMMAND_TIMEOUT_SECONDS` | không | `5.0` | trần cho **một** lệnh |
| `APP_REDIS__RECONNECT_DELAY_SECONDS` | không | `1.0` | chờ trước lần nối lại đầu tiên |
| `APP_REDIS__MAX_RECONNECT_DELAY_SECONDS` | không | `30.0` | trần thời gian chờ (tăng gấp đôi mỗi lần) |

`KEY_PREFIX` đáng đặt khi nhiều ứng dụng dùng chung một Redis: đặt `"don-hang:"`
thì khoá `bao-cao:A` nằm ở `don-hang:bao-cao:A`, không ai ghi đè của ai. Nó ghép
vào cả tên kênh pub/sub, nên hai ứng dụng cũng không nghe nhầm của nhau.

`COMMAND_TIMEOUT_SECONDS` là thứ hay bị bỏ qua: Redis **chậm** còn tệ hơn Redis
**chết**, vì không có trần thì mọi request đang chờ cache sẽ treo theo.

---

## Cache

```python
@injectable
class BaoCaoService:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def bao_cao(self, ma: str) -> dict:
        return await self._redis.cached(
            f"bao-cao:{ma}", lambda: self._tinh_that(ma), ttl=30
        )
```

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

## Ví dụ chạy được

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

## Số đo

| Tên | Ý nghĩa |
|---|---|
| `redis_cache_hit_total` | đọc trúng cache |
| `redis_cache_miss_total` | đọc trượt, phải tính lại |
| `redis_error_total` | lệnh lỗi (kể cả lúc `cached()` tự bỏ qua) |
| `redis_published_total` | tin đã phát |
| `redis_received_total` | tin nhận được |
| `redis_handler_failed_total` | handler xử lý lỗi |

---

## Chạy thử bằng Docker

```bash
docker run -d --name redis-test -p 6389:6379 redis:7-alpine
fam install redis
TEST_REDIS_URL=redis://localhost:6389/0 fam test    # bật nhóm test cần Redis thật
```
