# Việc chạy nền

Trang này hướng dẫn cách làm những việc **không do người dùng gọi trực tiếp**:
tới giờ thì chạy, ai đó bấm nút rồi xử lý sau, một vòng lặp đọc camera suốt
ngày, hay một chuyện xảy ra và nhiều nơi cần biết.

Không phải cài gì thêm — không Redis, không RabbitMQ. Viết decorator lên method
là xong, `fam dev` là nó chạy.

---

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Cứ 5 giây kiểm tra trạng thái camera một lần" | [1. Lặp theo chu kỳ](#1-lặp-theo-chu-kỳ) |
| "**Vừa start là chạy luôn, đừng đợi hết chu kỳ**" | [`run_on_startup`](#chạy-ngay-lúc-app-lên-thay-vì-đợi-hết-chu-kỳ) |
| "3 giờ sáng hàng ngày dọn log" | [2. Chạy đúng giờ](#2-chạy-đúng-giờ) |
| "Hâm nóng cache sau khi app lên" | [3. Chạy đúng một lần](#3-chạy-đúng-một-lần) |
| "Người dùng tải ảnh lên, trả lời ngay, xử lý sau" | [4. Đưa việc vào hàng đợi](#4-đưa-việc-vào-hàng-đợi) |
| "Mỗi camera một luồng đọc RTSP chạy suốt" | [5. Vòng lặp chạy mãi](#5-vòng-lặp-chạy-mãi) |
| "Đơn thanh toán xong: gửi mail VÀ cộng thống kê VÀ ghi log" | [6. Báo cho nhiều nơi](#6-báo-cho-nhiều-nơi) |
| "Viết rồi mà không chạy / chạy 4 lần / Ctrl+C không thoát" | [Hỏng thì tra ở đây](#hỏng-thì-tra-ở-đây) |

Chưa chắc chọn cái nào thì đi theo cây này:

```
Có cần BÁO cho nhiều nơi cùng lúc không?
├── Có, mỗi nơi tự lo phần của mình           -> @on_event   (mục 6)
└── Không, chỉ một nơi làm
    ├── Chạy MÃI (đọc camera, giữ kết nối)    -> @worker     (mục 5)
    └── Chạy xong rồi thôi
        ├── Cứ tới giờ là chạy                -> @interval / @cron / @timeout
        └── Có người gửi vào
            ├── Mất việc thì chấp nhận được    -> @job        (mục 4)
            └── Mất việc là hỏng nghiệp vụ     -> RabbitMQ, xem rabbitmq.md
```

Hai cái hay bị nhầm với nhau nhất là `@job` và `@on_event`. Cả hai đều là "gửi
đi rồi quên", nhưng khác hẳn ở chỗ ai nhận:

| | `@job("detect")` | `@on_event("order.paid")` |
|---|---|---|
| Có bao nhiêu nơi nhận | **đúng một** — viết hai cái trùng tên là báo lỗi | **bao nhiêu cũng được** |
| Chạy thế nào | xếp hàng, lần lượt | cùng lúc, không thứ tự |
| Bạn đang nghĩ gì | "làm giúp tôi việc này" | "chuyện này vừa xảy ra, ai quan tâm thì lo" |

---

## 1. Lặp theo chu kỳ

### Làm thế nào

Viết vào service của bạn, ví dụ `src/api/cameras/camera_service.py`:

```python
from fastapi_modular import injectable, interval
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)


@injectable
class CameraService:
    @interval(seconds=5)
    async def update_status(self) -> None:
        log.info("camera.check")
```

Chạy `fam dev`. Trong log khởi động phải thấy nó được nhận:

```
scheduler.job      job=CameraService.update_status  schedule='mỗi 5.0s'
scheduler.started  jobs=1  lock='một máy (flock trong /tmp)'
scheduler.owner    job=CameraService.update_status
```

**Không thấy dòng `scheduler.job` nghĩa là khung không tìm ra method của bạn** —
gần như luôn vì class thiếu `@injectable`, hoặc file chưa được import.

### Chạy ngay lúc app lên, thay vì đợi hết chu kỳ

Mặc định `@interval` **đợi hết một chu kỳ rồi mới chạy lần đầu**. Với
`seconds=5` thì không ai để ý, nhưng với `seconds=300` thì app lên xong phải
chờ 5 phút mới thấy gì — và đó gần như không bao giờ là thứ bạn muốn:

```python
@interval(seconds=300, run_on_startup=True)
async def dong_bo(self) -> None:
    ...
```

Đo được (chu kỳ 3 giây cho dễ nhìn):

```
run_on_startup=True    chạy lúc 0.0s, rồi 3.01s
mặc định               chưa chạy gì lúc 1.0s; lần đầu là 3.01s
```

Chu kỳ vẫn tính từ lúc lượt trước **chạy xong**, không đổi.

> **Nhớ rằng lần chạy đầu rơi vào lúc app đang khởi động.** Việc nặng đặt
> `run_on_startup=True` sẽ tranh CPU với những thứ khác đang lên. Cần "hâm nóng
> một lần rồi thôi" thì đó là [`@timeout`](#3-chạy-đúng-một-lần), không phải
> `@interval`.

### Lưu ý

**Handler không được nhận tham số nào ngoài `self`.** Việc theo lịch tự chạy,
không ai truyền gì vào. Cần dữ liệu thì lấy qua `__init__` như service thường:

```python
@injectable
class CameraService:
    def __init__(self, repo: Repository[Camera]) -> None:
        self._repo = repo                      # ĐÚNG: lấy ở đây

    @interval(seconds=5)
    async def update_status(self) -> None:
        for cam in await self._repo.find():
            ...
```

**Nhịp đếm từ lúc lượt trước chạy XONG.** Việc mất 2 giây với `seconds=5` thì
thực tế là 7 giây một lần. Đổi lại hai lượt không bao giờ chồng lên nhau. Cần
đúng nhịp tuyệt đối thì dùng `@cron`.

**Có gọi mạng thì đặt `max_seconds`.** Không đặt thì một lượt treo làm việc này
im vĩnh viễn mà không có gì báo:

```python
@interval(seconds=60, max_seconds=10)
async def ping_api(self) -> None:
    await self._http.get("https://...")
```

Lượt treo bị huỷ, ghi log `scheduler.run_timeout`, lượt sau vẫn chạy.

**Nhiều máy cùng gọi một API ngoài** thì thêm `jitter=5` — mỗi lần chờ cộng
thêm 0–5 giây ngẫu nhiên, để cả đàn không đập vào cùng một giây.

### Cái bẫy lớn nhất: app của bạn có 4 tiến trình

`fam run` mặc định bật **4 worker**, tức 4 tiến trình Python độc lập, mỗi tiến
trình nạp đủ code của bạn. Nếu tự viết `while True: await sleep(5)` thì nó chạy
**bốn lần mỗi 5 giây**: log thành 4 bản, API ngoài tốn 4× quota, 4 tiến trình
ghi đè trạng thái của nhau.

`@interval` mặc định đã lo: **một tiến trình giành quyền và giữ**, ba tiến
trình kia đứng chờ. Tiến trình đang chạy chết thì một trong ba lên thay trong
khoảng 5 giây.

Đo với 4 tiến trình chạy `@interval(seconds=0.25)` trong 1,1 giây:

| | Số lượt chạy | Số tiến trình chạy |
|---|---|---|
| mặc định (`single=True`) | **5** | **1** |
| `single=False` | 20 | 4 |

Trong log, tiến trình đang làm in `scheduler.owner`, ba tiến trình kia in
`scheduler.standby`. **Thấy `standby` là đúng, không phải lỗi.**

Chạy đúng một tiến trình (`fam dev`, hoặc 1 replica trên k8s) thì đặt
`APP_SCHEDULER__SINGLE=false` cho gọn — không có gì để tranh.

---

## 2. Chạy đúng giờ

```python
@cron("0 3 * * *", timezone="Asia/Ho_Chi_Minh")
async def clean_old_logs(self) -> None:
    ...
```

> ### Đọc kỹ dòng này
>
> **Mặc định là UTC.** Viết `@cron("0 3 * * *")` không kèm `timezone` thì nó
> chạy lúc **10 giờ sáng giờ Việt Nam**, không phải 3 giờ sáng.
>
> Nghĩ theo giờ Việt Nam thì luôn truyền `timezone="Asia/Ho_Chi_Minh"`.

Để sai lệch lộ ra ngay chứ không phải sau vài ngày, log khởi động in lần chạy
kế tiếp ở **cả hai** múi giờ — nhìn dòng `next_run_local` là biết đúng hay sai:

```
scheduler.job  job=...  schedule='0 3 * * * (UTC)'
               next_run='2026-08-26T03:00:00+00:00'
               next_run_local='2026-08-26T10:00:00+07:00'
```

### Viết biểu thức

```
┌─ phút (0-59)
│ ┌─ giờ (0-23)
│ │ ┌─ ngày trong tháng (1-31)
│ │ │ ┌─ tháng (1-12)
│ │ │ │ ┌─ thứ (0-6, 0 = Chủ nhật)
* * * * *
```

Mỗi trường nhận `*`, một số, `a-b`, `a,b,c`, `*/n`, `a-b/n`. Có sẵn lối tắt
`@hourly` `@daily` `@weekly` `@monthly` `@yearly`.

Vài cái hay dùng:

| Viết | Chạy khi nào |
|---|---|
| `*/15 * * * *` | mỗi 15 phút |
| `0 * * * *` | đầu mỗi giờ |
| `0 3 * * *` | 3 giờ sáng hàng ngày |
| `0 0 * * 1` | 0 giờ mỗi thứ Hai |
| `0 0 1 * *` | 0 giờ ngày 1 hàng tháng |

Viết sai bị báo lỗi **ngay lúc khởi động**, không đợi tới lúc chạy — kể cả loại
đúng cú pháp nhưng không bao giờ xảy ra như `0 0 30 2 *` (30 tháng Hai).

**Chỗ phản trực giác:** khi khai **cả** ngày trong tháng **và** thứ, cron gốc
nối chúng bằng HOẶC, trừ khi một trong hai bắt đầu bằng `*`:

| Biểu thức | Nghĩa thật |
|---|---|
| `0 0 1 * 1` | ngày 1 hàng tháng **HOẶC** mọi thứ Hai |
| `0 0 */7 * 1` | ngày 1,8,15,22,29 **VÀ** phải đúng thứ Hai |

Tránh phiền: đừng khai cả hai cùng lúc trừ khi bạn thật sự muốn.

Mọi lưu ý ở mục 1 (`max_seconds`, chuyện 4 tiến trình) áp dụng y nguyên.

---

## 3. Chạy đúng một lần

```python
@timeout(seconds=10)
async def warm_cache(self) -> None:
    ...
```

Chạy đúng một lần, 10 giây sau khi app khởi động. Dùng để hâm nóng: nạp cache,
dựng sẵn kết nối, kiểm tra một lần.

**Việc phải xong TRƯỚC khi app nhận request thì đừng dùng cái này** — nó chạy
sau lúc app đã mở cổng. Việc đó đặt thẳng vào lifespan của dự án, xem
[architecture.md](architecture.md).

---

## 4. Đưa việc vào hàng đợi

Dùng khi người dùng gửi yêu cầu, bạn muốn **trả lời ngay** và xử lý sau.

### Làm thế nào

Hai phần. Phần khai việc:

```python
from fastapi_modular import injectable, job


@injectable
class ImageService:
    @job("detect")
    async def detect(self, payload: dict) -> None:
        await run_yolo(payload["path"])
```

Phần gửi việc vào — tiêm `JobQueue`:

```python
from fastapi_modular import injectable, JobQueue


@injectable
class UploadService:
    def __init__(self, jobs: JobQueue) -> None:
        self._jobs = jobs

    async def upload(self, path: str) -> dict:
        await self._jobs.submit("detect", {"path": path})   # trả về NGAY
        return {"trang_thai": "đang xử lý"}
```

Chuỗi `"detect"` ở hai chỗ phải khớp nhau. Gõ sai thì việc bị bỏ và log ghi
`jobs.unknown` kèm danh sách tên đang có.

Log khởi động: `jobs.started jobs=['detect'] workers=1 max_queued=1000`.

### Lưu ý

> ### Việc nằm trong RAM
>
> **App tắt hay chết là mất sạch việc còn trong hàng đợi.** Không có cách nào
> lách — tiến trình biến mất thì bộ nhớ của nó biến mất theo.

Nên chỉ giao vào đây những việc **mất cũng chịu được**:

| Hợp với `@job` | KHÔNG hợp |
|---|---|
| ghi log, cập nhật thống kê | trừ tiền, tạo đơn |
| gửi thông báo đẩy | gửi mail xác nhận đơn |
| sinh ảnh thu nhỏ | bất cứ việc gì mất đi thì khách phải gọi tổng đài |
| nhận dạng cho ảnh **đã nằm trên đĩa** | |

Cột phải cần hàng đợi bền — xem [rabbitmq.md](rabbitmq.md).

Lúc tắt, khung chạy nốt hàng đợi trong 15 giây; phần không kịp thì ghi log kèm
con số chứ không im lặng: `jobs.dropped_on_shutdown count=17`.

**Mặc định chạy đúng một việc tại một thời điểm, theo thứ tự gửi vào.** Đây
thường là thứ người ta muốn khi nói "xử lý tuần tự". Đặt `APP_JOBS__WORKERS=4`
thì bốn việc chạy song song và **thứ tự không còn bảo đảm**.

**Hàng đợi đầy thì `submit()` ném lỗi 503 chứ không chờ.** Đầy nghĩa là bên
chạy chậm hơn bên gửi, và giấu điều đó bằng cách chờ chỉ làm request treo theo.
Nếu bên gọi chấp nhận bị chậm lại (một vòng nạp dữ liệu chạy nền, không phải
HTTP handler) thì `submit(..., wait=True)`.

**Việc nặng như YOLO thì thêm `thread=True`**, và đổi `async def` thành `def`
thường:

```python
@job("detect", thread=True)
def detect(self, payload: dict, ctx: WorkerContext) -> None:
    events = model.predict(payload["path"])   # hàm chặn, gọi thẳng
    ctx.run(self._repo.save(events))          # ghi database qua ctx.run
```

Vì sao cần: không có `thread=True` thì suy luận YOLO **làm đứng cả tiến
trình** — mọi request HTTP và mọi frame WebSocket đứng im chờ nó xong. Xem
[Ba câu hỏi ai cũng hỏi](#ba-câu-hỏi-ai-cũng-hỏi).

**Tải thật thì tách hẳn ra tiến trình riêng.** Dù có `thread=True`, chạy nhận
dạng cùng tiến trình với API vẫn tranh CPU với việc phục vụ request:

```
API  ──publish──▶  RabbitMQ  ──▶  tiến trình worker RIÊNG
                                   nạp model MỘT lần lúc boot
```

---

## 5. Vòng lặp chạy mãi

Dùng khi có **phần dựng ở TRƯỚC vòng lặp**: mở camera, mở socket, nạp model —
thứ làm một lần rồi dùng lại suốt. `@interval` không giữ được gì giữa hai lượt
nên nó sẽ mở lại camera mỗi 5 giây.

### Làm thế nào

```python
from fastapi_modular import injectable, worker, WorkerContext


@injectable
class CameraService:
    def __init__(self, repo: Repository[Event]) -> None:
        self._repo = repo

    @worker("camera")
    async def watch(self, data: dict, ctx: WorkerContext) -> None:
        capture = await ctx.blocking(cv2.VideoCapture, data["ip"])   # DỰNG
        try:
            while ctx.running:                                       # VÒNG LẶP
                frame = await ctx.blocking(capture.read)
                events = await ctx.blocking(model.predict, frame)
                await self._repo.save(events)
        finally:
            await ctx.blocking(capture.release)                      # DỌN
```

Khác ba loại trên ở một điểm: **nó không tự chạy**. Bạn phải gọi hàm để sinh ra
một bản:

```python
for camera in await self._repo.find():
    await service.watch(camera.id, {"ip": camera.ip, "fps": camera.fps})
    #                   └ KHOÁ      └ DATA (dict, bạn muốn để gì cũng được)
```

Gọi được từ bất cứ đâu: lifespan lúc boot, một endpoint "thêm camera", hay một
`@interval` quét bảng camera mỗi phút và bật những cái mới.

**Khoá là danh tính của bản chạy.** Dùng id trong database hay dùng IP đều
được, miễn là duy nhất. Gọi lại **cùng một khoá không sinh bản thứ hai**, nó
trả về bản đang chạy — với camera thì đó là điều bắt buộc, vì mở hai kết nối
RTSP tới cùng một thiết bị là cách nhanh nhất để cả hai cùng giật.

### Dừng nó, và dọn dẹp

Gọi thẳng trên chính method đó:

```python
await self.watch.stop(camera_id)     # dừng MỘT bản, chờ nó dọn xong
await self.watch.stop_all()          # dừng mọi bản, trả về số bản đã dừng
self.watch.running()                 # đang có những bản nào
self.watch.is_running(camera_id)     # True/False
```

`stop()` **chờ tới lúc `finally:` chạy xong**, nên viết tiếp ngay dưới nó là an
toàn — khi dòng sau chạy thì camera đã đóng:

```python
async def remove(self, camera_id: str) -> None:
    await self.watch.stop(camera_id)          # về khi camera đã đóng
    await self._repo.delete(camera_id)        # nên không có chuyện worker
    await self._notify(f"đã gỡ {camera_id}")  # ghi thêm cho thiết bị vừa xoá
```

Dọn cái gì thì viết ở đâu:

| Dọn cái gì | Viết ở đâu | Vì sao |
|---|---|---|
| camera, file, kết nối | `finally:` trong thân worker | chạy **mọi lần** vòng lặp kết thúc, kể cả khi rớt mạng rồi dựng lại |
| xoá bản ghi, báo nơi khác | sau `await ...stop()` | chỉ đúng khi người dùng thật sự gỡ thiết bị |

### Lưu ý

> ### Viết `while ctx.running:`, đừng viết `while True:`
>
> Đây là lỗi tốn thời gian nhất của cả trang này. `while True:` không bao giờ
> nghe lệnh dừng, nên lúc tắt app khung phải chờ hết 20 giây rồi mới bỏ mặc —
> và trong 20 giây đó **Ctrl+C trông như bị treo**.

Cần ngủ trong vòng lặp thì dùng `ctx.wait(giây)` thay `time.sleep(giây)`:
`time.sleep(30)` giữ lúc tắt app thêm 30 giây, `ctx.wait(30)` thoát ngay.

Đo trên app thật, cùng một worker:

| Vòng lặp viết kiểu | Ctrl+C tới lúc tiến trình thoát |
|---|---|
| `while True:` + `time.sleep(1)` | 21 giây |
| `while ctx.running:` + `ctx.wait(1)` | dưới 1 giây, `finally` chạy đủ |

Khung kêu ngay lúc khởi động nếu thấy bạn viết `while True:` —
`worker.endless_loop`.

**Hỏng thì tự dựng lại.** Vòng lặp ném lỗi thì khung ghi log rồi dựng lại sau
1s, 2s, 4s… tới 30s. Camera rớt mạng là chuyện thường ngày, và một vòng lặp
chết im lặng thì không ai biết cho tới lúc có người hỏi "sao camera 12 không
lên sự kiện nữa". Không muốn vậy thì `restart=False`.

**Vòng lặp tự `return` thì KHÔNG dựng lại** — đó là bạn chủ động, khung không
cãi.

**Chạy nhiều tiến trình mà chỉ muốn một cái nối tới mỗi thiết bị** thì
`@worker("camera", single=True)`, cùng cơ chế khoá với việc theo lịch ở mục 1.

---

## 6. Báo cho nhiều nơi

Dùng khi một chuyện xảy ra và **nhiều nơi cần biết**, mỗi nơi tự lo phần của
mình, còn nơi phát **không cần biết ai đang nghe**.

### Làm thế nào

Nơi phát tiêm `EventBus`:

```python
from fastapi_modular import EventBus, injectable


@injectable
class OrderService:
    def __init__(self, events: EventBus) -> None:
        self._events = events

    async def pay(self, order_id: str) -> None:
        await self._repo.mark_paid(order_id)
        await self._events.emit("order.paid", {"id": order_id})
```

Các nơi nghe — ở file khác, module khác, bao nhiêu cũng được:

```python
from fastapi_modular import injectable, on_event


@injectable
class MailService:
    @on_event("order.paid")
    async def send_receipt(self, data: dict) -> None:
        ...


@injectable
class StatsService:
    @on_event("order.paid")            # nơi nghe thứ hai — bình thường
    async def count(self, data: dict) -> None:
        ...

    @on_event("order.*")               # nghe mọi sự kiện của đơn hàng
    async def audit(self, data: dict) -> None:
        ...
```

Ba handler đó chạy **cùng lúc**. `OrderService` không biết chúng tồn tại — thêm
một nơi nghe là thêm một method, không phải sửa chỗ phát.

Log khởi động: `events.started listeners=3 events=['order.*', 'order.paid']`.

### Chọn `emit` hay `dispatch`

```python
await bus.emit("order.paid", data)   # CHỜ mọi nơi nghe xong
bus.dispatch("order.paid", data)     # trả về NGAY, chúng chạy nền
```

`emit` khi bạn cần mọi thứ xong trước khi đi tiếp — ví dụ trước khi trả lời
HTTP. `dispatch` khi không cần, và **đây mới là cái hay dùng**: một request
không nên chậm đi chỉ vì có thêm người đăng ký nghe.

`emit` trả về **số handler chạy trót lọt**; so với `len(bus.listeners("..."))`
là biết có ai hỏng không.

### Ký tự đại diện

Tên sự kiện ngăn bằng dấu chấm:

| Mẫu | Khớp | Không khớp |
|---|---|---|
| `order.paid` | `order.paid` | `order.shipped` |
| `order.*` | `order.paid` | `order.item.added` — `*` là ĐÚNG một tầng |
| `order.#` | `order.paid`, `order.item.added` | |
| `camera.*.motion` | `camera.12.motion` | `camera.motion` |

Đại diện **chỉ dùng khi nghe**. `emit("order.*")` bị chặn — phát một mẫu thì
không ai biết là ý gì.

### Lưu ý

**Một nơi nghe hỏng không kéo theo những nơi khác.** Gửi mail hỏng mà mất luôn
cập nhật thống kê là vô lý. Lỗi được ghi vào log `events.handler_failed`.

**Không có thử lại.** Cần thử lại, cần chạy nốt sau khi app khởi động lại — thì
chính handler đó nên đẩy việc sang `@job` hoặc RabbitMQ:

```python
@on_event("order.paid")
async def gui_mail(self, data: dict) -> None:
    await self._jobs.submit("send-mail", data)     # để @job lo phần thử lại
```

**Sợ một nơi nghe treo làm treo cả `emit`** thì đặt hạn:

```python
@on_event("order.paid", max_seconds=2.0)
async def goi_api_ngoai(self, data: dict) -> None: ...
```

> ### Chỉ trong MỘT tiến trình
>
> `fam run --workers 4` là bốn tiến trình. Sự kiện phát ở tiến trình 1 **không
> tới** tiến trình 2 — mỗi tiến trình có `EventBus` riêng.

Đây không phải thiếu sót mà là định nghĩa: nó gọi thẳng hàm trong bộ nhớ nên
rất nhanh và không cần cài gì. Muốn xuyên tiến trình thì đó là việc của broker:

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

### Nghe lúc đang chạy, không qua decorator

```python
bo_nghe = bus.subscribe("camera.*.motion", ham_cua_toi)
...
bo_nghe()
```

`@on_event` đủ cho gần hết mọi trường hợp. Cái này dành cho nơi nghe chỉ sống
một lúc: một phiên WebSocket, một lần chờ trong test.

---

## Ba câu hỏi ai cũng hỏi

### `ctx` là gì, khi nào phải khai?

`ctx` **không phải chuyện của thread**. Nó là ba công cụ, và bạn chỉ khai khi
cần một trong ba:

| Cần gì | Dùng | Có ở |
|---|---|---|
| thoát vòng lặp cho sạch | `ctx.running`, `ctx.wait(giây)` | mọi kiểu |
| gọi hàm CHẶN mà không làm đứng tiến trình | `await ctx.blocking(fn, …)` | `async def` |
| gọi hàm `async` từ trong thread | `ctx.run(coro)` | `thread=True` |

Suy ra:

| | Có cần `ctx` không |
|---|---|
| `@worker` | **gần như luôn** — cần `ctx.running` để dừng |
| `@interval` / `@cron` / `@timeout` / `@job` / `@on_event` (`async def`) | **không**, trừ khi có hàm chặn cần bọc |
| bất kỳ cái nào với `thread=True` | **có**, nếu cần gọi `async` |

Không khai thì khung không truyền — cứ viết `async def lam(self) -> None` như
thường:

```python
@interval(seconds=60)
async def ping(self) -> None:                # không cần ctx
    await self._http.get("/health")

@interval(seconds=60)
async def scan(self, ctx: WorkerContext) -> None:
    await ctx.blocking(os.scandir, "/data")  # có hàm chặn -> cần ctx
```

### Khi nào dùng `thread=True`?

**Khi thân hàm toàn lời gọi chặn.** Có ở cả năm decorator, và luôn đi kèm hai
thay đổi: hàm khai bằng `def` thường (không phải `async def`), và mọi lời gọi
`async` phải đi qua `ctx.run(...)`.

| | mặc định (`async def`) | `thread=True` |
|---|---|---|
| Hàm khai bằng | `async def` | `def` thường |
| Ghi database | `await self._repo.save(...)` | `ctx.run(self._repo.save(...))` |
| Gọi hàm chặn | `await ctx.blocking(fn, ...)` | gọi thẳng |
| Dừng được giữa chừng | có | **không** — phải đợi lời gọi chặn trả về |

**Mặc định đúng cho gần hết mọi trường hợp**, kể cả camera + AI: bọc từng lời
gọi chặn trong `ctx.blocking(...)` là nó chạy ở thread khác, còn `await` vào
database thì thẳng tuột.

`thread=True` chỉ đáng dùng khi bọc từng lời gọi trở nên rườm rà:

```python
@worker("camera", thread=True)
def watch(self, data: dict, ctx: WorkerContext) -> None:
    capture = cv2.VideoCapture(data["ip"])          # đang ở thread, gọi thẳng
    try:
        while ctx.running:
            frame = capture.read()
            events = model.predict(frame)
            ctx.run(self._repo.save(events))        # cầu nối sang event loop
    finally:
        capture.release()
```

Chú ý: `thread=True` chỉ **thật sự** nhanh hơn với thư viện nhả GIL (torch,
opencv, numpy — phần tính toán của chúng viết bằng C). Với vòng lặp Python
thuần thì không: nó vẫn giữ GIL và vẫn làm nghẽn cả tiến trình.

### Trong thread thì ghi database kiểu gì?

`ctx.run(coro)`:

```python
ctx.run(self._repo.save(event))
```

Đừng gọi `asyncio.run()` — nó tạo một event loop MỚI, mà connection pool của
database thuộc về loop cũ, và nó sẽ hỏng theo những cách rất khó hiểu.

**Mỗi lời gọi tự commit.** Worker không nằm trong request nào, nên mỗi
`save`/`delete` là một transaction riêng, xong là xong. Muốn nhiều lệnh ghi
"cùng thành công hoặc cùng không" thì bọc lại — `db.transaction()` dùng được
trong `ctx.run` như thường:

```python
async def chuyen_kho(self, tu: str, den: str) -> None:
    async with self._db.transaction():
        await self._repo.save(...)
        await self._repo.delete(...)

# trong worker:
ctx.run(self._service.chuyen_kho("A", "B"))
```

**Đừng resolve provider request-scoped trong worker.** Worker sống lâu hơn thứ
sinh ra nó, nên khung cắt nó khỏi request scope — `container.resolve(Principal)`
hay bất cứ provider `Scope.REQUEST` nào cũng sẽ báo lỗi ngay. Đó là cố ý: không
có request thì không có "danh tính của request này".

---

## Hỏng thì tra ở đây

| Bạn thấy gì | Gần như luôn là |
|---|---|
| **Viết decorator rồi mà không thấy chạy** | class thiếu `@injectable`, hoặc file chưa được import. Kiểm bằng log khởi động: `scheduler.job` / `jobs.started` / `events.started` phải nhắc tên bạn |
| **Đợi mãi mới thấy chạy lần đầu** | đúng thiết kế: `@interval` đợi hết một chu kỳ rồi mới chạy. Muốn chạy ngay lúc app lên thì [`run_on_startup=True`](#chạy-ngay-lúc-app-lên-thay-vì-đợi-hết-chu-kỳ) |
| **Việc chạy 4 lần mỗi nhịp** | `single=False`, hoặc mỗi máy một `flock` riêng (nhiều máy thì cần Redis) |
| **Việc không chạy lần nào, log ghi `standby`** | đúng rồi — tiến trình khác đang giữ quyền. Chạy một tiến trình thì đặt `APP_SCHEDULER__SINGLE=false` |
| **`@cron` chạy lệch 7 tiếng** | quên `timezone=` — mặc định là UTC |
| **`scheduler.lock_lost`** | Redis chớp, hoặc lượt chạy lâu hơn hạn khoá — việc có thể đang chạy hai nơi |
| **`jobs.unknown`** | tên trong `submit()` không khớp tên trong `@job` |
| **`jobs_queued` phình dần** | bên chạy chậm hơn bên gửi; tăng `APP_JOBS__WORKERS` hoặc chuyển sang RabbitMQ |
| **`jobs.dropped_on_shutdown`** | đúng như tên: bấy nhiêu việc đã mất khi tắt app |
| **Phát sự kiện mà không thấy gì chạy** | in `bus.listeners("ten.su.kien")` ra xem ai đang nghe; thường là sai mẫu hoặc thiếu `@injectable` |
| **`events.not_started`** | phát trước khi app khởi động xong — chuyển lời gọi đó vào sau `startup` |
| **`events.dispatch_dropped`** | bên nghe chậm hơn bên phát; chỗ đó cần `@job`, không phải một trần cao hơn |
| **API đứng hình khi worker chạy** | quên bọc `ctx.blocking(...)` quanh hàm chặn |
| **`ctx.blocking` chậm dần khi thêm worker** | vượt trần pool — nới `APP_WORKERS__THREAD_POOL_SIZE` |
| **`workers_restarted_total` tăng đều** | camera rớt mạng, hoặc vòng lặp ném lỗi mỗi lượt — xem log `worker.crashed` |
| **`worker.stop_timeout` lúc tắt app** | vòng lặp không kiểm `ctx.running`, hoặc lời gọi chặn không có timeout |
| **Ghi/xoá trong worker: `delete()` trả `True` mà dữ liệu vẫn còn** | Bản **trước 0.3.1**: worker thừa hưởng transaction của request sinh ra nó và không ai commit. Nâng cấp: `pip install -U fastapi-modular`. Nhận ra nó bằng cách đọc database bằng **công cụ khác** (`sqlite3 app.db "SELECT …"`), chứ hỏi lại repository thì vẫn thấy "xong rồi" |
| **Ctrl+C bấm mà không có gì xảy ra** | xem ngay dưới |

### Ctrl+C như không ăn

Gần như luôn là **một `while True:` không có đường thoát**. Khung xin dừng,
vòng lặp không nghe, khung chờ hết `APP_WORKERS__STOP_SECONDS` (mặc định 20
giây) rồi mới bỏ mặc — và trong hai chục giây đó vòng lặp vẫn in ra màn hình
như chưa có gì xảy ra.

Khung nói ra ở cả hai đầu:

```
[warning] worker.endless_loop   name=camera function=CameraService.watch    ← lúc KHỞI ĐỘNG
[info]    worker.stopping       count=1 workers=['camera:test'] timeout=20.0
[info]    worker.stopping_still remaining=['worker-camera-test'] seconds_left=15.0
[warning] worker.stop_timeout   stuck=['camera:test']                       ← lúc TẮT
```

Dòng `worker.endless_loop` in ngay lúc khởi động, trước khi bạn kịp gặp vấn đề.
Nó chỉ soi được `while True:`; vòng lặp thoát bằng `break` theo điều kiện riêng
thì khung không đọc được ý định nên không kêu, và cũng không chặn.

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

Nếu vòng lặp đã kiểm `ctx.running` mà vẫn kẹt, thì nó đang nằm trong một **lời
gọi chặn không chịu trả về** — `capture.read()` trên luồng đã chết,
`requests.get()` không đặt timeout. Python không giết được thread đang kẹt;
đường duy nhất là đặt timeout cho chính lời gọi đó. Nhưng nó **không** giữ tiến
trình lại nữa: mọi thread khung mở đều là daemon, nên quá hạn là app vẫn thoát.

---

## Tra cứu

### Tham số của từng decorator

```python
@interval(
    seconds,                # chu kỳ
    *,
    name="",                # tên hiển thị và tên KHOÁ; mặc định Class.method
    single=True,            # chỉ một tiến trình chạy
    run_on_startup=False,   # True = chạy ngay, không đợi hết một chu kỳ
    jitter=0.0,             # cộng ngẫu nhiên 0..jitter giây vào mỗi lần chờ
    max_seconds=None,       # trần thời gian MỘT lượt
    thread=False,
)

@cron("0 3 * * *", *, timezone="UTC", name="", single=True,
      max_seconds=None, thread=False)

@timeout(seconds, *, name="", single=True, max_seconds=None, thread=False)

@job(
    name,                   # tên loại việc, dùng lúc submit; duy nhất trong app
    *,
    max_retries=0,          # thử lại NGAY TẠI CHỖ khi handler ném lỗi
    retry_delay=1.0,
    thread=False,
)

@worker(
    name="",                # mặc định là Class.method
    *,
    thread=False,
    restart=True,           # hỏng thì dựng lại
    restart_delay=1.0,
    max_restart_delay=30.0,
    single=False,           # chỉ MỘT tiến trình chạy bản này
)

@on_event(
    pattern,                # "order.paid", "order.*", "camera.#"
    *,
    thread=False,
    max_seconds=0.0,        # 0 = không giới hạn
)
```

Handler nhận được gì:

| | Chữ ký |
|---|---|
| `@interval` / `@cron` / `@timeout` | `(self)` hoặc `(self, ctx)` — **không nhận dữ liệu** |
| `@job` | `(self, payload)` hoặc `(self, payload, ctx)` |
| `@worker` | `(self)`, `(self, data)`, `(self, ctx)`, `(self, data, ctx)` |
| `@on_event` | `(self)`, `(self, data)`, `(self, data, ctx)` |

Với `@job` và `@on_event`, tham số dữ liệu chú kiểu bằng model Pydantic thì
payload được kiểm khuôn trước khi vào handler; sai khuôn thì bỏ lượt đó và ghi
log, không làm chết những lượt khác.

`@job` mặc định `max_retries=0` — hỏng là ghi log rồi bỏ. Nhớ rằng thử lại
**làm đứng cả hàng đợi** khi `workers=1`.

### Xem worker nào đang chạy

`WorkerPool` giữ sổ mọi bản đang sống — tiêm vào rồi hỏi, hoặc gọi trong một
endpoint chẩn đoán:

```python
from fastapi_modular.core.workers import WorkerPool


@injectable
class DebugService:
    def __init__(self, pool: WorkerPool) -> None:
        self._pool = pool

    def dang_chay(self) -> list[dict]:
        return self._pool.running()
        # [{"worker": "camera", "key": "cam-01", "uptime_seconds": 903.2, "running": True}]
```

Mỗi loại worker cũng tự có `.running()` / `.stop(key)` / `.stop_all()` gắn ngay
trên method đã khai — xem [mục 5](#5-vòng-lặp-chạy-mãi).

### Biến cấu hình

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
| `APP_EVENTS__ENABLED` | `true` | tắt hẳn `@on_event` |
| `APP_EVENTS__MAX_SECONDS` | `0.0` | hạn mặc định cho một lượt handler; 0 = không giới hạn |
| `APP_EVENTS__MAX_PENDING` | `1000` | trần số lượt `dispatch` chạy nền cùng lúc |
| `APP_EVENTS__DRAIN_SECONDS` | `5.0` | chờ handler nền chạy nốt khi tắt app |

### Số đo

| Số đo | Nhãn |
|---|---|
| `scheduled_runs_total` / `scheduled_failures_total` / `scheduled_skipped_total` | `job` |
| `scheduled_duration_seconds` | `job` |
| `jobs_submitted_total` / `jobs_done_total` / `jobs_failed_total` / `jobs_rejected_total` | `job` |
| `jobs_queued` | *(gauge — con số đáng theo dõi nhất)* |
| `workers_started_total` / `workers_restarted_total` | `worker` |
| `workers_running` | *(gauge)* |
| `events_emitted_total` / `event_handlers_done_total` / `event_handlers_failed_total` | `event` |
| `event_handler_duration_seconds` | `event` |
| `event_handlers_pending` | *(gauge)* |

### Số đo hiệu năng

Chỉ đọc mục này nếu bạn đang cân nhắc một quyết định cụ thể. Đo trên máy dev.

**`ctx.run` tốn bao nhiêu** — cùng một lời gọi, một lần `await` thẳng và một
lần qua `ctx.run` từ trong thread:

| Lời gọi | `await` thẳng | qua `ctx.run` | Chênh |
|---|---|---|---|
| coroutine rỗng *(chi phí trần)* | 20.000.000/s | 50.700/s | +0,020 ms |
| WebSocket broadcast | 1.351.000/s | 47.600/s | +0,020 ms |
| MQTT emit qos=1 | 7.494 tin/s | 5.959 tin/s | ~0 |
| Kafka emit acks=all | 1.745 tin/s | 4.299 tin/s | ~0 |
| SQLite INSERT | 1.269 ghi/s | 811 ghi/s | +0,4 ms |

**`ctx.run` tốn cố định khoảng 0,02 ms**, trần khoảng 50.000 lời gọi/giây. Mọi
thứ đụng tới ổ đĩa hay mạng đều chậm hơn thế hàng chục lần nên nó không đáng
lo. Chỗ duy nhất nó lộ ra là khi việc bên dưới nhanh cỡ 0,1–0,5 ms — và cách
sửa là **gộp** nhiều lời gọi thành một, không phải bỏ `ctx.run`:

```python
lo = []
while ctx.running:
    lo.append(model.predict(capture.read()))
    if len(lo) >= 50:
        ctx.run(self._repo.save_many(lo))     # một lượt cho 50 sự kiện
        lo.clear()
```

**`@on_event` nhanh cỡ nào** — handler rỗng:

| | Lượt phát/giây | Mỗi lượt phát |
|---|---|---|
| `emit`, 1 nơi nghe | 220.000 | 4,6 µs |
| `emit`, 3 nơi nghe | 38.000 | 26 µs |
| `emit`, 10 nơi nghe | 16.000 | 62 µs |
| `dispatch`, 3 nơi nghe | 69.000 | 14 µs *(chưa tính lúc chạy nền)* |

Số lượt phát giảm khi thêm nơi nghe là đương nhiên — mỗi nơi nghe là một
coroutine phải dựng và chờ. So với RabbitMQ `emit` 126 tin/s thì khác nhau ba
bậc, nhưng RabbitMQ đi được sang máy khác còn cái này thì không.

**`@worker(thread=True)` không mượn pool chung** — mỗi bản một thread daemon
riêng, nên nhiều worker không làm cạn pool của `ctx.blocking`:

| Số worker `thread=True` | Thread tiến trình | `ctx.blocking(0,01s)` mất |
|---|---|---|
| 4 | 6 | 0,013s |
| 20 | 22 | 0,015s |
| 40 | 42 | 0,015s |

**SQLite + worker ghi liên tục:** mặc định của khung là WAL + synchronous
NORMAL (1.376 ghi/s), không phải mặc định gốc của SQLite (68 ghi/s). Tổng thông
lượng ghi **không tăng theo số worker** — SQLite chỉ có một người ghi tại một
thời điểm. App bị `kill -9` không làm hỏng file và không mất dòng nào đã ghi.
Chi tiết ở [database.md](database.md).
