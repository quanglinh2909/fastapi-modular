# Việc chạy nền

Ba thứ khác nhau, hay bị gộp làm một:

| | Chạy khi nào | Sống bao lâu | Dùng gì |
|---|---|---|---|
| **Theo lịch** | tới giờ là chạy | một lượt rồi thôi | `@interval` · `@cron` · `@timeout` |
| **Theo yêu cầu** | khi có ai gửi việc vào | một việc rồi thôi | `@job` + `JobQueue.submit()` |
| **Vòng lặp sống mãi** | khi bạn gọi hàm | tới khi bảo nó dừng | `@worker` |

Cả ba đều **không cần hạ tầng gì** — không Redis, không RabbitMQ, không thêm
một dòng cấu hình. Có decorator thì chạy, không có thì thôi.

---

## Chọn cái nào

```
Việc này chạy xong rồi thôi, hay chạy mãi?
├── Chạy MÃI (đọc camera, giữ kết nối, nghe cổng)      -> @worker
└── Chạy xong rồi thôi
    ├── Không ai gửi, cứ tới giờ là chạy               -> @interval / @cron / @timeout
    └── Có người gửi vào
        ├── Mất việc thì chấp nhận được                 -> @job
        └── Mất việc là hỏng nghiệp vụ                  -> @rabbitmq_subscriber
```

Dấu hiệu nhận ra `@worker`: **có phần dựng ở TRƯỚC vòng lặp**. Mở camera, mở
socket, nạp model — thứ làm một lần rồi dùng lại suốt. `@interval` không giữ
được gì giữa hai lượt nên nó sẽ mở lại camera mỗi 5 giây.

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

### Hỏng thì dựng lại

Vòng lặp ném lỗi thì khung ghi log rồi dựng lại sau một khoảng chờ tăng dần
(1s, 2s, 4s… tới `max_restart_delay`). Camera rớt mạng là chuyện thường ngày,
và một vòng lặp chết im lặng thì không ai biết cho tới lúc có người hỏi "sao
camera 12 không lên sự kiện nữa".

Vòng lặp **tự kết thúc** (bạn `return` hoặc `ctx.running` hoá False) thì không
dựng lại — đó là bạn chủ động, khung không cãi. Muốn hỏng là dừng hẳn thì
`restart=False`.

### Dừng và liệt kê

```python
@injectable
class CameraController:
    def __init__(self, workers: WorkerPool) -> None:
        self._workers = workers

    async def remove(self, ip: str) -> None:
        await self._workers.stop("watch", ip)

    async def status(self) -> list[dict]:
        return self._workers.running()   # [{"worker", "key", "uptime_seconds", "running"}]
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
