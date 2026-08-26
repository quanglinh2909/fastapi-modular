# Việc chạy nền

Bốn thứ khác nhau, hay bị gộp làm một:

| | Chạy khi nào | Ai chạy | Dùng gì |
|---|---|---|---|
| **Theo lịch** | tới giờ là chạy | một handler | `@interval` · `@cron` · `@timeout` |
| **Theo yêu cầu** | khi có ai gửi việc vào | một handler, **tuần tự** | `@job` + `JobQueue.submit()` |
| **Vòng lặp sống mãi** | khi bạn gọi hàm | một handler, chạy mãi | `@worker` |
| **Báo cho nhiều nơi** | khi có việc gì đó xảy ra | **N handler, song song** | `@on_event` + `EventBus.emit()` |

Cả bốn đều **không cần hạ tầng gì** — không Redis, không RabbitMQ, không thêm
một dòng cấu hình. Có decorator thì chạy, không có thì thôi.

---

## Chọn cái nào

```
Cần MỘT nơi làm, hay BÁO cho nhiều nơi?
├── Báo cho nhiều nơi, chúng tự lo             -> @on_event
└── Một nơi làm
    ├── Chạy MÃI (đọc camera, giữ kết nối)     -> @worker
    └── Chạy xong rồi thôi
        ├── Cứ tới giờ là chạy                 -> @interval / @cron / @timeout
        └── Có người gửi vào
            ├── Mất việc thì chấp nhận được     -> @job
            └── Mất việc là hỏng nghiệp vụ      -> @rabbitmq_subscriber
```

Dấu hiệu nhận ra `@worker`: **có phần dựng ở TRƯỚC vòng lặp**. Mở camera, mở
socket, nạp model — thứ làm một lần rồi dùng lại suốt. `@interval` không giữ
được gì giữa hai lượt nên nó sẽ mở lại camera mỗi 5 giây.

Nhánh đầu tiên là ranh giới hay bị bỏ qua nhất. `@job` và `@on_event` trông
giống nhau — đều là "gửi cái gì đó đi rồi quên" — nhưng khác hẳn nhau ở chỗ ai
nhận:

| | `@job("detect")` | `@on_event("order.paid")` |
|---|---|---|
| Bao nhiêu handler | **đúng một**; trùng tên là lỗi | **bao nhiêu cũng được**; đó là điểm của nó |
| Chạy thế nào | xếp hàng, tuần tự | song song, không theo thứ tự |
| Có hàng đợi không | có, `max_queued` | không — gọi thẳng |
| Bên gửi nghĩ gì | "làm giúp tôi việc này" | "chuyện này vừa xảy ra, ai quan tâm thì lo" |

Nhánh cuối là chỗ chỉ có một cách trả lời trung thực: **`@job` giữ việc trong
RAM. App tắt hay chết là mất sạch phần chưa chạy.** Xem
[Việc nằm trong RAM](#việc-nằm-trong-ram).

---

## Vòng lặp sống mãi — `@worker`

Hình dạng quen thuộc: mỗi camera một luồng, khác nhau mỗi cái IP.

```python
from fastapi_modular import injectable, worker, WorkerContext


@injectable
class CameraService:
    def __init__(self, db: Database) -> None:
        self._db = db

    @worker("camera")
    async def watch(self, data: dict, ctx: WorkerContext) -> None:
        capture = await ctx.blocking(cv2.VideoCapture, data["ip"])   # DỰNG
        try:
            while ctx.running:                                        # VÒNG LẶP
                frame = await ctx.blocking(capture.read)
                events = await ctx.blocking(model.predict, frame)
                await self._db.save(events)                           # await như thường
        finally:
            await ctx.blocking(capture.release)                       # DỌN
```

**Gọi hàm là sinh ra một bản chạy nền**, trả về ngay. Khoá và dữ liệu truyền
vào lúc gọi:

```python
for camera in await self._db.all_cameras():
    await service.watch(camera.id, {"ip": camera.ip, "fps": camera.fps})
    #                   └ KHOÁ       └ DATA (dict, tuỳ bạn đặt gì)
```

Khoá là **danh tính của bản chạy**, không phải tên hàm và không lấy từ chữ ký.
Muốn dùng IP làm khoá cũng được, dùng id trong database cũng được — miễn là nó
duy nhất.

Tên worker (`"camera"` ở trên) không khai thì mặc định là `__qualname__`, tức
`CameraService.watch` — đã kèm tên lớp nên **hai method trùng tên ở hai lớp
khác nhau không đụng nhau**.

Gọi lại cùng một `key` **không** sinh bản thứ hai — nó trả về bản đang chạy.
Với camera thì đó là điều bắt buộc: mở hai kết nối RTSP tới cùng một thiết bị
là cách nhanh nhất để cả hai cùng giật.

Gọi được từ bất cứ đâu: lifespan lúc boot, một endpoint "thêm camera", hay một
`@interval` quét bảng camera mỗi phút và bật những cái mới.

### Khi nào cần `ctx`, khi nào không

Câu hỏi hay gặp: *không phải thread thì có cần `ctx` không?*

`ctx` **không phải chuyện của thread**. Nó là ba thứ khác nhau, và bạn chỉ khai
khi cần thứ đó:

| Cần gì | Lấy từ | Có ở |
|---|---|---|
| thoát vòng lặp cho sạch | `ctx.running` | mọi kiểu |
| gọi hàm CHẶN mà không giữ tiến trình | `await ctx.blocking(fn, …)` | `async def` |
| gọi hàm ASYNC từ trong thread | `ctx.run(coro)` | `thread=True` |

Suy ra:

| | Cần `ctx` không |
|---|---|
| `@worker` (`async def`) | **gần như luôn** — cần `ctx.running` để dừng, `ctx.blocking` để gọi hàm chặn |
| `@worker(thread=True)` | **luôn** — `ctx.running` để dừng, `ctx.run` để ghi database |
| `@interval` / `@cron` / `@timeout` (`async def`) | **không**, trừ khi có hàm chặn cần `ctx.blocking` |
| `@job` (`async def`) | **không**, trừ khi có hàm chặn |
| bất kỳ cái nào với `thread=True` | **có**, nếu cần gọi async (`ctx.run`) |

Không khai thì khung không truyền — cứ viết `async def lam(self) -> None` như
thường.

```python
@interval(seconds=60)
async def ping(self) -> None:              # không cần ctx
    await self._http.get("/health")

@interval(seconds=60)
async def scan(self, ctx: WorkerContext) -> None:
    await ctx.blocking(os.scandir, "/data")  # có hàm chặn -> cần ctx
```

### `ctx.running` — cách duy nhất thoát cho sạch

Viết `while True:` thì lúc tắt app khung phải huỷ ngang, và với `thread=True`
thì nó còn **không huỷ được** — chỉ đợi rồi bỏ mặc. `while ctx.running:` hoá
False ngay khi có lệnh dừng, và phần `finally` của bạn chạy bình thường.

Cần ngủ trong vòng lặp thì dùng `ctx.wait(seconds)` thay `time.sleep()`:
`time.sleep(30)` giữ lúc tắt app thêm 30 giây, `ctx.wait(30)` thoát ngay.

### `ctx.blocking(...)` — vì sao phải bọc

`capture.read()`, `model.predict()`, `cv2.VideoCapture()` đều là hàm **chặn**.
Gọi thẳng trong `async def` thì cả tiến trình đứng im chờ chúng: mọi request
HTTP, mọi frame WebSocket, mọi worker khác. `async def` không tự cứu được —
`await` chỉ nhả quyền khi chờ I/O, còn đây là tính toán.

`await ctx.blocking(fn, *args)` đẩy lời gọi sang thread khác rồi chờ kết quả.
Đo được với 3 camera chạy song song: event loop trễ **0,001 giây**.

#### Bên dưới nó là gì

`ctx.blocking` đẩy việc vào một **pool thread dùng chung**. Ba điều đáng biết:

- **Thread được TÁI SỬ DỤNG**, không mở mới mỗi lần gọi. Gọi một triệu lần
  cũng không sinh một triệu thread.
- **Pool có trần**: mặc định `min(32, số nhân + 4)` — trên máy 12 nhân là 16
  chỗ. Nhiều worker gọi dày hơn số chỗ thì chúng xếp hàng chờ nhau: **chậm
  đi**, không hỏng. Nới bằng `APP_WORKERS__THREAD_POOL_SIZE`.
- **Thread trong pool là `daemon`**, và điều đó không phải chi tiết vụn vặt.
  `ThreadPoolExecutor` của thư viện chuẩn dùng thread thường, mà lúc thoát
  Python **JOIN mọi thread thường — không timeout, không cách nào bỏ qua**. Chỉ
  cần một `capture.read()` treo trên luồng RTSP đã chết là tiến trình không
  thoát được nữa: Ctrl+C bấm bao nhiêu lần cũng chỉ in ra một
  `KeyboardInterrupt` trong `t.join()` rồi lại chờ tiếp, và người ta phải
  `kill -9`. Vì vậy khung dùng pool riêng với thread daemon.

  Lời gọi treo thì vẫn treo — Python không có cách nào giết một thread đang kẹt
  — nhưng nó không kéo cả tiến trình theo. **Hàm chặn nào có tham số timeout
  thì hãy đặt nó** (`cv2.CAP_PROP_OPEN_TIMEOUT_MSEC`, `socket.settimeout`,
  `requests.get(..., timeout=...)`); đó là cách duy nhất lấy lại được cái chỗ
  trong pool.

Còn **thân của `@worker`/`@job`/`@interval` khai `thread=True` thì KHÔNG dùng
pool đó** — mỗi lượt chạy một thread daemon riêng. Khác biệt này quan trọng, và
tôi đã mắc đúng lỗi ấy hai lần:

- Vòng lặp chạy mãi mà mượn pool chung thì nó giữ chỗ **vĩnh viễn**. Đủ 16
  worker là pool cạn sạch và mọi `ctx.blocking` treo cứng — đo được 20 worker
  thì cả tiến trình chết, không phải chậm.
- `@job(thread=True)` mượn pool chung là thread **không phải daemon**. Một việc
  không chịu kết thúc là tiến trình không bao giờ thoát nổi.

Sau khi cho mỗi lượt một thread riêng:

| Worker `thread=True` | Thread tiến trình | `ctx.blocking(0.01s)` mất |
|---|---|---|
| 4 | 6 | 0,013s |
| 12 | 14 | 0,013s |
| 20 | 22 | 0,015s *(trước khi sửa: treo vĩnh viễn)* |
| 40 | 42 | 0,015s |

Cái giá là mỗi lượt chạy tốn một lần dựng thread — cỡ chục micro giây, không
đáng kể với một việc mà bạn đã chọn đẩy sang thread.

### Hai kiểu chạy — có ở CẢ BỐN decorator

`thread=True` không riêng của `@worker`: `@interval`, `@cron`, `@timeout` và
`@job` đều nhận, và đều tiêm `ctx` như nhau.

```python
@interval(seconds=5, thread=True)
def quet_o_dia(self, ctx: WorkerContext) -> None:   # def thường
    ket_qua = quet_cham()                            # hàm chặn, gọi thẳng
    ctx.run(self._db.save(ket_qua))                  # ghi database

@job("detect", thread=True)
def nhan_dang(self, payload: dict, ctx: WorkerContext) -> None:
    events = model.predict(payload["path"])
    ctx.run(self._db.save(events))
```

|  | `@worker(...)` | `@worker(..., thread=True)` |
|---|---|---|
| Hàm khai bằng | `async def` | `def` thường |
| Chạy ở đâu | vòng lặp sự kiện | một thread riêng |
| Ghi database | `await self._db.save(...)` | `ctx.run(self._db.save(...))` |
| Gọi hàm chặn | `await ctx.blocking(fn, ...)` | gọi thẳng |
| Dừng được giữa chừng | có | **không** — phải đợi lời gọi chặn trả về |

**Mặc định (`async def`) đúng cho gần hết mọi trường hợp**, kể cả camera + AI.

`thread=True` chỉ đáng dùng khi vòng lặp gọi hàm chặn liên tục và dày, tới mức
bọc từng lời gọi thành rườm rà:

```python
@worker("camera", thread=True)
def watch(self, data: dict, ctx: WorkerContext) -> None:
    capture = cv2.VideoCapture(data["ip"])          # đang ở thread, gọi thẳng
    try:
        while ctx.running:
            frame = capture.read()
            events = model.predict(frame)
            ctx.run(self._db.save(events))          # cầu nối sang event loop
    finally:
        capture.release()
```

`ctx.run(coro)` là câu trả lời cho **"chạy trong thread thì ghi database kiểu
gì"**. Không dùng `asyncio.run()` ở đó: nó tạo một event loop MỚI, mà connection
pool của database thuộc về loop cũ — hỏng theo những cách rất khó hiểu.

### Hiệu năng: `ctx` tốn bao nhiêu

Câu hỏi thật là "worker ghi database / bắn message queue / đẩy WebSocket thì có
chậm hơn viết thẳng trong HTTP handler không". Đo trên máy dev, cùng một lời
gọi, một lần `await` thẳng và một lần qua `ctx.run(...)` từ thread:

| Lời gọi | `await` thẳng | qua `ctx.run` | Chênh |
|---|---|---|---|
| coroutine rỗng *(chi phí trần)* | 20.000.000/s | 50.700/s | **+0,020 ms** |
| WebSocket broadcast | 1.351.000/s | 47.600/s | **+0,020 ms** |
| MQTT emit qos=1 | 7.494 tin/s | 5.959 tin/s | ~0 |
| Kafka emit acks=all | 1.745 tin/s | 4.299 tin/s | ~0 |
| RabbitMQ emit `persistent=False` | 4.319 tin/s | 4.563 tin/s | ~0 |
| RabbitMQ emit `persistent=True` *(mặc định)* | 126 tin/s | 118 tin/s | ~0 |
| SQLite INSERT *(WAL+NORMAL)* | 1.269 ghi/s | 811 ghi/s | +0,4 ms |

Hai dòng RabbitMQ chênh nhau 34 lần **không phải vì `ctx.run`**: tin
`persistent` vào hàng đợi `durable` bắt RabbitMQ fsync xuống đĩa rồi mới xác
nhận, và vòng `for` chỉ để một tin bay mỗi lần. Cách sửa là `broker.emit_many(...)`
— xem [rabbitmq.md](rabbitmq.md#gửi-nhiều-tin-một-lúc).

Đọc bảng này theo đúng một cách: **`ctx.run` tốn cố định khoảng 0,02 ms.**

Nó là chi phí chuyển một lời gọi từ thread sang vòng lặp sự kiện rồi chờ kết
quả — `asyncio.run_coroutine_threadsafe`, không phải mở thread mới. Trần của nó
là khoảng **50.000 lời gọi/giây**, và mọi thứ đụng tới ổ đĩa hay mạng đều chậm
hơn thế hàng chục lần. Với MQTT/Kafka nó còn không đo được, vì sai số giữa hai
lần chạy lớn hơn chính nó.

Chỉ MỘT chỗ nó lộ ra: khi công việc bên dưới nhanh cỡ 0,1–0,5 ms, như một
INSERT SQLite ở chế độ WAL. Ở đó 0,4 ms chuyển giao chiếm được một phần đáng
kể — nhưng cách sửa không phải là bỏ `ctx.run`, mà là **gộp nhiều lời gọi thành
một**:

```python
@worker("camera", thread=True)
def watch(self, data: dict, ctx: WorkerContext) -> None:
    lo = []
    while ctx.running:
        lo.append(model.predict(capture.read()))
        if len(lo) >= 50:                      # một lần đi loop cho 50 sự kiện
            ctx.run(self._repo.save_many(lo))  # thay vì 50 lần
            lo.clear()
```

Còn với worker `async def` thì không có chi phí nào cả: `await self._repo.save()`
đúng bằng viết trong HTTP handler, vì nó chạy trên cùng vòng lặp đó.

**`ctx.blocking(...)` thì khác hẳn** — nó đắt hơn nhiều so với `ctx.run`, vì
mỗi lần gọi là một lượt bàn giao qua pool thread. Đừng bọc những thứ vốn đã
nhanh (`json.dumps`, số học); bọc đúng cái chặn thật: đọc frame, chạy model,
gọi HTTP đồng bộ.

### SQLite: đọc kỹ nếu worker ghi liên tục

SQLite chịu được worker ghi liên tục, nhưng có hai điều phải biết trước:

- **Mặc định của khung là WAL + synchronous=NORMAL** (1.376 ghi/s), không phải
  mặc định gốc của SQLite (68 ghi/s). Nếu bạn đặt ba biến `APP_DB__SQLITE_JOURNAL_MODE`,
  `APP_DB__SQLITE_SYNCHRONOUS`, `APP_DB__SQLITE_BUSY_TIMEOUT_SECONDS` về giá trị
  gốc thì worker sẽ chậm đúng 20 lần.
- **Tổng thông lượng ghi không tăng theo số worker.** SQLite chỉ có một người
  ghi tại một thời điểm; 12 tiến trình cùng ghi cũng chỉ bằng 1. Đo được 0 lỗi
  ở mọi mức thử — người thứ hai chờ chứ không lỗi — nhưng cũng không nhanh hơn.
- **App bị `kill -9` không làm hỏng file, cũng không mất dòng nào đã ghi** (đo
  75 lần giết giữa lúc worker đang ghi). Mất điện thì file vẫn không hỏng nhưng
  có thể mất các sự kiện gần nhất — nếu camera không được phép mất sự kiện nào
  thì đặt `APP_DB__SQLITE_SYNCHRONOUS=FULL` và chấp nhận 95 ghi/s.

Chi tiết, bảng số và ngưỡng nên chuyển sang PostgreSQL: [database.md](database.md#tốc-độ-ghi-và-vì-sao-mặc-định-ở-đây-khác-sqlite-gốc).

### Hỏng thì dựng lại

Vòng lặp ném lỗi thì khung ghi log rồi dựng lại sau một khoảng chờ tăng dần
(1s, 2s, 4s… tới `max_restart_delay`). Camera rớt mạng là chuyện thường ngày,
và một vòng lặp chết im lặng thì không ai biết cho tới lúc có người hỏi "sao
camera 12 không lên sự kiện nữa".

Vòng lặp **tự kết thúc** (bạn `return` hoặc `ctx.running` hoá False) thì không
dựng lại — đó là bạn chủ động, khung không cãi. Muốn hỏng là dừng hẳn thì
`restart=False`.

### Dừng một bản, và dọn dẹp

Gọi thẳng trên chính method mang `@worker` — không phải nhắc lại tên worker
dưới dạng chuỗi ở chỗ gọi, nên đổi tên worker thì không sót chỗ nào:

```python
await self.watch.stop(device_id)      # dừng MỘT bản, chờ nó dọn xong
await self.watch.stop_all()           # dừng mọi bản, trả về số bản đã dừng
self.watch.running()                  # [{"worker", "key", "uptime_seconds", "running"}]
self.watch.is_running(device_id)      # True/False
```

**`stop()` chờ tới lúc vòng lặp thoát hẳn** — tức là sau `finally:` trong thân
hàm. Nên chỗ dọn dẹp có hai nơi, và ranh giới giữa chúng rõ ràng:

| Dọn cái gì | Viết ở đâu |
|---|---|
| Tài nguyên của chính vòng lặp (camera, file, kết nối) | `finally:` trong thân worker |
| Việc nghiệp vụ đi kèm (xoá bản ghi, báo cho nơi khác) | sau `await ...stop(key)` |

`finally:` chạy **mọi lần** vòng lặp kết thúc — bị dừng, tự `return`, hay ném
lỗi — nên tài nguyên phải đóng ở đó. Việc nghiệp vụ thì không: gỡ thiết bị khỏi
database chỉ đúng khi người dùng gỡ thiết bị, chứ không phải mỗi lần camera rớt
mạng và worker dựng lại.

```python
@injectable
class DeviceService:
    def __init__(self, repo: Repository[Device]) -> None:
        self._repo = repo

    @worker("camera")
    async def watch(self, data: dict, ctx: WorkerContext) -> None:
        capture = await ctx.blocking(cv2.VideoCapture, data["ip"])
        try:
            while ctx.running:
                frame = await ctx.blocking(capture.read)
                await self._repo.save(...)
        finally:
            await ctx.blocking(capture.release)     # LUÔN chạy

    async def remove(self, device_id: str) -> None:
        # Dừng TRƯỚC: dòng dưới chỉ chạy khi camera đã đóng và vòng lặp đã im,
        # nên không có chuyện worker ghi thêm sự kiện cho một thiết bị vừa xoá.
        await self.watch.stop(device_id)
        await self._repo.delete(device_id)
        await self._notify(f"đã gỡ {device_id}")
```

`stop()` trả về `False` nếu không có bản nào mang khoá đó — dùng nó để biết là
gọi thừa, chứ nó không ném lỗi.

Cần dừng từ một chỗ không giữ instance của service thì tiêm `WorkerPool`:

```python
def __init__(self, workers: WorkerPool) -> None:
    self._workers = workers

async def stop_everything(self) -> None:
    await self._workers.stop("camera", device_id)   # tên worker dạng chuỗi
    await self._workers.stop_kind("camera")         # mọi khoá của loại này
    self._workers.stats()                           # {"count", "instances"}
```

Lúc tắt app khung tự gọi `stop_all()` **đầu tiên**, trước cả scheduler và hàng
đợi việc: worker là vòng lặp sống mãi, còn chạy là còn dùng database.

### `@worker(...)`

```python
@worker(
    name="",                  # tên loại worker; mặc định là __qualname__
    *,
    thread=False,
    restart=True,
    restart_delay=1.0,
    max_restart_delay=30.0,
    single=False,             # chỉ MỘT tiến trình chạy bản này
)
```

Chữ ký hàm: `(self)`, `(self, data)`, `(self, ctx)` hoặc `(self, data, ctx)`.
Gọi: `await self.watch(key, data)` — cả hai đều tuỳ chọn.

`single=True` khoá theo `<name>:<key>`, dùng chung cơ chế với
[việc theo lịch](#cái-bẫy-nhiều-worker). Đặt khi chạy nhiều worker uvicorn mà
chỉ muốn một tiến trình nối tới mỗi thiết bị.

---

## Theo lịch

```python
from fastapi_modular import injectable, interval, cron, timeout


@injectable
class CameraService:
    @interval(seconds=5)
    async def update_status(self) -> None:
        """Chạy lặp mỗi 5 giây."""

    @cron("0 3 * * *", timezone="Asia/Ho_Chi_Minh")
    async def clean_old_logs(self) -> None:
        """3 giờ sáng hàng ngày, GIỜ VIỆT NAM."""

    @timeout(seconds=10)
    async def warm_cache(self) -> None:
        """Chạy ĐÚNG MỘT LẦN, 10 giây sau khi app khởi động."""
```

Handler **không nhận tham số nào ngoài `self`** — việc theo lịch tự chạy, không
ai truyền gì vào. Cần dữ liệu thì lấy qua DI ở `__init__` như service thường.

### Cái bẫy: nhiều worker

`fam run` mặc định bật **4 worker**, tức 4 tiến trình Python độc lập, mỗi tiến
trình nạp đủ code của bạn. Một vòng `while True: await sleep(5)` viết tay sẽ
chạy **bốn lần mỗi 5 giây**:

- ghi log thành 4 bản
- gọi API ngoài tốn 4× quota
- cập nhật trạng thái camera thì 4 tiến trình ghi đè nhau

Mặc định `single=True` lo việc đó: **một tiến trình giành quyền và giữ**, ba
tiến trình kia đứng chờ. Tiến trình đang chạy chết thì một trong ba lên thay
trong vòng `APP_SCHEDULER__TAKEOVER_SECONDS` giây (mặc định 5).

Đo được, 4 tiến trình chạy `@interval(seconds=0.25)` trong 1,1 giây:

| | Số lượt chạy | Số tiến trình chạy |
|---|---|---|
| `single=True` (mặc định) | **5** | **1** |
| `single=False` | 20 | 4 |

Khoá chọn tự động:

| Đang bật | Khoá dùng | Tầm với |
|---|---|---|
| Redis | `SET NX EX` trên Redis | **nhiều máy** |
| (mặc định) | `flock` trên file tạm | một máy |

`flock` có một tính chất mà khoá Redis không có: **tiến trình chết là khoá tự
nhả**, vì nhân hệ điều hành đóng file descriptor hộ — không có khoá kẹt, không
phải đoán TTL. Đổi lại nó chỉ biết tới một máy.

Log khởi động nói rõ đang dùng cái nào:

```
scheduler.started  jobs=3  lock='một máy (flock trong /tmp)'
scheduler.owner    job=CameraService.update_status
scheduler.standby  job=CameraService.update_status  hint='tiến trình khác đang chạy việc này'
```

> Chạy `fam worker` (một tiến trình duy nhất) hoặc 1 replica trên k8s thì đặt
> `APP_SCHEDULER__SINGLE=false` cho gọn — không có gì để tranh.

### `@interval(...)`

```python
@interval(
    seconds,                # chu kỳ
    *,
    name="",                # tên hiển thị và tên KHOÁ; mặc định Class.method
    single=True,
    run_on_startup=False,   # True = chạy ngay, không đợi hết một chu kỳ
    jitter=0.0,             # cộng ngẫu nhiên 0..jitter giây vào mỗi lần chờ
    max_seconds=None,       # trần thời gian MỘT lượt
)
```

**Nhịp đếm từ lúc lượt trước CHẠY XONG**, không phải từ lúc bắt đầu. Việc mất
2 giây với `seconds=5` thì thực tế là 7 giây một lần. Đổi lại hai lượt không
bao giờ chồng nhau. Cần đúng nhịp tuyệt đối thì dùng `@cron`.

`max_seconds` đáng đặt cho mọi việc có gọi mạng: không đặt thì một lượt treo sẽ
làm việc này **im vĩnh viễn** mà không có gì báo. Có đặt thì lượt treo bị huỷ,
ghi log `scheduler.run_timeout`, và lượt sau vẫn chạy.

`jitter` dành cho lúc nhiều máy cùng gọi một API ngoài — không có nó thì cả đàn
đập vào cùng một giây.

### `@cron(...)`

```python
@cron(
    "0 3 * * *",
    *,
    timezone="UTC",         # ĐỌC KỸ dòng dưới
    name="", single=True, max_seconds=None,
)
```

> **Mặc định là UTC.** `"0 3 * * *"` với mặc định này chạy lúc **10 giờ sáng
> giờ Việt Nam**. Ý bạn là 3 giờ sáng giờ ta thì truyền
> `timezone="Asia/Ho_Chi_Minh"`.

Để sai lệch lộ ra ngay, log khởi động in lần chạy kế tiếp ở **cả hai** múi giờ:

```
scheduler.job  job=...  schedule='0 3 * * * (UTC)'
               next_run='2026-08-26T03:00:00+00:00'
               next_run_local='2026-08-26T10:00:00+07:00'
```

Năm trường, và các lối tắt `@hourly` `@daily` `@weekly` `@monthly` `@yearly`:

```
┌─ phút (0-59)
│ ┌─ giờ (0-23)
│ │ ┌─ ngày trong tháng (1-31)
│ │ │ ┌─ tháng (1-12)
│ │ │ │ ┌─ thứ (0-6, 0 = Chủ nhật)
* * * * *
```

Mỗi trường nhận `*`, một số, `a-b`, `a,b,c`, `*/n`, `a-b/n`.

**Luật ngày/thứ của cron gốc, chỗ phản trực giác nhất:** cả hai tập luôn được
kiểm, và ký tự `*` ở **đầu trường** quyết định nối chúng bằng VÀ hay HOẶC.

| Biểu thức | Nghĩa |
|---|---|
| `0 0 1 * 1` | ngày 1 hàng tháng **HOẶC** mọi thứ Hai *(không trường nào có `*`)* |
| `0 0 */7 * 1` | ngày 1,8,15,22,29 **VÀ** phải đúng thứ Hai *(ngày bắt đầu bằng `*`)* |
| `0 0 1 * *` | ngày 1 hàng tháng |
| `0 0 * * 1` | mọi thứ Hai |

Biểu thức sai bị chặn **ngay lúc khai báo**, không đợi tới lúc chạy. Kể cả loại
hợp lệ về cú pháp nhưng không bao giờ xảy ra (`0 0 30 2 *` — 30 tháng Hai).

### `@timeout(...)`

Chạy đúng một lần, `seconds` giây sau khi app khởi động. Dùng để hâm nóng: nạp
cache, dựng sẵn kết nối, kiểm tra một lần.

Việc phải chạy **ngay** lúc boot thì đừng dùng cái này — đặt thẳng vào lifespan
của dự án, vì ở đó bạn chặn được app nhận request cho tới khi xong. Xem
[architecture.md](architecture.md).

---

## Theo yêu cầu — `@job`

```python
from fastapi_modular import injectable, job, JobQueue


@injectable
class ImageService:
    @job("detect")
    async def detect(self, payload: dict) -> None:
        await run_yolo(payload["path"])


@injectable
class UploadService:
    def __init__(self, jobs: JobQueue) -> None:
        self._jobs = jobs

    async def upload(self, path: str) -> dict:
        await self._jobs.submit("detect", {"path": path})   # trả về NGAY
        return {"trang_thai": "đang xử lý"}
```

Chỉ dùng `asyncio.Queue` của Python. `submit()` trả về ngay, không chờ việc
chạy xong.

### Việc nằm trong RAM

**App tắt hay chết là mất sạch việc còn trong hàng đợi.** Không có cách nào
lách: tiến trình biến mất thì bộ nhớ của nó biến mất theo. Đây là bản chất của
hàng đợi trong tiến trình, không phải thiếu sót của khung.

| Hợp với `@job` | KHÔNG hợp |
|---|---|
| ghi log, cập nhật thống kê | trừ tiền, tạo đơn |
| gửi thông báo đẩy | gửi mail xác nhận đơn |
| sinh ảnh thu nhỏ | bất cứ việc gì mất đi thì khách phải gọi tổng đài |
| chạy nhận dạng cho ảnh **đã nằm trên đĩa** | |

Cột phải cần [`@rabbitmq_subscriber`](rabbitmq.md) — hàng đợi bền, có thử lại,
có `.dlq`.

Lúc tắt, khung **chạy nốt** hàng đợi trong `APP_JOBS__DRAIN_SECONDS` giây; phần
không kịp thì ghi log kèm con số, không im lặng:

```
jobs.dropped_on_shutdown  count=17
```

### Tuần tự tới đâu

Mặc định `workers=1`: **đúng một việc chạy tại một thời điểm, theo thứ tự gửi
vào**. Đây thường là thứ người ta muốn khi nói "xử lý tuần tự".

`APP_JOBS__WORKERS=4` thì bốn việc chạy song song và **thứ tự không còn bảo
đảm**.

Thường thứ bạn cần không phải tuần tự toàn cục mà là **tuần tự theo từng
camera**: hai camera chạy song song, hai việc của cùng một camera thì đúng thứ
tự. Với hàng đợi trong tiến trình, cách đơn giản nhất là `workers=1`. Cần song
song thật theo khoá thì đó là lúc dùng Kafka (`key=ma_camera` — cùng key thì
cùng phân vùng, và phân vùng bảo đảm thứ tự).

### Hàng đợi đầy

`submit()` **ném `ServiceUnavailableError` (503) chứ không chờ**:

```
Hàng đợi việc đầy (1000 chỗ) nên không nhận thêm 'detect'. Bên chạy việc
đang chậm hơn bên gửi — tăng APP_JOBS__WORKERS, hoặc chuyển sang hàng đợi bền.
```

Đây là chỗ áp lực ngược lộ ra. Hàng đợi đầy nghĩa là bên tiêu thụ chậm hơn bên
gửi, và giấu điều đó bằng cách chờ chỉ làm request treo theo.

Muốn chờ thật thì `submit(..., wait=True)` — chỉ dùng khi bên gọi **chấp nhận
bị chậm lại**, ví dụ một vòng nạp dữ liệu chạy nền, không phải trong HTTP
handler.

### Việc nặng — YOLO và bạn bè

```python
@job("detect", thread=True)
def detect(self, payload: dict, ctx: WorkerContext) -> None:   # `def` thường
    events = model.predict(payload["path"])
    ctx.run(self._db.save(events))            # ghi database từ trong thread
```

`thread=True` chạy handler trong một **thread** thay vì trên vòng lặp sự
kiện. Việc này **có** tác dụng với torch/opencv/numpy vì phần tính toán của
chúng viết bằng C và **nhả GIL** trong lúc chạy. Nó **không** có tác dụng với
vòng lặp Python thuần — cái đó vẫn giữ GIL và vẫn làm nghẽn cả tiến trình.

Không có `thread=True` thì suy luận YOLO **chặn event loop**: mọi request HTTP
và mọi frame WebSocket đứng im chờ nó xong. `async def` không cứu được —
`await` chỉ nhả quyền khi chờ I/O, còn đây là tính toán.

Và dù có `thread=True`, chạy nhận dạng cùng tiến trình với API vẫn tranh CPU
với việc phục vụ request. Tải thật thì tách hẳn:

```
API  ──publish──▶  RabbitMQ  ──▶  tiến trình worker RIÊNG (@rabbitmq_subscriber)
                                   nạp model MỘT lần lúc boot
```

### `@job(...)`

```python
@job(
    name,                   # tên loại việc, dùng lúc submit; duy nhất trong app
    *,
    max_retries=0,          # thử lại NGAY TẠI CHỖ khi handler ném lỗi
    retry_delay=1.0,
    thread=False,           # chạy trong thread; hàm khai bằng `def` thường
)
```

`max_retries` mặc định 0 — hỏng là ghi log rồi bỏ. Nhớ rằng thử lại **làm đứng
cả hàng đợi** khi `workers=1`.

Tham số đầu chú kiểu bằng model Pydantic thì payload được kiểm khuôn trước khi
vào handler; sai khuôn thì ghi log và bỏ việc đó, không làm chết worker.

---

## Báo cho nhiều nơi — `@on_event`

```python
from fastapi_modular import EventBus, injectable, on_event

@injectable
class OrderService:
    def __init__(self, events: EventBus) -> None:
        self._events = events

    async def pay(self, order_id: str) -> None:
        await self._repo.mark_paid(order_id)
        await self._events.emit("order.paid", {"id": order_id})

@injectable
class MailService:
    @on_event("order.paid")
    async def send_receipt(self, data: dict) -> None: ...

@injectable
class StatsService:
    @on_event("order.paid")
    async def count(self, data: dict) -> None: ...
```

Hai handler đó chạy **song song**, và `OrderService` **không biết chúng tồn
tại**. Thêm một nơi nghe là thêm một method, không phải sửa chỗ phát. Đó là
toàn bộ giá trị của kiểu này — và cũng là lý do đừng dùng nó khi bên phát CẦN
biết kết quả.

Đây là `fanout` của RabbitMQ, hoặc observer/`EventEmitter` của NestJS — nhưng
**chỉ trong một tiến trình**, không qua mạng, không cần cài gì.

### Hai cách phát

```python
await bus.emit("order.paid", data)   # CHỜ mọi nơi nghe xong, trả về số handler chạy trót lọt
bus.dispatch("order.paid", data)     # trả về NGAY, handler chạy nền, trả về số nơi sẽ chạy
```

`emit` khi bên phát cần mọi thứ xong trước khi đi tiếp — ví dụ trước khi trả
lời HTTP. `dispatch` khi không cần, và đây mới là cái hay dùng: **một request
không nên chậm đi chỉ vì có thêm người đăng ký nghe**.

`dispatch` giữ nguyên request-id của bên phát, nên log của handler nền vẫn nối
được về đúng request đã sinh ra nó.

### Ký tự đại diện

Tên sự kiện ngăn bằng dấu chấm, và mẫu dùng đúng luật của RabbitMQ topic:

| Mẫu | Khớp | Không khớp |
|---|---|---|
| `order.paid` | `order.paid` | `order.shipped` |
| `order.*` | `order.paid` | `order.item.added` — `*` là ĐÚNG một tầng |
| `order.#` | `order.paid`, `order.item.added`, `order` | |
| `camera.*.motion` | `camera.12.motion` | `camera.motion` |

Đại diện chỉ dùng khi **nghe**. Phát `emit("order.*")` bị chặn ngay — phát một
mẫu thì không ai biết là ý gì.

### Một nơi nghe hỏng thì sao

Ghi log rồi thôi; **những nơi khác vẫn chạy**. Bắt buộc phải vậy: gửi mail hỏng
mà kéo theo không cập nhật được thống kê là vô lý.

```python
so = await bus.emit("order.paid", data)
if so < len(bus.listeners("order.paid")):
    ...            # có ai đó hỏng — chi tiết nằm trong log events.handler_failed
```

**Không có thử lại.** Cần thử lại, cần chạy nốt sau khi app khởi động lại — thì
chính handler đó nên đẩy việc sang `@job` (mất được) hoặc RabbitMQ (không mất
được). Đừng chờ lớp này lo giúp.

Sợ một nơi nghe treo làm treo cả `emit` thì đặt hạn:

```python
@on_event("order.paid", max_seconds=2.0)
async def goi_api_ngoai(self, data: dict) -> None: ...
```

### Nghe lúc đang chạy, không qua decorator

```python
bo_nghe = bus.subscribe("camera.*.motion", ham_cua_toi)
...
bo_nghe()
```

`@on_event` đủ cho gần hết mọi trường hợp. Cái này dành cho nơi nghe chỉ sống
một lúc: một phiên WebSocket, một lần chờ trong test, một tính năng bật/tắt
theo cấu hình.

### `thread=True` và `ctx`

Giống hệt ba decorator kia:

```python
@on_event("anh.moi", thread=True)
def nhan_dang(self, data: dict, ctx: WorkerContext) -> None:
    ket_qua = model.predict(data["path"])       # đang ở thread, gọi thẳng
    ctx.run(self._repo.save(ket_qua))           # cầu nối sang event loop
```

### Ranh giới: chỉ trong MỘT tiến trình

`fam run --workers 4` là **bốn tiến trình**. Sự kiện phát ở tiến trình 1 KHÔNG
tới tiến trình 2 — mỗi tiến trình có `EventBus` riêng của nó.

Đây không phải thiếu sót mà là định nghĩa: nó gọi thẳng hàm trong bộ nhớ, nên
không cần cài gì và nhanh hơn hẳn mọi đường đi qua mạng. Đo với handler rỗng:

| | Lượt phát/giây | Mỗi lượt phát |
|---|---|---|
| `emit`, 1 nơi nghe | 220.000 | 4,6 µs |
| `emit`, 3 nơi nghe | 38.000 | 26 µs |
| `emit`, 10 nơi nghe | 16.000 | 62 µs |
| `dispatch`, 3 nơi nghe | 69.000 | 14 µs *(chưa tính lúc chạy nền)* |

Số lượt phát giảm khi thêm nơi nghe là đương nhiên — mỗi nơi nghe là một
coroutine phải dựng và chờ. Nhìn theo tổng số lượt handler thì nó vẫn tăng:
1 nơi nghe 220.000 lượt/s, 10 nơi nghe 158.000 lượt/s. So với RabbitMQ
`emit` 126 tin/s thì khác nhau ba bậc — nhưng RabbitMQ đi được sang máy khác,
còn cái này thì không.

Muốn xuyên tiến trình thì đó là việc của broker:

| Cần | Dùng |
|---|---|
| trong một tiến trình | `@on_event` |
| mọi tiến trình đều nhận một bản | RabbitMQ `fanout`, hoặc `@redis_subscriber` |
| chia việc cho các tiến trình | `@rabbitmq_subscriber` |

Cầu nối giữa hai bên chỉ là một handler:

```python
@on_event("order.paid")
async def bao_ra_ngoai(self, data: dict) -> None:
    await self._mq.emit("order.paid", data, exchange="events",
                        exchange_type="fanout")
```

### `@on_event(...)`

```python
@on_event(
    "order.paid",       # tên sự kiện, hoặc mẫu có * / #
    *,
    thread=False,
    max_seconds=0.0,    # 0 = không giới hạn
)
```

Chữ ký hàm: `(self)`, `(self, data)`, `(self, data, ctx)`. Tham số `data` chú
kiểu bằng model Pydantic thì dữ liệu được kiểm khuôn trước khi vào handler, và
sai khuôn thì handler đó bị bỏ qua — những handler khác vẫn chạy.

---

## Cấu hình

Không cần đặt gì để chạy. Các biến dưới đây để chỉnh:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `APP_SCHEDULER__ENABLED` | `true` | tắt hẳn phần theo lịch |
| `APP_SCHEDULER__SINGLE` | `true` | chỉ một tiến trình chạy mỗi việc |
| `APP_SCHEDULER__LOCK_DIR` | *(thư mục tạm)* | nơi để file khoá; chỉ dùng khi không bật Redis |
| `APP_SCHEDULER__TAKEOVER_SECONDS` | `5.0` | bao lâu thử giành quyền lại một lần |
| `APP_SCHEDULER__RENEW_SECONDS` | `10.0` | nhịp gia hạn khoá Redis |
| `APP_SCHEDULER__DRAIN_SECONDS` | `15.0` | chờ lượt đang chạy dở khi tắt app |
| `APP_JOBS__ENABLED` | `true` | tắt hẳn hàng đợi việc |
| `APP_JOBS__MAX_QUEUED` | `1000` | sức chứa; đầy thì `submit()` ném 503 |
| `APP_JOBS__WORKERS` | `1` | số việc chạy song song; 1 = đúng thứ tự |
| `APP_JOBS__DRAIN_SECONDS` | `15.0` | chờ chạy nốt hàng đợi khi tắt app |
| `APP_WORKERS__MAX_INSTANCES` | `200` | trần số bản `@worker` chạy cùng lúc |
| `APP_WORKERS__STOP_SECONDS` | `20.0` | chờ worker thoát khi tắt app |
| `APP_WORKERS__SINGLE` | `true` | worker khai `single=True` thì có khoá thật hay không |
| `APP_WORKERS__TAKEOVER_SECONDS` | `5.0` | bản đang chờ thì bao lâu thử giành quyền lại |
| `APP_WORKERS__THREAD_POOL_SIZE` | `0` | số thread cho `ctx.blocking`; 0 = mặc định Python `min(32, nhân+4)` |
| `APP_EVENTS__ENABLED` | `true` | tắt hẳn `@on_event`; `emit`/`dispatch` không gọi ai |
| `APP_EVENTS__MAX_SECONDS` | `0.0` | hạn mặc định cho một lượt handler; 0 = không giới hạn |
| `APP_EVENTS__MAX_PENDING` | `1000` | trần số lượt `dispatch` chạy nền cùng lúc |
| `APP_EVENTS__DRAIN_SECONDS` | `5.0` | chờ handler nền chạy nốt khi tắt app |

---

## Số đo và chẩn đoán

| Số đo | Nhãn |
|---|---|
| `scheduled_runs_total` | `job` |
| `scheduled_failures_total` | `job` |
| `scheduled_skipped_total` | `job` |
| `scheduled_duration_seconds` | `job` |
| `jobs_submitted_total` / `jobs_done_total` / `jobs_failed_total` | `job` |
| `jobs_rejected_total` | `job` |
| `jobs_queued` | *(gauge — con số đáng theo dõi nhất)* |
| `workers_started_total` / `workers_restarted_total` | `worker` |
| `workers_running` | *(gauge)* |
| `events_emitted_total` | `event` |
| `event_handlers_done_total` / `event_handlers_failed_total` | `event` |
| `event_handler_duration_seconds` | `event` |
| `event_handlers_pending` | *(gauge)* |

| Thấy gì | Gần như luôn là |
|---|---|
| việc chạy nhiều lần mỗi nhịp | `single=False`, hoặc mỗi máy một `flock` riêng (cần Redis) |
| việc không chạy lần nào | tiến trình này đang `standby`; xem log `scheduler.owner` ở tiến trình khác |
| cron chạy lệch 7 tiếng | quên `timezone=` — mặc định là UTC |
| `jobs_queued` phình dần | bên chạy chậm hơn bên gửi; tăng `WORKERS` hoặc chuyển sang RabbitMQ |
| `scheduler.lock_lost` | Redis chớp, hoặc lượt chạy lâu hơn hạn khoá — việc có thể đang chạy hai nơi |
| `jobs.dropped_on_shutdown` | đúng như tên: bấy nhiêu việc đã mất khi tắt app |
| `workers_restarted_total` tăng đều | camera rớt mạng, hoặc vòng lặp ném lỗi mỗi lượt — xem log `worker.crashed` |
| `worker.stop_timeout` lúc tắt app | vòng lặp không kiểm `ctx.running`, hoặc lời gọi chặn không có timeout |
| API đứng hình khi worker chạy | quên bọc `ctx.blocking(...)` quanh hàm chặn |
| `ctx.blocking` chậm dần khi thêm worker | vượt trần pool — nới `APP_WORKERS__THREAD_POOL_SIZE` |
| phát sự kiện mà không thấy gì chạy | in `bus.listeners("ten.su.kien")` ra; thường là sai mẫu, hoặc class chưa `@injectable` |
| log `events.not_started` | phát trước khi lifespan chạy xong — chuyển lời gọi đó vào sau `startup` |
| log `events.dispatch_dropped` | bên nghe chậm hơn bên phát; chỗ đó cần `@job`, không phải `MAX_PENDING` cao hơn |
| **Ctrl+C bấm mà không có gì xảy ra** | xem mục ngay dưới |

## Ctrl+C như không ăn

Gần như luôn là **một `while True:` không có đường thoát**. Khung xin dừng,
vòng lặp không nghe, khung chờ hết `APP_WORKERS__STOP_SECONDS` (mặc định 20
giây) rồi mới bỏ mặc — và trong hai chục giây đó vòng lặp vẫn in ra màn hình
như chưa có gì xảy ra.

Khung nói ra cả hai đầu:

```
[warning] worker.endless_loop   name=camera function=CamService.watch
          hint='`while True:` không có đường thoát: ...'      ← lúc KHỞI ĐỘNG
[info]    worker.stopping       count=1 workers=['camera:test'] timeout=20.0
[info]    worker.stopping_still remaining=['worker-camera-test'] seconds_left=15.0
[warning] worker.stop_timeout   stuck=['camera:test']         ← lúc TẮT
```

Dòng `worker.endless_loop` in ra ngay lúc khởi động, trước khi bạn kịp gặp vấn
đề. Nó chỉ soi được `while True:`; vòng lặp thoát bằng `break` theo điều kiện
riêng thì khung không đọc được ý định nên không kêu, và cũng không chặn.

Sửa:

```python
# TRƯỚC — Ctrl+C phải chờ 20 giây
@worker("camera", thread=True)
def watch(self, data: dict, ctx: WorkerContext) -> None:
    while True:
        xu_ly()
        time.sleep(1)

# SAU — Ctrl+C thoát trong chưa tới một giây
@worker("camera", thread=True)
def watch(self, data: dict, ctx: WorkerContext) -> None:
    while ctx.running:          # hoá False ngay khi có lệnh dừng
        xu_ly()
        ctx.wait(1)             # tỉnh NGAY khi có lệnh dừng, khác time.sleep
```

Đo trên một app thật với cùng một worker:

| Vòng lặp viết kiểu | Ctrl+C tới lúc tiến trình thoát |
|---|---|
| `while True:` + `time.sleep(1)` | 21 giây |
| `while ctx.running:` + `ctx.wait(1)` | dưới 1 giây, `finally` chạy đủ |

Còn nếu vòng lặp có kiểm `ctx.running` mà vẫn kẹt, thì nó đang nằm trong một
**lời gọi chặn không chịu trả về** — `capture.read()` trên luồng đã chết,
`requests.get()` không đặt timeout. Python không giết được thread đang kẹt;
đường duy nhất là đặt timeout cho chính lời gọi đó. Nhưng nó sẽ **không** giữ
tiến trình lại nữa: mọi thread khung mở đều là daemon, nên quá hạn là app vẫn
thoát.
