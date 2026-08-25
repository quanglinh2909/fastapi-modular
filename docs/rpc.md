# Gửi rồi chờ trả lời (`emit` / `send`)

Thư viện vốn chỉ có một chiều: bắn tin đi rồi quên. Đây là chiều còn lại — gửi
tin, **chờ bên kia trả lời**, rồi dùng kết quả.

Khuôn tin lấy đúng của **NestJS**, nên một service viết bằng khung này nói
chuyện được với một microservice NestJS mà không cần lớp dịch nào ở giữa. Đã
chạy thật hai chiều với `@nestjs/microservices` 11.2.1 — xem [mục cuối](#đối-chứng-với-nestjs-thật).

---

## Đối chiếu với NestJS

| NestJS | Ở đây |
|---|---|
| `client.emit(pattern, data)` | `await broker.emit(pattern, data, queue="…")` |
| `await firstValueFrom(client.send(pattern, data))` | `await broker.send(pattern, data, queue="…")` |
| `@EventPattern('x')` | `@rabbitmq_responder("x", queue="…")` — trả về gì cũng bị bỏ |
| `@MessagePattern('x')` | `@rabbitmq_responder("x", queue="…")` — **giá trị trả về được gửi ngược** |
| `@MessagePattern({ cmd: 'sum' })` | `@rabbitmq_responder({"cmd": "sum"}, queue="…")` |

Một decorator lo cả hai, vì bên nhận không tự chọn được: **tin có `id` thì phải
trả lời, không có `id` thì không**. Người GỬI quyết định điều đó bằng cách gọi
`send` hay `emit`. NestJS cũng phân loại đúng theo dấu hiệu này.

> `publish()` cũ **không đổi gì**. Nó gửi payload thô — khuôn riêng của thư
> viện này. `emit()` gửi khuôn NestJS. Xem [publish hay emit](#publish-hay-emit).

---

## Làm một lượt

**Bên trả lời** — giống hệt một service thường, chỉ khác là nó `return`:

```python
# src/api/math/math_service.py
from fastapi_modular.core.container import injectable
from fastapi_modular.infrastructure.rabbitmq import rabbitmq_responder


@injectable
class MathService:
    @rabbitmq_responder("sum", queue="math")
    async def cong(self, data: list[int]) -> int:
        return sum(data)
```

**Bên gọi**:

```python
@injectable
class BaoCaoService:
    def __init__(self, mq: RabbitBroker) -> None:
        self._mq = mq

    async def tong(self, so: list[int]) -> int:
        return await self._mq.send("sum", so, queue="math")   # -> 10
```

Không có cấu hình nào phải thêm. Hàng đợi `math` tự mọc lúc khởi động, và
`send` **không** tạo thêm hàng đợi nào để nhận câu trả lời (xem
[Câu trả lời về bằng đường nào](#câu-trả-lời-về-bằng-đường-nào)).

---

## Nghĩ kỹ trước khi dùng `send`

`send` biến hàng đợi thành lời gọi hàm qua mạng, và kéo theo đúng những thứ mà
hàng đợi vốn dựng lên để tránh:

| | `emit` / `publish` | `send` |
|---|---|---|
| Bên kia đang chết | tin **nằm chờ** trong hàng đợi tới khi nó sống lại | bên gọi **treo** rồi nhận lỗi hết giờ |
| Bên kia chậm | không ảnh hưởng bên gọi | bên gọi chậm theo, độ trễ **cộng dồn** qua từng chặng |
| Deploy bên kia | tin đọng lại, lên là xử lý tiếp | mọi lời gọi trong lúc đó đều hỏng |
| Bên gọi cần gì | không cần gì | cần kết quả **ngay bây giờ** |

Câu hỏi để tự chọn: **người dùng có đang ngồi đợi kết quả này không?**

- Đang đợi (tra cứu, kiểm tra tồn kho, tính giá) → `send`.
- Không đợi (gửi mail, ghi log, cập nhật thống kê) → `emit` hoặc `publish`.

Và một điều phải nhớ:

> **Hết giờ KHÔNG có nghĩa là bên kia chưa làm gì.** Rất có thể nó đã làm xong
> và chỉ câu trả lời bị lạc trên đường về. Nên việc bên kia làm phải **lặp lại
> được**, và đừng tự động gọi lại một việc trừ tiền hay tạo đơn.

---

## `send` — bên gọi

```python
await broker.send(
    pattern,             # str hoặc dict — "sum", {"cmd": "sum"}
    data=None,           # Any, json hoá được
    *,
    queue=None,          # kiểu NestJS: gửi thẳng vào hàng đợi này
    exchange="",         # kiểu AMQP: định tuyến như thường
    routing_key=None,
    exchange_type=None,
    headers=None,
    timeout=None,        # giây; None = 5.0
)
```

Hai cách khai địa chỉ, **loại trừ nhau**:

```python
await broker.send("sum", [1, 2], queue="math")              # kiểu NestJS
await broker.send("sum", [1, 2], exchange="rpc", routing_key="math.sum")   # kiểu AMQP
```

Khai `queue=` là gửi qua exchange mặc định vào đúng hàng đợi mang tên đó —
chính là cách `ClientRMQ` của NestJS gửi (`sendToQueue`). Muốn nói chuyện với
một microservice NestJS thì dùng cách này, với `queue` bằng đúng tên hàng đợi
khai trong `options.queue` của nó.

### Kết quả và lỗi

| Bên kia | Bên gọi nhận |
|---|---|
| `return <giá trị>` | đúng giá trị đó |
| `raise` bất kỳ lỗi gì | `RpcRemoteError` (502) kèm nguyên văn lỗi |
| không có responder nào khớp pattern | `RpcRemoteError` kèm câu của NestJS |
| im lặng quá `timeout` | `RpcTimeoutError` (504) |

Phân biệt hai lỗi cuối cho kỹ: `RpcRemoteError` nghĩa là **ta biết** việc đã
hỏng vì cái gì; `RpcTimeoutError` nghĩa là **ta không biết gì cả**. Chỉ cái sau
mới đáng đi tìm xem bên kia còn sống không.

---

## `@rabbitmq_responder` — bên trả lời

```python
@rabbitmq_responder(
    pattern,             # str hoặc dict
    *,
    queue,               # BẮT BUỘC — hàng đợi nhận yêu cầu
    exchange="",         # để trống = nhận thẳng theo tên hàng đợi
    routing_key=None,
    exchange_type=None,
    prefetch=20,
    durable=True,
    auto_delete=False,
)
```

Handler nhận `(self, data)` hoặc `(self, data, meta)`, và **trả về** kết quả:

```python
@rabbitmq_responder({"cmd": "tim-nguoi-dung"}, queue="users")
async def tim(self, data: dict, meta: dict) -> dict:
    # meta = {"pattern", "queue", "message_id", "correlation_id", "reply_to"}
    return {"id": data["id"], "ten": "An"}
```

**Nhiều responder dùng chung một hàng đợi** là chuyện bình thường và đúng mô
hình NestJS — một service nghe một hàng đợi rồi tự phân việc theo pattern:

```python
@rabbitmq_responder("sum", queue="math")
async def cong(self, data: list[int]) -> int: ...

@rabbitmq_responder("max", queue="math")       # cùng hàng đợi, khác pattern
async def lon_nhat(self, data: list[int]) -> int: ...
```

Khung chặn ngay lúc khởi động nếu hai responder cùng hàng đợi mà **trùng
pattern** (một trong hai sẽ không bao giờ được gọi), hoặc khai **thiết lập hàng
đợi khác nhau** (chỉ cái dựng trước có tác dụng).

### Kiểm khuôn payload

Chú kiểu tham số đầu bằng một model Pydantic thì payload được kiểm trước khi
vào handler, và sai khuôn thì người gọi nhận đúng lỗi đó — thay vì phải ngồi
đợi hết giờ rồi đoán:

```python
class DonHang(BaseModel):
    ma: str
    so_luong: int

@rabbitmq_responder("dat-hang", queue="orders")
async def dat(self, don: DonHang) -> dict:
    return {"ma": don.ma, "thanh_tien": don.so_luong * 1000}
```

### Không có thử lại, không có `.dlq`

Cố ý. Người gọi chỉ chờ vài giây rồi bỏ; thử lại sau khi họ đã bỏ cuộc là làm
một việc không ai đọc kết quả — tệ hơn nữa nếu việc đó ghi dữ liệu. Handler
hỏng thì **báo ngay** cho người gọi để họ tự quyết định.

Cần thử lại và giữ tin hỏng thì đó là việc của [`@rabbitmq_subscriber`](rabbitmq.md#retry-và-dlq-là-gì)
— tức là bạn đang cần một sự kiện, không phải một lời gọi.

---

## Câu trả lời về bằng đường nào

RabbitMQ có sẵn hàng đợi giả `amq.rabbitmq.reply-to` cho đúng việc này
("direct reply-to"). Khung dùng nó, và NestJS cũng vậy.

Nghĩa là: **không có hàng đợi trả lời nào được tạo ra**, dù gọi bao nhiêu lần.
Kiểm được:

```bash
docker exec rabbit rabbitmqctl -q list_queues name    # trước
# ...gọi send() 50 lần...
docker exec rabbit rabbitmqctl -q list_queues name    # sau: y hệt
```

Cái giá của nó: hàng đợi giả gắn liền với **một kênh AMQP**, nên yêu cầu phải
gửi trên đúng kênh đang nghe nó. Khung tự mở và giữ kênh riêng đó; bạn không
phải làm gì. Kênh chết theo kết nối, nên khi rớt mạng thì mọi lời gọi đang chờ
được **đánh thức ngay** thay vì phải đứng đủ `timeout` giây.

---

## `publish` hay `emit`

Cả hai đều là "bắn đi, không chờ". Khác nhau ở **thân tin**:

```python
await broker.publish("events", "alert.created", {"id": 1})
# thân tin: {"id": 1}

await broker.emit("alert.created", {"id": 1}, queue="nest-app")
# thân tin: {"pattern": "alert.created", "data": {"id": 1}}
```

| Dùng | Khi |
|---|---|
| `publish` | hai đầu đều là khung này; định tuyến bằng routing key; payload thô |
| `emit` | đầu kia là NestJS, hoặc bạn muốn cùng một khuôn với `send` |

`@rabbitmq_subscriber` nghe theo **routing key**; `@rabbitmq_responder` nghe
theo **pattern**. Chọn `emit` thì chọn luôn `@rabbitmq_responder` ở đầu kia.

---

## Chuỗi hoá pattern

NestJS cho phép pattern dạng object, và chuỗi hoá nó bằng một hàm riêng chứ
không phải `JSON.stringify`:

```python
{"cmd": "sum"}              ->  {"cmd":"sum"}
{"v": 1, "cmd": "sum"}      ->  {"cmd":"sum","v":1}      # khoá được SẮP XẾP
{"z": 1, "a": 2, "M": 3}    ->  {"a":2,"M":3,"z":1}      # sắp theo localeCompare
```

Dòng cuối là chỗ dễ sai nhất: NestJS sắp khoá bằng `localeCompare` (thứ tự từ
điển, không phân biệt hoa thường) chứ **không** theo mã ký tự — so mã ký tự sẽ
xếp `"M"` (77) trước `"a"` (97). Lệch một ký tự là bên kia không tìm thấy
handler, và **không có lỗi nào được ném ra** để mà lần: lời gọi chỉ treo tới
hết giờ.

Khung cài đúng luật đó. `tests/test_rpc.py` khoá lại bằng 132 vector do **chính
NestJS sinh ra**, không phải gõ tay.

---

## Đối chứng với NestJS thật

Bộ test của thư viện không kéo Node vào CI, nên phần đối chứng chạy tay. Dựng
lại như sau — mất khoảng hai phút:

```bash
docker run -d --name rabbit -p 5672:5672 rabbitmq:3.13-management-alpine
mkdir nest-check && cd nest-check
npm init -y && npm i @nestjs/core@11 @nestjs/common@11 @nestjs/microservices@11 \
    amqplib amqp-connection-manager rxjs reflect-metadata
npm i -D typescript @types/node
```

`server.ts` — **phải biên dịch bằng TypeScript**, gắn decorator bằng JavaScript
thuần sẽ không đăng ký được `@EventPattern`:

```ts
import 'reflect-metadata';
import { Module, Controller } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { MessagePattern, EventPattern, Transport } from '@nestjs/microservices';

@Controller()
class MathController {
  @MessagePattern('sum')          sum(data: number[]) { return data.reduce((a, b) => a + b, 0); }
  @MessagePattern({ cmd: 'info' }) info(data: any)    { return { echo: data, from: 'nestjs' }; }
  @EventPattern('noted')          noted(data: any)    { console.log('GOT', data); }
}

@Module({ controllers: [MathController] })
class AppModule {}

(async () => {
  const app = await NestFactory.createMicroservice(AppModule, {
    transport: Transport.RMQ,
    options: { urls: [process.env.MQ_URL!], queue: 'nest-math',
               queueOptions: { durable: false }, noAck: true },
  });
  await app.listen();
})();
```

Rồi gọi sang từ Python:

```python
await broker.send("sum", [1, 2, 3, 4], queue="nest-math")        # -> 10
await broker.send({"cmd": "info"}, {"ai": "python"}, queue="nest-math")
await broker.emit("noted", {"tu": "python"}, queue="nest-math")   # -> server in "GOT"
```

Chiều ngược lại, `ClientProxy` của NestJS gọi vào `@rabbitmq_responder` với
`options.queue` trỏ đúng tên hàng đợi của bạn.

Hai chỗ đã cắn khi làm việc này, ghi lại để khỏi mất thì giờ:

- **Cả hai bên đều tự khai hàng đợi**, nên tham số phải khớp. `queueOptions:
  { durable: false }` bên NestJS thì bên này phải `durable=False`, nếu không
  RabbitMQ trả `PRECONDITION_FAILED` và đóng kênh.
- **`client.send(pattern, null)` bị chính NestJS chặn** ở phía client
  (`InvalidMessageException`), tin chưa kịp gửi đi. Gửi `{}` thay vì `null`.

### Sinh lại bộ vector pattern

```bash
node -e '
const {transformPatternToRoute} = require("@nestjs/microservices/utils/transform-pattern.utils.js");
console.log(transformPatternToRoute({z:1, a:2, M:3}));'   # {"a":2,"M":3,"z":1}
```

---

## Số đo và chẩn đoán

| Log | Nghĩa |
|---|---|
| `mq.responder_started` | hàng đợi đã dựng, kèm danh sách pattern nghe được |
| `mq.no_responder` | có yêu cầu nhưng không pattern nào khớp — kèm danh sách pattern đang có |
| `mq.responder_failed` | handler ném lỗi; người gọi đã nhận được lỗi này |
| `mq.responder_result_dropped` | handler trả về giá trị nhưng tin là `emit` — không ai đọc |
| `mq.responder_bad_packet` | tin không theo khuôn `{pattern, data, id}` |
| `mq.reply_too_late` | trả lời về sau khi người gọi đã bỏ cuộc — `timeout` đang quá ngắn |
| `rpc.reply_failed` | làm xong việc nhưng gửi câu trả lời không được |

Triệu chứng hay gặp:

| Thấy gì | Gần như luôn là |
|---|---|
| `RpcTimeoutError` mà bên kia vẫn sống | pattern lệch nhau — soi `mq.no_responder` ở bên trả lời |
| `RpcTimeoutError` và bên kia không có log gì | sai `queue`, hoặc responder chưa khởi động được |
| Nhiều `mq.reply_too_late` | `timeout` ngắn hơn thời gian xử lý thật |
| Gọi được lần đầu rồi treo | broker rớt giữa chừng; xem `mq.connection_lost` |
