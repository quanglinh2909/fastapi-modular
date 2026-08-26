# RabbitMQ

Tuỳ chọn. Không cài, không bật thì không ảnh hưởng gì tới phần còn lại.

```bash
fam install rabbitmq     # cài thư viện + ghi APP_RABBITMQ__* vào .env
```

Hai việc làm được: **đăng tin** (`RabbitBroker.publish`) và **xử lý tin nền**
(`@rabbitmq_subscriber`).

Cần gửi rồi **chờ trả lời** (kiểu `client.send` / `@MessagePattern` của NestJS)
thì đó là `emit`/`send` và `@rabbitmq_responder` — [docs/rpc.md](rpc.md).

Một `@rabbitmq_subscriber` mặc định tạo **đúng một hàng đợi**. Thử lại, hàng đợi
chết và hạn dùng đều là thứ **tự bật**, không phải thứ mặc định có.

Đủ [năm kiểu exchange](#năm-kiểu-exchange) của AMQP — `topic` (mặc định),
`direct`, `fanout`, `headers`, `default` — và [ba dạng hạn dùng](#hạn-dùng-ttl).

---

## Cấu hình

Trong `.env` chỉ có thứ thuộc về **kết nối**. Mọi chính sách của từng consumer
khai ở `@rabbitmq_subscriber`.

Xoá dòng nào thì biến đó quay về mặc định — trừ `URL`, thứ duy nhất phải tự
điền vì không đoán được.

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_RABBITMQ__ENABLED` | không | `false` | bật/tắt toàn bộ lớp này |
| `APP_RABBITMQ__URL` | **có** | `amqp://guest:guest@localhost:5672/` | `amqp://user:pass@host:port/vhost` |
| `APP_RABBITMQ__PUBLISH_TIMEOUT_SECONDS` | không | `5.0` | chờ broker xác nhận một lần đăng tin; đè bằng `publish(timeout=)` |
| `APP_RABBITMQ__CONNECT_TIMEOUT_SECONDS` | không | `10.0` | chờ khi mở kết nối |
| `APP_RABBITMQ__HEARTBEAT_SECONDS` | không | `30` | nhịp tim AMQP; phát hiện đứt sau ~2× giá trị này |
| `APP_RABBITMQ__RECONNECT_DELAY_SECONDS` | không | `2.0` | chờ trước lần nối lại đầu tiên |
| `APP_RABBITMQ__MAX_RECONNECT_DELAY_SECONDS` | không | `30.0` | trần thời gian chờ (tăng gấp đôi mỗi lần) |

`fam env rabbitmq` ghi sẵn cả bảy dòng này vào `.env`, mỗi dòng kèm giải
thích và mặc định ngay phía trên.

Mật khẩu chứa `@` viết thẳng được (`amqp://admin:Pass@123@host:5672/`); parser
lấy dấu `@` cuối cùng làm ranh giới.

---

## Đăng tin

```python
from fastapi_modular.core.container import injectable
from fastapi_modular.infrastructure.rabbitmq import RabbitBroker


@injectable
class AlertService:
    def __init__(self, mq: RabbitBroker) -> None:
        self._mq = mq

    async def canh_bao(self, alert) -> None:
        await self._mq.publish("events", "alert.created.hanoi", {"id": alert.id})
```

### `broker.publish(...)`

```python
await broker.publish(
    exchange,            # str  — tên exchange (tự tạo nếu chưa có); "" = exchange mặc định
    routing_key="",      # str  — "alert.created.hanoi"; fanout/headers bỏ trống
    payload=None,        # Any  — bất cứ thứ gì json hoá được
    *,
    exchange_type=None,  # None = dùng lại kiểu đã khai, chưa có thì "topic"
    headers=None,        # dict | None — với exchange headers thì đây là thứ để lọc
    persistent=True,     # tin sống sót qua restart broker
    ttl=None,            # giây — hạn dùng của RIÊNG tin này
    timeout=None,        # None = dùng APP_RABBITMQ__PUBLISH_TIMEOUT_SECONDS
    fire_and_forget=False,
) -> bool
```

| Tham số | Không truyền thì | Truyền khi nào |
|---|---|---|
| `payload` | `None` | luôn truyền, trừ sự kiện không có dữ liệu |
| `headers` | `{}` | gắn metadata (trace id, phiên bản schema) |
| `persistent` | `True` | `False` cho tin mất cũng được (số đo, nhịp tim) — nhanh hơn |
| `timeout` | lấy từ `.env` (5s) | lần đăng tin đặc biệt lâu, ví dụ `timeout=30` khi nhập liệu hàng loạt |
| `fire_and_forget` | `False` → ném lỗi khi broker hỏng | `True` khi thà mất tin còn hơn hỏng cả request |

**Trả về** `True` nếu broker đã xác nhận nhận tin. Chỉ trả `False` khi
`fire_and_forget=True` và gửi hỏng.

**Ném lỗi** (khi `fire_and_forget=False`):

| Lỗi | Khi nào | HTTP tương ứng |
|---|---|---|
| `ComponentNotEnabledError` | `APP_RABBITMQ__ENABLED=false` | 503 |
| `ServiceUnavailableError` | chưa nối được broker, hoặc quá `timeout` | 503 |
| `AMQPError` | lỗi giao thức | 503 |

```python
# gửi kèm header, không quan tâm thành công
await self._mq.publish(
    "events", "alert.viewed", {"id": "A1"},
    headers={"trace_id": get_trace_id()},
    fire_and_forget=True,
)
```

### Gửi nhiều tin một lúc

Vòng `for` gửi tin đi rất chậm, và không phải vì RabbitMQ chậm:

```python
for event in events:
    await self._mq.emit("cam.event", event, queue="events")   # 130 tin/s
```

Mỗi lần `emit` chờ broker **xác nhận** (publisher confirm) rồi mới gửi tin sau.
Với tin `persistent` (mặc định) vào hàng đợi `durable`, xác nhận đó phải đợi
RabbitMQ fsync xuống đĩa — khoảng 7,7 ms. Vòng `for` chỉ để **một tin bay mỗi
lần**, nên 7,7 ms đó không bao giờ được che đi.

```python
await self._mq.emit_many("cam.event", events, queue="events")  # 6.100 tin/s
```

Đo trên broker localhost, tin `persistent`, hàng đợi `durable`, lô 2.000 tin,
trung vị 5 lần đo:

| Cách gửi | Tốc độ | Mỗi tin |
|---|---|---|
| `for ... await emit(...)` | 130 tin/s | 7,7 ms |
| `emit_many(..., concurrency=25)` | 1.841 tin/s | 0,54 ms |
| `emit_many(..., concurrency=50)` | 3.520 tin/s | 0,28 ms |
| **`emit_many(...)`** *(mặc định 100)* | **6.132 tin/s** | **0,16 ms** |

**Không đánh đổi gì về độ bền vững**: tin vẫn `persistent`, vẫn chờ xác nhận
từng cái. Chỉ là nhiều tin bay cùng lúc nên RabbitMQ gộp được các lần fsync.

Đặt `concurrency` cao hơn 100 *có thể* nhanh hơn nhưng dao động rất rộng — 400
đo được từ 4.761 tới 12.947 tin/s giữa các lần, tuỳ lúc broker gộp fsync. Nó
còn khiến một lô lớn chiếm hết bộ đệm gửi và làm nghẽn những lời `publish` khác
của tiến trình.

```python
sent = await self._mq.emit_many(
    "cam.event", events,
    queue="events",
    persistent=True,        # như emit()
    concurrency=100,        # số tin cho phép bay cùng lúc
)
if sent < len(events):
    log.warning("mất tin", thieu=len(events) - sent)
```

Tin nào hỏng thì ghi cảnh báo và không tính, **cả lô vẫn đi tiếp** — gửi 500 sự
kiện mà một cái sai thì mất 499 cái kia là quá đắt. So số trả về với số phần tử
đưa vào là biết có mất gì không.

Không cần bền vững (vị trí xe, nhiệt độ, tin theo dõi trực tiếp) thì
`persistent=False` cũng đưa vòng `for` lên 4.319 tin/s — nhưng broker restart là
mất sạch phần chưa ai lấy.

### Đặt tên routing key

`alert.created.hanoi` — từ rộng đến hẹp, vì mẫu khớp từ trái sang:

| Mẫu | `alert` | `alert.created` | `alert.created.hanoi` |
|---|---|---|---|
| `alert.*` | không | **có** | không |
| `alert.#` | **có** | **có** | **có** |
| `*.created.*` | không | không | **có** |
| `#` | **có** | **có** | **có** |

`*` khớp đúng một từ, `#` khớp không hoặc nhiều từ.

Đặt tên exchange và routing key thành hằng số dùng chung; gõ nhầm chuỗi thì
RabbitMQ tạo luôn exchange mới và tin bay vào đó, không có lỗi nào cả:

```python
# src/api/alerts/events.py
EXCHANGE = "events"
ALERT_CREATED = "alert.created"
```

---

## Năm kiểu exchange

Exchange không giữ tin. Nó chỉ trả lời một câu hỏi: **tin này đi vào những hàng
đợi nào?** Năm kiểu là năm cách trả lời câu hỏi đó.

| Kiểu | Chọn hàng đợi theo | Khai ở consumer | Dùng khi |
|---|---|---|---|
| `topic` *(mặc định)* | routing key khớp **mẫu** có `*` và `#` | `routing_key="alert.#"` | phần lớn trường hợp: một luồng sự kiện, mỗi bên nghe một nhánh |
| `direct` | routing key **trùng khít** | `routing_key="device.reboot"` | tên lệnh/loại việc đóng, không cần mẫu |
| `fanout` | **không lọc gì** — mọi hàng đợi đã bind | bỏ `routing_key` | phát tán: xoá cache, tải lại cấu hình, mọi worker đều phải biết |
| `headers` | **header** của tin | `headers_match={"vung": "hanoi"}` | phải lọc theo **nhiều chiều** cùng lúc, nhét hết vào routing key thì rối |
| `default` | **tên hàng đợi** | `exchange=""` | giao việc thẳng cho một hàng đợi cụ thể, không định tuyến gì |

Không khai `exchange_type` thì là `topic`, trừ khi `exchange=""` — tên rỗng
luôn là exchange mặc định.

### Chọn kiểu nào

Cứ mặc định `topic` cho tới khi có lý do rõ ràng để đổi. `topic` làm được việc
của `direct` (routing key không có `*` `#` thì nó chính là trùng khít) và của
`fanout` (bind `#`), chỉ thua ở chỗ đọc code không thấy ngay ý định.

Đổi khi ý định đáng được viết ra:

- `fanout` nói "**mọi** người nhận" rõ hơn `topic` + `#`, và không ai lỡ tay
  thêm bộ lọc vào được.
- `direct` từ chối `alert.*` ngay lúc khởi động, nên không ai vô tình mở rộng
  phạm vi một hàng đợi lệnh.
- `headers` là kiểu duy nhất lọc được nhiều chiều độc lập.

### `fanout` — mọi hàng đợi một bản sao

```python
import socket

TOI = socket.gethostname()      # mỗi worker một tên hàng đợi khác nhau

# Người gửi
await broker.publish("cache-events", payload={"khoa": "user:9"}, exchange_type="fanout")

# Mỗi worker một hàng đợi RIÊNG -> mỗi worker nhận một bản sao
@rabbitmq_subscriber("cache-events", queue=f"xoa-cache-{TOI}",
                     exchange_type="fanout", durable=False, auto_delete=True)
async def xoa_cache(self, payload: dict) -> None: ...
```

Cái bẫy ở đây không phải kiểu exchange mà là **tên hàng đợi**: hai worker dùng
CHUNG một tên thì RabbitMQ chia lượt cho nhau, mỗi tin chỉ một worker thấy —
fanout hay không cũng vậy. Muốn mọi worker cùng nhận thì mỗi worker phải có
hàng đợi riêng (tên kèm hostname, hoặc [hàng đợi tự sinh](#khi-cần-mọi-worker-cùng-nhận-một-bản-sao)).

### `direct` — trùng khít, không mẫu nào cả

```python
await broker.publish("cmd", "device.reboot", {"id": "cam-01"}, exchange_type="direct")

@rabbitmq_subscriber("cmd", "device.reboot", queue="device-cmd", exchange_type="direct")
async def reboot(self, payload: dict) -> None: ...
```

`routing_key` chứa `*` hoặc `#` sẽ bị từ chối ngay lúc khởi động — với `direct`
chúng chỉ là ký tự thường, và "cái mẫu của tôi không khớp gì cả" là lỗi tốn cả
buổi để tìm ra.

### `headers` — lọc nhiều chiều

```python
await broker.publish(
    "audit", payload={"id": 1},
    exchange_type="headers", headers={"vung": "hanoi", "muc": "cao"},
)

# match="all": phải khớp MỌI cặp.  match="any": khớp một cặp là đủ.
@rabbitmq_subscriber("audit", queue="canh-bao-hanoi", exchange_type="headers",
                     headers_match={"vung": "hanoi", "muc": "cao"}, match="all")
async def canh_bao(self, payload: dict) -> None: ...
```

`routing_key` **bị bỏ qua hoàn toàn** — khai nó là lỗi ngay lúc khởi động, vì
tin vẫn đi nhưng không theo cách bạn tưởng.

Chỉ đổi sang `headers` khi thật sự cần lọc nhiều chiều **độc lập** với nhau
(vùng × mức × loại). Ba chiều nhét vào routing key thì phải chọn trước thứ tự
`vung.muc.loai`, và người muốn "mọi vùng, mức cao" sẽ mắc kẹt. Đổi lại: mẫu
routing key hiện ngay trong giao diện quản trị RabbitMQ và trong log, còn điều
kiện header thì phải đi tra binding mới thấy.

### `default` — đi thẳng vào một hàng đợi

Exchange tên rỗng có sẵn ở mọi broker, không khai báo được, và **mọi hàng đợi
đã tự nối với nó qua đúng tên của mình**. Gửi vào đó là gửi thẳng cho một hàng
đợi, không định tuyến gì:

```python
await broker.publish("", "viec-nen", {"id": 7})     # routing key = TÊN hàng đợi

@rabbitmq_subscriber("", queue="viec-nen")          # exchange="" -> tự hiểu là default
async def lam_viec(self, payload: dict) -> None: ...
```

Hợp cho hàng đợi việc một-chiều (job queue), nơi chỉ có đúng một bên nhận và
không bao giờ cần thêm bên thứ hai. Cần thêm người nghe về sau thì phải sửa
người gửi — đó là cái giá của việc bỏ qua định tuyến.

> Không bind tay vào exchange mặc định được: AMQP cấm, và lệnh bind sai sẽ đóng
> cả kênh. Khung tự bỏ qua bước bind cho kiểu này.

### Một exchange chỉ có MỘT kiểu

Khai lại một exchange với kiểu khác là lỗi giao thức: RabbitMQ đáp
`PRECONDITION_FAILED` rồi **đóng kênh đăng tin**, kéo theo mọi lời `publish`
khác của tiến trình — không riêng lời gọi sai. Nên khung chặn tại chỗ:

```
Exchange 'events' đã khai kiểu 'fanout', giờ lại đòi kiểu 'topic'.
```

Vì vậy `publish()` **không cần** nhắc lại `exchange_type` khi trong tiến trình
đã có consumer khai exchange đó — khung dùng lại đúng kiểu ấy. Chỉ khai
`exchange_type` ở `publish()` khi tiến trình này chỉ gửi, không nghe.

Exchange đã tồn tại trên broker từ trước với kiểu khác thì phải xoá nó:
`rabbitmqctl delete_exchange <tên>`. Không có cách đổi kiểu tại chỗ.

---

## Consumer nền

```python
from fastapi_modular.core.container import injectable
from fastapi_modular.infrastructure.rabbitmq import rabbitmq_subscriber


@injectable
class AlertConsumer:
    def __init__(self, service: AlertService) -> None:
        self._service = service

    @rabbitmq_subscriber("events", "alert.created", queue="alert-mailer")
    async def gui_mail(self, payload: AlertCreated) -> None:
        await self._service.notify(payload.id)
```

Không cần decorator ở cấp class — bất kỳ class `@injectable` nào cũng chứa
consumer được. `fam module alerts --consumer` hoặc `fam module alerts` --consumer-only
sinh sẵn khung này.

### `@rabbitmq_subscriber(...)`

```python
@rabbitmq_subscriber(
    exchange,            # str — tên exchange; "" = exchange mặc định
    routing_key="",      # str — mẫu để nghe: "alert.#"; fanout/headers bỏ trống
    *,
    queue,               # str — BẮT BUỘC, tên nhóm consumer
    exchange_type=None,  # None = "topic", hoặc "default" khi exchange=""
    headers_match=None,  # dict — chỉ cho exchange_type="headers"
    match="all",         # "all" = khớp mọi cặp header, "any" = một cặp là đủ
    message_ttl=None,    # giây — tin nằm trong hàng đợi quá lâu thì bỏ
    queue_expires=None,  # giây — hàng đợi không ai dùng quá lâu thì broker xoá
    max_retries=0,
    retry_delay=10.0,
    dead_letter=False,
    durable=True,
    auto_delete=False,
    prefetch=20,
)
```

Không khai gì thêm thì trên broker mọc ra **đúng một hàng đợi**. Handler ném lỗi
thì tin bị bỏ, kèm log `mq.message_dropped`. Hai hàng đợi phụ chỉ xuất hiện khi
bạn tự bật — xem [.retry và .dlq là gì](#retry-và-dlq-là-gì).

| Tham số | Mặc định | Không truyền thì | Đổi khi nào |
|---|---|---|---|
| `queue` | *bắt buộc* | — | luôn phải đặt; đây là danh tính của nhóm consumer |
| `exchange_type` | `topic` | routing key là **mẫu** có `*` và `#` | `direct` / `fanout` / `headers` / `default` — xem [Năm kiểu exchange](#năm-kiểu-exchange) |
| `headers_match` | `None` | *(chỉ dùng với `exchange_type="headers"`)* | `{"vung": "hanoi"}` khi lọc theo header |
| `match` | `"all"` | phải khớp **mọi** cặp trong `headers_match` | `"any"` khi khớp một cặp là đủ |
| `message_ttl` | `None` | tin nằm trong hàng đợi **mãi mãi** cho tới khi có người lấy | đặt (giây) khi tin cũ mất giá trị — xem [Hạn dùng](#hạn-dùng-ttl) |
| `queue_expires` | `None` | hàng đợi tồn tại mãi kể cả khi không ai dùng | đặt (giây) cho hàng đợi tạm sinh theo phiên/theo worker |
| `max_retries` | `0` | **hỏng là bỏ ngay**, chỉ để lại log | `3`–`5` khi lỗi thường là tạm thời (gọi API ngoài, SMTP) → thêm `<queue>.retry` |
| `retry_delay` | `10.0` | *(không dùng tới khi `max_retries=0`)* | `60` cho dịch vụ ngoài; `0.5` cho việc nhanh |
| `dead_letter` | `False` | tin hỏng **biến mất** sau khi hết lượt thử | `True` khi cần xem lại tin hỏng → thêm `<queue>.dlq` |
| `durable` | `True` | hàng đợi sống sót qua restart broker | `False` cho số đo, nhịp tim |
| `auto_delete` | `False` | **giữ lại hàng đợi khi app tắt** — tin gửi lúc app đang deploy/chết vẫn đọng ở broker, lên là xử lý tiếp | `True` khi tin chỉ có giá trị lúc này (theo dõi trực tiếp, số đo) |
| `prefetch` | `20` | nhận tối đa 20 tin chưa ack | nhỏ (1–5) nếu handler chậm/payload nặng; lớn (100+) nếu handler nhanh |

`queue` cố ý **không tự sinh**: tên tự sinh sẽ đổi sau mỗi lần deploy, để lại
hàng đợi cũ đầy tin không ai đọc.

Nhiều worker cùng chạy sẽ **chia nhau** tin trên hàng đợi này — mỗi tin đúng
một worker xử lý.

#### `durable` và `auto_delete` khác nhau chỗ nào

Hai tham số trả lời hai câu hỏi khác nhau, và mặc định của cả hai đều là *giữ*:

| | Câu hỏi | Mặc định | Đặt ngược lại thì |
|---|---|---|---|
| `durable=True` | **broker** restart thì hàng đợi còn không? | còn | broker restart là mất hàng đợi lẫn tin |
| `auto_delete=False` | **app** tắt thì hàng đợi còn không? | còn | consumer cuối cùng ngắt là broker xoá hàng đợi + mọi tin trong đó |

Với `auto_delete=False` (mặc định), tắt app rồi publish tiếp thì tin vẫn xếp
hàng chờ; mở app lên là handler chạy hết chỗ tồn đọng đó. Đây là lý do dùng
hàng đợi ngay từ đầu, nên đừng đổi trừ khi có lý do rõ ràng.

Với `auto_delete=True`, hàng đợi biến mất lúc consumer cuối cùng ngắt và mọi tin
publish trong lúc app tắt rơi vào hư không (exchange không tìm thấy hàng đợi nào
khớp thì bỏ tin, không báo lỗi).

Dọn dẹp là **trọn gói**: nếu có `<queue>.retry` và `<queue>.dlq` (do bạn tự bật)
thì chúng bị xoá theo, không để lại gì trên broker.

```python
# tin theo dõi trực tiếp: app tắt là không còn ý nghĩa, đừng để rác lại broker
@rabbitmq_subscriber("events", "metric.#", queue="live-dashboard",
            durable=False, auto_delete=True)
async def day_len_bang_dieu_khien(self, payload: dict) -> None:
    ...
```

```
mặc định (chỉ một hàng đợi):
app đang chạy -> live-dashboard
app đã tắt    -> (không còn gì)

nếu có bật thêm max_retries + dead_letter:
app đang chạy -> live-dashboard, live-dashboard.retry, live-dashboard.dlq
app đã tắt    -> (không còn gì)
```

Chỗ này khung phải tự làm, vì `auto_delete` của AMQP không đủ: nó chỉ kích hoạt
khi consumer **cuối cùng** rời đi, mà `.retry` và `.dlq` thì không có ai nghe bao
giờ — để mặc thì hàng đợi chính biến mất còn hai hàng đợi phụ nằm lại vĩnh viễn.

Chạy nhiều worker vẫn an toàn: trước khi xoá, khung hỏi lại broker xem hàng đợi
chính còn không. Còn nghĩa là worker khác vẫn đang nghe, và việc dọn được hoãn
lại cho worker tắt sau cùng — nếu không, worker đầu tiên tắt sẽ cướp mất hàng
đợi thử lại của những worker còn đang chạy.

| | Hàng đợi trên broker |
|---|---|
| hai worker chạy | `q`, `q.retry`, `q.dlq` |
| worker 1 tắt | `q`, `q.retry`, `q.dlq` — worker 2 vẫn thử lại được |
| worker 2 tắt nốt | *(sạch)* |

Bị `kill -9` thì không kịp dọn: hàng đợi chính vẫn tự xoá (broker thấy kết nối
đứt), còn `.retry`/`.dlq` ở lại tới lần tắt sạch sau đó.

### Chữ ký handler

| Viết | Nhận được |
|---|---|
| `async def f(self, payload: MyModel)` | `payload` đã validate bằng pydantic |
| `async def f(self, payload: dict)` | dữ liệu thô, không validate |
| `async def f(self, payload: dict, meta: dict)` | thêm `meta` |

`meta` gồm:

```python
{"exchange": "events", "routing_key": "alert.created.hanoi",
 "message_id": "9f2c...", "attempt": 1, "redelivered": False}
```

Mỗi tin chạy trong một request scope riêng, có `request_id` riêng — transaction
database hoạt động y như khi được gọi từ HTTP.

### Xử lý lỗi

Mặc định (`max_retries=0, dead_letter=False`):

| Handler làm gì | Kết quả |
|---|---|
| trả về bình thường | ack, xong |
| ném lỗi bất kỳ | **tin bị bỏ**, log `mq.message_dropped` |

Khi đã bật thử lại và hàng đợi chết:

| Handler làm gì | Kết quả |
|---|---|
| trả về bình thường | ack, xong |
| ném lỗi bất kỳ, còn lượt thử | đẩy sang `<queue>.retry`, quay lại sau `retry_delay` giây |
| ném lỗi, hết lượt thử | vào `<queue>.dlq` (hoặc bị bỏ nếu `dead_letter=False`) |
| ném `PermanentMessageError` | vào thẳng `.dlq`, **không** thử lại |
| payload sai khuôn model | như `PermanentMessageError` |

```python
from fastapi_modular.infrastructure.rabbitmq import PermanentMessageError

@rabbitmq_subscriber("events", "order.paid", queue="order-ship",
            max_retries=5, retry_delay=30, dead_letter=True)
async def giao_hang(self, payload: OrderPaid, meta: dict) -> None:
    don = await self._repo.get(payload.order_id)
    if don is None:
        raise PermanentMessageError(f"Đơn {payload.order_id} không tồn tại")
    if meta["attempt"] > 1:
        log.warning("thu_lai", lan=meta["attempt"])
    await self._ship.create(don)          # lỗi mạng -> tự thử lại
```

### Hàng đợi được tạo ra

| Khai báo | Hàng đợi trên broker |
|---|---|
| **mặc định** | `q` |
| `max_retries=3` | `q`, `q.retry` |
| `max_retries=3, dead_letter=True` | `q`, `q.retry`, `q.dlq` |
| `dead_letter=True` (không thử lại) | `q`, `q.dlq` |
| thêm `auto_delete=True` | như trên, nhưng **tất cả** biến mất khi app tắt |

Exchange `dlx` (dùng chung cho mọi consumer) chỉ được khai khi có ít nhất một
consumer bật `dead_letter=True`.

---

## Hạn dùng (TTL)

Mặc định tin nằm trong hàng đợi **vĩnh viễn** — đó thường là điều bạn muốn, và
cũng là lý do hàng đợi tồn tại. Nhưng có loại tin cũ đi thì thành vô nghĩa hoặc
thành có hại: vị trí xe lúc 9h sáng, mã OTP, lệnh "bật đèn" gửi lúc thiết bị
đang mất mạng.

Ba cách đặt hạn, khác nhau ở **phạm vi** và ở **cái giá khi đổi**:

| Cách | Phạm vi | Đổi con số |
|---|---|---|
| `publish(..., ttl=5)` | **một tin** | tự do, đổi lúc nào cũng được |
| `@rabbitmq_subscriber(..., message_ttl=60)` | **mọi tin** trong hàng đợi đó | phải xoá hàng đợi cũ trước |
| `@rabbitmq_subscriber(..., queue_expires=3600)` | **bản thân hàng đợi** | phải xoá hàng đợi cũ trước |

Tất cả tính bằng **giây** (AMQP tính bằng mili-giây, khung tự quy đổi).

### `ttl` — hạn của một tin

```python
# Vị trí xe: quá 5 giây thì số cũ còn tệ hơn không có số nào.
await broker.publish("events", "xe.viTri", {"id": "51A", "lat": 21.0}, ttl=5)
```

Không đụng tới khai báo hàng đợi, nên đổi tuỳ ý. Đây là lựa chọn mặc định nên
dùng khi chỉ một vài loại tin cần hạn.

### `message_ttl` — hạn của mọi tin trong hàng đợi

```python
@rabbitmq_subscriber("events", "viTri.#", queue="theo-doi-xe",
                     message_ttl=30, durable=False)
async def cap_nhat(self, payload: dict) -> None: ...
```

Dùng khi **cả hàng đợi** là loại dữ liệu chóng hỏng, và bạn muốn luật đó nằm
cạnh consumer chứ không rải ở mọi chỗ gọi `publish`.

Đây cũng là cái van chặn hàng đợi phình vô hạn khi consumer chết mà người gửi
vẫn gửi: không có hạn thì tin dồn tới lúc broker hết đĩa và **mọi** hàng đợi
chết theo, kể cả những hàng đợi không liên quan.

### `queue_expires` — hạn của chính hàng đợi

```python
@rabbitmq_subscriber("events", "phien.#", queue=f"phien-{ma_phien}",
                     queue_expires=3600, durable=False)
async def theo_doi(self, payload: dict) -> None: ...
```

Broker xoá hàng đợi sau khoảng thời gian **không ai dùng** (không consumer,
không ai lấy tin). Dành cho hàng đợi sinh theo phiên hoặc theo worker — thứ mà
`auto_delete` không dọn nổi vì tiến trình có thể chết mà không kịp ngắt sạch.

### Hết hạn rồi thì tin đi đâu

Mặc định: **biến mất, không log, không đếm**. Muốn biết mình đã bỏ mất gì thì
bật `dead_letter=True` — tin hết hạn rơi vào `<queue>.dlq` y như tin lỗi:

```python
@rabbitmq_subscriber("events", "otp.#", queue="gui-otp",
                     message_ttl=120, dead_letter=True)
async def gui_otp(self, payload: OtpRequest) -> None: ...
```

Nhìn `<queue>.dlq` phình lên là biết consumer đang chậm hơn người gửi — thứ
mà con số "hàng đợi rỗng" không bao giờ nói cho bạn.

> **`message_ttl` và `queue_expires` đi vào tham số khai báo hàng đợi.**
> RabbitMQ không cho khai lại hàng đợi đã tồn tại với tham số khác. Đổi con số
> rồi khởi động lại mà chưa xoá hàng đợi cũ thì gặp `PRECONDITION_FAILED` —
> khung sẽ báo đúng lệnh `rabbitmqctl delete_queue <tên>` cần chạy. Cần hạn đổi
> linh hoạt thì dùng `publish(ttl=…)`.

> **TTL chỉ có tác dụng khi không ai đang nghe.** Consumer đang chạy sẽ lấy tin
> gần như tức thì, chẳng bao giờ kịp hết hạn. Hạn dùng là lưới an toàn cho lúc
> consumer chết, chậm, hoặc đang deploy — không phải công cụ điều tiết.

---

## `.retry` và `.dlq` là gì

Mặc định **không có** hai hàng đợi này — `@rabbitmq_subscriber(queue="alert-mailer")`
mọc ra đúng một hàng đợi `alert-mailer`, và tin nào handler xử lý hỏng thì bị bỏ
kèm log `mq.message_dropped`.

Bật thêm khi tin đáng tiền:

```python
@rabbitmq_subscriber("events", "alert.created", queue="alert-mailer",
            max_retries=2,          # -> thêm alert-mailer.retry
            retry_delay=5,
            dead_letter=True)       # -> thêm alert-mailer.dlq
```

Khi đó trên broker có **ba** hàng đợi, vai trò khác hẳn nhau:

| Hàng đợi | Ai bỏ tin vào | Ai lấy tin ra | Tin nằm đó nghĩa là |
|---|---|---|---|
| `alert-mailer` | exchange, mỗi khi có tin khớp routing key | **handler của bạn** | đang chờ tới lượt xử lý |
| `alert-mailer.retry` | khung, khi handler ném lỗi và còn lượt thử | **không ai** — tin tự hết hạn rồi quay về hàng đợi chính | đang đếm ngược `retry_delay` giây |
| `alert-mailer.dlq` | RabbitMQ, khi khung `reject` tin | **không ai** — nằm đó tới khi bạn xử lý tay | đã bỏ cuộc; cần người xem |

Điểm hay bị hiểu nhầm: **`.retry` và `.dlq` không có consumer nào**. Chúng là
chỗ *chứa*, không phải chỗ *xử lý*. Tin ra khỏi `.retry` là nhờ hết hạn, còn ra
khỏi `.dlq` thì phải có người can thiệp.

### Vòng đời một tin

Với `@rabbitmq_subscriber(..., max_retries=2, retry_delay=5, dead_letter=True)`:

```
publish
   |
   v
alert-mailer ──> handler chạy ──> xong          -> ack, tin biến mất. Hết.
                     |
                     | ném lỗi (lần 1)
                     v
              alert-mailer.retry  (nằm im 5 giây, không ai nghe)
                     |
                     | hết hạn -> RabbitMQ tự đẩy ngược về
                     v
alert-mailer ──> handler chạy lại (lần 2) ──> xong -> ack. Hết.
                     |
                     | ném lỗi (lần 2) -> lại .retry -> lần 3
                     v
                 hết lượt thử
                     v
              alert-mailer.dlq   (nằm đó cho tới khi bạn động vào)
```

`max_retries=2` nghĩa là **thử lại 2 lần**, tức handler chạy tối đa **3 lần** (1
lần đầu + 2 lần thử lại).

### Khi nào tin vào `.dlq`

(Bảng dưới đây giả định đã bật `max_retries` và `dead_letter=True`.)

| Tình huống | Vào `.dlq` sau | Vì sao |
|---|---|---|
| handler ném lỗi thường (`RuntimeError`, timeout, lỗi mạng...) | hết `max_retries` lượt | lỗi có thể là tạm thời, đáng thử lại |
| handler ném `PermanentMessageError` | **ngay lần đầu** | bạn đã khẳng định thử lại vô ích |
| payload không khớp model pydantic | **ngay lần đầu** | dữ liệu sai thì thử 100 lần vẫn sai |
| `dead_letter=False` **(mặc định)** | *không có `.dlq`* — tin **biến mất**, chỉ còn log `mq.message_dropped` | mặc định là không giữ gì cả |

Tin trong `.dlq` giữ nguyên body gốc, kèm header `x-attempt` cho biết đã thử mấy
lần. Không có header đó nghĩa là nó rớt ngay lần đầu (lỗi vĩnh viễn hoặc sai
khuôn).

> Vì sao không dùng `requeue=True` cho gọn: tin hỏng vĩnh viễn sẽ quay vòng liên
> tục ở tốc độ tối đa, ăn hết CPU và đẩy mọi tin lành ra sau. Đó là cách phổ
> biến nhất để làm sập một hệ thống hàng đợi.

### Xem trong `.dlq` có gì

```python
await broker.queue_info("alert-mailer.dlq")      # {"messages": 3, "consumers": 0}
await broker.peek("alert-mailer.dlq", limit=10)  # xem tin, KHÔNG lấy đi
```

`peek` trả tin lại chỗ cũ sau khi xem, nên gọi bao nhiêu lần cũng ra cùng kết
quả. Mỗi phần tử: `message_id`, `routing_key`, `headers` (có `x-attempt`),
`body`. Ngoài code thì dùng `rabbitmqctl list_queues name messages`.

Xử lý tin trong `.dlq` là **việc thủ công, có chủ đích** — sửa bug rồi đẩy tin
về hàng đợi chính bằng `broker.publish_to_queue("alert-mailer", body)`, hoặc xoá
đi nếu không cứu được. Khung cố ý không tự động làm việc này: tự đẩy về nghĩa là
lặp lại đúng vòng lỗi vừa thoát ra.

### Ví dụ chạy được

`src/api/rabbitmq_test/` có sẵn một consumer ba lối ra và hai endpoint để bấm
thử — không cần viết gì thêm:

```python
# src/api/rabbitmq_test/rabbitmq_consumer.py — TỰ BẬT cả hai hàng đợi phụ
@rabbitmq_subscriber("event.exchange_edge", "alert.created", queue="alert-mailer",
            max_retries=2, retry_delay=5, dead_letter=True)
async def event(self, payload: AlertCreated, meta: dict) -> None:
    if payload.kieu == "hong-vinh-vien":
        raise PermanentMessageError(...)       # -> .dlq ngay
    if payload.kieu == "hong-tam-thoi":
        raise RuntimeError(...)                # -> .retry -> ... -> .dlq
    log.info("alert.da_gui_mail", noi_dung=payload.message)   # -> ack
```

```bash
curl -X POST localhost:8002/api/rabbitmq-test/gui \
     -H 'Content-Type: application/json' \
     -d '{"message":"chao","kieu":"hong-tam-thoi"}'

curl localhost:8002/api/rabbitmq-test/hang-doi        # ba hàng đợi + nội dung .dlq
```

Bấm lần lượt ba `kieu` rồi theo dõi `hang-doi`, số đếm đi đúng như hình trên:

```
kieu=ok                       chinh=0  retry=0  dlq=0     <- ack, không để lại gì
kieu=hong-vinh-vien           chinh=0  retry=0  dlq=1     <- vào thẳng, không thử lại
kieu=hong-tam-thoi   +1s      chinh=0  retry=1  dlq=1     <- đang đếm ngược
                     +7s      chinh=0  retry=1  dlq=1     <- đã quay lại, hỏng tiếp
                     +13s     chinh=0  retry=0  dlq=2     <- hết 2 lượt -> bỏ cuộc
payload thiếu trường          chinh=0  retry=0  dlq=3     <- sai khuôn, vào ngay
```

Nội dung `.dlq` đọc được:

```
x-attempt=(không có)   {"message": "loi-vinh-vien", "kieu": "hong-vinh-vien"}
x-attempt=2            {"message": "smtp-chap-chon", "kieu": "hong-tam-thoi"}
x-attempt=(không có)   {"thieu_truong_message": 1}
```

> Hàng đợi đã tồn tại thì **không đổi tham số được** (RabbitMQ từ chối). Đổi
> `dead_letter`, `durable` hay `auto_delete` của một consumer đang chạy thì phải
> xoá hàng đợi cũ trước: `rabbitmqctl delete_queue <tên>`. Khung sẽ báo đúng lệnh cần chạy.

---

## Khi cần mọi worker cùng nhận một bản sao

`@rabbitmq_subscriber` chia tin cho các worker: nhiều worker cùng một tên hàng
đợi thì mỗi tin chỉ MỘT worker thấy. Cần ngược lại — mỗi worker một bản sao —
thì mỗi worker phải có hàng đợi RIÊNG. Hai cách:

**Cách gọn:** đặt tên hàng đợi kèm hostname rồi dùng
[`exchange_type="fanout"`](#fanout--mọi-hàng-đợi-một-bản-sao). Vẫn là
`@rabbitmq_subscriber` bình thường, có đủ retry/DLQ/TTL.

**Cách tay:** tự mở hàng đợi tự sinh tên — khi không muốn nghĩ về tên, và
không cần thứ gì của `@rabbitmq_subscriber`:

```python
channel = await broker.new_channel(prefetch=50)          # prefetch=20 nếu không truyền
queue = await broker.worker_queue(channel, "ten-goi-nho")  # tự sinh tên, tự xoá khi ngắt
await queue.bind(await broker.exchange("events"), routing_key="alert.#")
await queue.consume(callback)
```

| Hàm | Chữ ký | Ghi chú |
|---|---|---|
| `new_channel` | `(*, prefetch=20)` | mỗi thành phần nên một kênh riêng |
| `worker_queue` | `(channel, hint)` | hàng đợi riêng tiến trình, `hint` chỉ để dễ đọc log |
| `exchange` | `(name, kind=None)` | tạo nếu chưa có, luôn bền; `kind=None` = dùng lại kiểu đã khai, chưa có thì `topic`; `name=""` là exchange mặc định |
| `durable_queue` | `(channel, name, *, durable=True, dead_letter=False, auto_delete=False, message_ttl=None, queue_expires=None)` | hàng đợi có tên; `dead_letter=True` mới khai thêm `<name>.dlq`; hai TTL tính bằng giây |
| `retry_queue` | `(channel, name, target_queue, *, durable=True)` | hàng đợi chờ, không ai nghe |
| `queue_info` | `(name)` | `{"messages": n, "consumers": n}`, hoặc `None` nếu hàng đợi không tồn tại |
| `queue_exists` | `(name)` | như trên, rút gọn thành `True`/`False` |
| `peek` | `(name, *, limit=10)` | xem tin mà không lấy đi — để soi `.dlq` |
| `delete_queue` | `(name, *, if_unused=True)` | trả `False` nếu không có hoặc còn người nghe |
| `publish_to_queue` | `(queue, body, *, headers=None, expiration=None, persistent=True)` | gửi **bytes thô** thẳng vào một hàng đợi — dùng để đẩy tin từ `.dlq` về nguyên vẹn. Gửi payload thường thì dùng `publish("", queue, payload)` |

---

## Đẩy sự kiện xuống client WebSocket

Không có sẵn trong khung — viết ba dòng trong consumer của bạn:

```python
@injectable
class AlertPush:
    def __init__(self, ws: WebSocketServer) -> None:
        self._ws = ws

    @rabbitmq_subscriber("events", "alert.#", queue="alert-push")
    async def day_xuong_client(self, payload: dict) -> None:
        await self._ws.to_room("alerts", "alert.created", payload, namespace="/ws/chat")
```

Hàng đợi có tên nên chỉ **một** worker nhận mỗi tin, mà worker đó chỉ giữ một
phần số kết nối. Muốn mọi client đều nhận thì bật
[adapter Redis](websocket.md#8-chạy-nhiều-worker) cho WebSocket.

---

## Trạng thái và vòng đời

| Gọi | Trả về |
|---|---|
| `broker.enabled` | `bool` — `APP_RABBITMQ__ENABLED` |
| `broker.connected` | `bool` — đang thật sự nói chuyện được với broker |
| `broker.url` | URL đã che mật khẩu |
| `broker.stats()` | `{"enabled", "connected", "url", "exchanges"}` |
| `GET /api/health/ready` | có thêm khoá `rabbitmq` khi bật |

`startup()` và `shutdown()` do `lifespan` gọi sẵn, không phải tự gọi.

### Tự nối lại

Luôn bật, không có cách tắt.

| Tình huống | Hành vi |
|---|---|
| Broker chưa lên lúc app khởi động | App vẫn chạy và phục vụ HTTP; nối lại ngầm 2s → 4s → … → 30s |
| Mất kết nối giữa chừng | Tự nối lại, khôi phục exchange, hàng đợi, binding, consumer |
| Broker restart | Như trên; hàng đợi bền và tin trong đó còn nguyên |
| Đang mất kết nối mà gọi `publish` | `ServiceUnavailableError` → HTTP 503 |

Thiếu thư viện `aio-pika` mà `ENABLED=true` thì báo lỗi ngay lúc khởi động —
đó là lỗi cấu hình, không phải sự cố tạm thời.

---

## Số đo

```bash
curl -s localhost:8000/api/metrics | grep rabbitmq_
```

| Số đo | Nhãn | Ý nghĩa |
|---|---|---|
| `rabbitmq_published_total` | `exchange`, `routing_key` | tin đã đăng |
| `rabbitmq_publish_failed_total` | `exchange` | đăng hỏng |
| `rabbitmq_consumed_total` | `queue` | xử lý xong |
| `rabbitmq_consume_failed_total` | `queue` | handler ném lỗi |
| `rabbitmq_retried_total` | `queue` | đã hẹn thử lại |
| `rabbitmq_dead_lettered_total` | `queue` | vào hàng đợi chết |

---

## Lệnh hay dùng

```bash
fam install rabbitmq
fam info                        # cấu hình đang dùng
fam module alerts --consumer              # module mới kèm consumer
fam module alerts --consumer-only                 # thêm consumer vào module có sẵn

docker exec rabbit rabbitmqctl list_queues name messages consumers durable arguments
docker exec rabbit rabbitmqctl list_exchanges name type durable      # soi KIỂU exchange
docker exec rabbit rabbitmqctl list_consumers queue_name prefetch_count
docker exec rabbit rabbitmqctl list_bindings
docker exec rabbit rabbitmqctl delete_queue <tên>       # khi đổi TTL / dead_letter / durable
docker exec rabbit rabbitmqctl delete_exchange <tên>    # khi đổi KIỂU exchange
```

`arguments` trong `list_queues` là chỗ nhìn thấy `x-message-ttl`, `x-expires` và
`x-dead-letter-exchange` mà hàng đợi đang thật sự mang — đối chiếu với code khi
nghi ngờ hàng đợi cũ còn giữ tham số cũ.

Chạy test cần broker thật:

```bash
docker run -d --name rabbit-test -p 5673:5672 rabbitmq:3.13-management-alpine
TEST_RABBITMQ_URL=amqp://guest:guest@localhost:5673/ fam test
```
