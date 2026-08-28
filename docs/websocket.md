# WebSocket

Lớp realtime của khung, viết theo đúng mô hình gateway của NestJS: một client mở
**một kết nối**, vào **phòng** để nhận tin theo nhóm, hoặc nhận tin **gửi thẳng**
cho riêng mình.

```
Trình duyệt ──ws://…/ws/chat?client_id=an──▶ ChatGateway
                                              ├─ guard   (bắt tay 1 lần)
                                              ├─ phòng   lobby, room:1…
                                              └─ @subscribe("message.send")
Service/Controller HTTP ─────▶ WebSocketServer ─▶ to_room / to_user / to_socket
```

---

## ⚠️ Đọc mục này trước, kẻo mất một buổi

Bốn việc **client bắt buộc phải làm**. Thiếu bất kỳ việc nào, mọi thứ vẫn chạy
ngon lành trên máy bạn rồi hỏng một cách khó hiểu về sau:

| Việc | Thiếu thì sao |
|---|---|
| **1. Trả lời `ping` bằng `pong`** | Kết nối tự đứt sau **70 giây**, mã 4408. Đây là câu hỏi được hỏi nhiều nhất. |
| **2. Nối lại có backoff** | Mất mạng một nhịp là chết hẳn, phải F5 mới sống lại. |
| **3. Vào lại phòng sau khi nối lại** | Nối lại thành công nhưng **im lặng mãi mãi** — lỗi khó lần ra nhất. |
| **4. Đọc mã đóng trước khi nối lại** | Token sai (4401) mà cứ nối lại → vòng lặp vô hạn đập vào server. |

Cả bốn đều nằm sẵn trong [đoạn code 30 dòng ở mục 2](#2-client-tối-thiểu-chạy-được-ngay).
Cứ chép về là xong; đọc phần giải thích khi nào cần.

---

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Cho tôi thấy nó chạy trước đã" | [1. Chạy thử 30 giây](#1-chạy-thử-30-giây) |
| Viết client (JS/trình duyệt) | [2. Client tối thiểu](#2-client-tối-thiểu-chạy-được-ngay) · [9. Next.js đầy đủ](#9-client-nextjs-đầy-đủ) |
| Viết server (gateway) | [4. Viết một gateway](#4-viết-một-gateway) |
| Gửi tin từ REST / tác vụ nền | [`WebSocketServer`](#websocketserver--đẩy-tin-từ-bất-cứ-đâu) |
| Cho client vào phòng | [5. Phòng](#5-phòng) |
| Thêm đăng nhập | [6. Xác thực](#6-xác-thực) |
| Chuẩn bị lên production | [8. Chạy nhiều worker](#8-chạy-nhiều-worker) |
| Thử tay, không viết code | [10. Postman](#10-thử-tay-bằng-postman) |
| **Đang gặp lỗi** | [13. Hỏng thì tra ở đây](#13-hỏng-thì-tra-ở-đây) |

---

## 1. Chạy thử 30 giây

```bash
fam dev
```

Module `chat` có sẵn trong template chạy được ngay. Mở kết nối tới:

```
ws://localhost:8000/ws/chat?client_id=an
```

Gửi lên:

```json
{"event": "room.join", "data": {"room": "room:1"}, "id": "1"}
{"event": "message.send", "data": {"room": "room:1", "text": "chào"}, "id": "2"}
```

Đẩy tin từ phía HTTP xuống chính phòng đó:

```bash
curl -X POST localhost:8000/api/chat/broadcast \
  -H 'content-type: application/json' \
  -d '{"room":"room:1","event":"alert.created","data":{"id":"A1"}}'
```

Xem trạng thái: `curl localhost:8000/api/chat/stats`

Chưa có công cụ nào để mở WebSocket? Dùng [Postman (mục 10)](#10-thử-tay-bằng-postman),
hoặc dán đoạn code ở mục 2 vào Console của trình duyệt.

---

## 2. Client tối thiểu, chạy được ngay

Dán thẳng vào Console trình duyệt (F12) là chạy. Ngắn nhưng đã làm **đủ cả bốn
việc bắt buộc** ở đầu trang:

```js
function connect(url, { onEvent = console.log } = {}) {
  let attempt = 0, stopped = false;
  const rooms = new Set();
  let ws;

  const open = () => {
    ws = new WebSocket(url);

    ws.onopen = () => {
      attempt = 0;
      // (3) Kết nối MỚI = phòng cũ không còn. Server không nhớ giùm.
      for (const r of rooms) ws.send(JSON.stringify({ event: "room.join", data: { room: r } }));
    };

    ws.onmessage = (e) => {
      const f = JSON.parse(e.data);
      // (1) Trả lời nhịp tim. Thiếu dòng này là đứt sau 70 giây.
      if (f.event === "ping") return ws.send(JSON.stringify({ event: "pong", data: f.data }));
      onEvent(f);
    };

    ws.onclose = (e) => {
      // (4) 1000 = mình chủ động đóng, 4403 = không đủ quyền. Nối lại vô ích.
      if (stopped || e.code === 1000 || e.code === 4403) return;
      // (2) Backoff: 0.5s, 1s, 2s… tối đa 30s, cộng nhiễu để tránh cả đàn cùng đập vào.
      const delay = Math.min(30000, 500 * 2 ** attempt++) + Math.random() * 300;
      setTimeout(open, delay);
    };
  };

  open();
  return {
    send: (event, data) => ws?.readyState === 1 && ws.send(JSON.stringify({ event, data })),
    join: (room) => { rooms.add(room); return ws?.send(JSON.stringify({ event: "room.join", data: { room } })); },
    close: () => { stopped = true; ws?.close(1000); },
  };
}

// dùng thử
const s = connect("ws://localhost:8000/ws/chat?client_id=an");
s.join("room:1");
s.send("message.send", { room: "room:1", text: "chào" });
```

Cần bản đầy đủ hơn (ack có Promise, TypeScript, React hook) thì xem
[mục 9](#9-client-nextjs-đầy-đủ).

### Vì sao phải trả lời `ping`

Đây là chỗ vấp nhiều nhất, nên nói kỹ.

Server chạy **hai đồng hồ độc lập nhau**:

- Cứ **25 giây** (`APP_WS__HEARTBEAT_SECONDS`), server đẩy xuống một khung `ping`.
- Nếu **70 giây** (`APP_WS__IDLE_TIMEOUT_SECONDS`) trôi qua mà server **không nhận
  được khung nào từ client**, nó đóng kết nối với mã **4408**.

Điều then chốt: **server tự gửi ping đi KHÔNG làm mới đồng hồ thứ hai.** Chỉ khi
client gửi ngược lên một khung thì đồng hồ mới được reset. Client chỉ ngồi nghe
sẽ chết theo đúng lịch này:

```
0s ──── 25s ──── 50s ──── 70s
 │       │        │        │
 mở     ping     ping     ĐÓNG 4408   ← client không hề gửi gì lên
```

```
0s ──── 25s ──── 50s ──── 75s ──── 100s ── …
 │       │↕       │↕       │↕       │↕
 mở     ping     ping     ping     ping     ← client trả pong, sống mãi
        pong     pong     pong     pong
```

Cái bẫy nằm ở chỗ: `ping` này là **khung JSON của ứng dụng**
(`{"event":"ping","data":{…}}`), **không phải** ping/pong của giao thức WebSocket.
Trình duyệt tự động trả lời ping *giao thức*, nhưng **không** tự trả lời khung
JSON này — bạn phải tự viết.

Thật ra gửi **bất kỳ** khung nào cũng làm mới đồng hồ, không nhất thiết phải là
`pong`. Nhưng cứ trả `pong` cho đúng ý đồ và dễ đọc log.

> **Đừng tắt hạn im lặng** bằng cách đặt `IDLE_TIMEOUT_SECONDS` cực lớn. Khi
> client rớt mạng đột ngột (rút dây, tắt wifi, hết pin), TCP **không báo cho ai
> cả** — không có gói FIN nào. Hạn im lặng là thứ duy nhất dọn được những socket
> "ma" đó; bỏ nó thì chúng tích lại tới khi hết RAM. Cần nới thì nới vừa phải, và
> nhớ giữ `IDLE_TIMEOUT` lớn hơn `HEARTBEAT` ít nhất 2–3 lần.

---

## 3. Khuôn tin nhắn

WebSocket là ống byte trần: không URL, không status code. Nên phải quy ước một
khuôn, và đây là khuôn đó — giống cặp `event` + `data` của socket.io.

**Client gửi lên**

```json
{"event": "message.send", "data": {"room": "room:1", "text": "chào"}, "id": "c1"}
```

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `event` | có | tên sự kiện, quyết định handler nào chạy |
| `data` | không | tham số, tuỳ handler |
| `id` | không | mã do client tự đặt, để ghép câu trả lời với câu hỏi |

**Server trả lời** — chỉ khi client có gửi `id`:

```json
{"event": "message.send", "data": {"delivered": 2}, "ack": "c1"}
```

**Server đẩy chủ động** — không có `ack`:

```json
{"event": "message.new", "data": {"from": "an", "text": "chào"}}
```

**Lỗi** — cùng hình dạng với lỗi HTTP của khung (`code`/`message`/`details`):

```json
{"event": "error", "data": {"code": "validation_error", "message": "Dữ liệu không hợp lệ",
 "details": [{"field": "text", "message": "Field required", "type": "missing"}]}, "ack": "c1"}
```

**Vì sao cần `id`/`ack`:** WebSocket không ghép cặp request–response như HTTP. Gửi
ba lệnh liên tiếp rồi nhận ba câu trả lời thì không có cách nào biết cái nào của
cái nào, trừ khi tự đánh số. Không cần biết kết quả thì cứ bỏ `id` đi.

### Sự kiện khung dựng sẵn

| Sự kiện | Chiều | Ghi chú |
|---|---|---|
| `connected` | server → client | khung **đầu tiên** sau khi nối, mang `socket_id`, `user_id`, `namespace`, `heartbeat_seconds` |
| `ping` | server → client | nhịp tim — **client phải trả lời `pong`** ([mục 2](#vì-sao-phải-trả-lời-ping)) |
| `pong` | client → server | server im lặng, chỉ ghi nhận là còn sống |
| `ping` | client → server | chiều ngược lại cũng được: server trả `pong` ngay |
| `room.join` / `room.leave` | client → server | chỉ khi gateway bật `client_rooms=True` |
| `error` | server → client | mọi lỗi |

Đọc `heartbeat_seconds` trong khung `connected` thì client biết chu kỳ ping thật
mà server đang dùng, không phải đoán.

---

## 4. Viết một gateway

```bash
fam module alerts --gateway        # module mới, có sẵn gateway
fam module alerts --gateway-only   # thêm gateway vào module đã có
```

Không phải đăng ký ở đâu cả — `register_routes` trong `src/main.py` tự quét và gắn.

```python
from fastapi_modular.core.websocket import Socket, WebSocketServer, gateway, subscribe


@gateway(path="/ws/alerts", guards=[WsJwt], client_rooms=True)
class AlertGateway:
    def __init__(self, service: AlertService, server: WebSocketServer) -> None:
        self._service = service      # service viết cho REST dùng lại nguyên vẹn
        self._server = server

    @subscribe("alert.ack")
    async def acknowledge(self, socket: Socket, payload: AlertAck) -> dict:
        await self._service.acknowledge(payload.alert_id, by=socket.user_id)
        return {"ok": True}          # trả về gì thì client nhận cái đó làm ack
```

### `@gateway(...)`

```python
@gateway(
    *,
    path,                  # str — "/ws/alerts"; KHÔNG nằm dưới tiền tố /api
    guards=(),             # Sequence[type] — chạy MỘT LẦN lúc bắt tay
    client_rooms=False,    # cho phép client tự gửi room.join / room.leave
    name=None,             # str | None — tên route; mặc định lấy tên class
)
```

| Tham số | Không truyền thì | Truyền khi nào |
|---|---|---|
| `path` | *bắt buộc* | luôn phải có; phải bắt đầu bằng `/` |
| `guards` | không xác thực, ai nối cũng được | `[WsJwt]` — xem [mục 6](#6-xác-thực) |
| `client_rooms` | client **không** vào phòng được, server tự quyết | `True` khi client tự chọn phòng — nhớ viết `can_join()` |
| `name` | tên class | hiếm khi cần |

### Hook tuỳ chọn

Khai method nào thì có hook đó, không khai thì bỏ qua:

| Method | Chữ ký | Chạy khi nào |
|---|---|---|
| `on_connect` | `async (self, socket)` | sau guard, trước khi client nhận `connected` |
| `on_disconnect` | `async (self, socket, code)` hoặc `async (self, socket)` | kết nối đứt, dù vì lý do gì |
| `can_join` | `(self, socket, room) -> bool` — `def` thường cũng được | client gửi `room.join`; trả `False` là từ chối |

```python
async def on_connect(self, socket: Socket) -> None:
    socket.join(f"user:{socket.user_id}")

def can_join(self, socket: Socket, room: str) -> bool:
    return room.startswith("site:")
```

### `@subscribe("tên.sự_kiện")`

Gắn method vào một tên sự kiện. Ba dạng chữ ký:

| Viết | Nhận được |
|---|---|
| `async def f(self, socket: Socket)` | không cần payload |
| `async def f(self, socket: Socket, payload: MyDTO)` | `data` đã validate bằng pydantic |
| `async def f(self, socket: Socket, payload: dict)` | `data` thô, không validate |

- Sai chữ ký thì **báo lỗi ngay lúc khởi động**, không phải đợi tới lúc có client
  gửi tin.
- Trả về khác `None` và client có gửi `id` thì khung tự gửi ack; trả `None` thì
  im lặng.
- Ném `AppError` (`NotFoundError`, `ForbiddenError`…) thì client nhận khung
  `error` mang đúng `code`, **kết nối vẫn giữ** — không bị đóng.
- Handler `@subscribe` **kế thừa được**: gộp bộ sự kiện dùng chung thành một lớp
  rồi trộn vào gateway; lớp con khai lại cùng tên sự kiện là ghi đè.

### `Socket` — một kết nối

| Thuộc tính | Kiểu | Ý nghĩa |
|---|---|---|
| `socket.id` | `str` | mã kết nối, 16 ký tự |
| `socket.user_id` | `str \| None` | do guard xác lập lúc bắt tay |
| `socket.roles` | `frozenset[str]` | vai trò |
| `socket.rooms` | `set[str]` | phòng đang ở |
| `socket.data` | `dict` | chỗ tuỳ ý của bạn, sống theo kết nối |
| `socket.pending` | `int` | số tin còn chờ gửi — soi client nào đang chậm |
| `socket.closing` | `bool` | đang đóng dở |

| Method | Chữ ký | Ghi chú |
|---|---|---|
| `emit` | `(event, data=None, *, ack=None, meta=None) -> bool` | **không** cần `await`; `False` = tin bị bỏ |
| `join` / `leave` | `(room)` | gọi lại nhiều lần không sao |
| `in_room` | `(room) -> bool` | |
| `close` | `async (code=1000, reason="")` | xem [bảng mã đóng](#mã-đóng-kết-nối) |
| `on_close` | `(hook)` | đăng ký việc cần làm khi kết nối đóng |

### `WebSocketServer` — đẩy tin từ bất cứ đâu

Nhận qua `__init__` như mọi provider; dùng được trong controller HTTP, service,
tác vụ nền — không cần đang ở trong một kết nối nào.

| Gọi | Gửi cho |
|---|---|
| `await server.to_room("room:1", "ev", data)` | cả phòng |
| `await server.to_user("an", "ev", data)` | mọi kết nối của một người (mọi tab) |
| `await server.to_socket(sid, "ev", data)` | đúng một kết nối |
| `await server.broadcast("ev", data)` | toàn bộ namespace |

Bốn hàm trên là lối tắt của `emit`, đủ dùng cho hầu hết trường hợp:

```python
await server.emit(
    event,                 # str — tên sự kiện client nhận
    data=None,             # Any — json hoá được
    *,
    namespace=None,        # None = tự đoán khi chỉ có MỘT gateway
    room=None,             # gửi cho một phòng
    user=None,             # gửi cho mọi kết nối của một người
    socket=None,           # gửi cho đúng một kết nối
    exclude=(),            # bỏ qua các socket id này
    local_only=False,      # True = không phát sang worker khác
) -> int
```

| Tham số | Không truyền thì | Truyền khi nào |
|---|---|---|
| `namespace` | tự đoán nếu chỉ có một gateway; **báo lỗi** nếu có nhiều | luôn truyền khi có từ hai gateway trở lên |
| `exclude` | gửi cho tất cả | `exclude=[socket.id]` để người gửi không nhận lại tin của mình |
| `local_only` | có phát sang worker khác qua adapter | `True` cho tin chỉ có ý nghĩa với worker này |

**Giá trị trả về** là số kết nối **trong worker này** đã nhận tin.

> `0` **không** có nghĩa là gửi hỏng — chỉ nghĩa là người nhận không có kết nối
> nào ở worker này. WebSocket là kênh truyền, **không phải hộp thư**: người nhận
> offline thì tin trôi mất. Cần chắc chắn tới nơi thì lưu database rồi cho client
> tải lại lúc nối vào.

### Xem ai đang ở đâu

```python
ns = socket.namespace                  # hoặc server.namespace("/ws/alerts")
ns.sockets_in("site:1")   -> list[Socket]
ns.sockets_of("an")       -> list[Socket]      # mọi tab của một người
ns.room_size("site:1")    -> int
ns.get(socket_id)         -> Socket | None
ns.stats()                -> {"path", "sockets", "users", "rooms", "pending"}
server.stats()            -> {"adapter", "origin", "namespaces", "connections"}
```

---

## 5. Phòng

Phòng chỉ là một cái tên. Vào bằng `socket.join("x")`, ra bằng
`socket.leave("x")`, đứt kết nối thì tự ra khỏi mọi phòng.

**Cách 1 — server quyết định** (mặc định, an toàn nhất):

```python
async def on_connect(self, socket: Socket) -> None:
    for site in await self._service.sites_of(socket.user_id):
        socket.join(f"site:{site}")
```

**Cách 2 — client tự xin vào**: bật `client_rooms=True`, client gửi
`{"event":"room.join","data":{"room":"site:1"}}`.

> **Bật `client_rooms=True` mà không viết `can_join()` thì ai cũng vào được phòng
> bất kỳ** — kể cả phòng riêng của người khác, vì tên phòng là thứ đoán được.
> Luôn viết `can_join()`.

```python
def can_join(self, socket: Socket, room: str) -> bool:
    return room == f"user:{socket.user_id}" or room.startswith("public:")
```

Mỗi kết nối vào tối đa `APP_WS__MAX_ROOMS_PER_SOCKET` phòng (mặc định 64).

> **Nhớ:** phòng gắn với **kết nối**, không gắn với người. Client nối lại là một
> kết nối mới, không còn ở phòng nào cả — phải tự vào lại (việc số 3 ở đầu trang).

---

## 6. Xác thực

Guard chạy **một lần lúc bắt tay**, dùng chung lớp `Guard` với HTTP:

```python
@injectable
class WsJwt:
    async def check(self, connection: HTTPConnection) -> None:
        token = connection.query_params.get("token") or connection.headers.get("authorization")
        if not token:
            raise UnauthorizedError("Thiếu token")
        claims = decode(token)                       # thư viện JWT của bạn
        current_principal().assume(id=claims["sub"], roles=set(claims["roles"]))
```

Danh tính lấy từ `Principal` rồi gắn lên `Socket`; mỗi tin nhắn sau đó chạy trong
một request scope riêng đã điền sẵn `Principal`, nên service dùng
`current_principal()` chạy y như khi được gọi từ REST.

Ba điều cần biết:

1. **Trình duyệt không đặt được header trên WebSocket.** `new WebSocket(url)`
   không có tham số headers — đây là giới hạn của trình duyệt, không phải của
   khung. Token phải đi qua query (`?token=…`) hoặc qua `Sec-WebSocket-Protocol`.
   Guard mẫu `RequireHeader` vì vậy nhận cả header `X-Client-Id` lẫn query
   `?client_id=`.
2. **Token nằm trong URL sẽ lọt vào access log** của proxy. Dùng token ngắn hạn
   phát riêng cho kênh realtime, đừng dùng access token dài hạn.
3. **Kết nối được `accept()` rồi mới bị đóng** nếu guard từ chối. Cố ý như vậy:
   đóng trước khi accept thì trình duyệt chỉ thấy "handshake failed" và không đọc
   được lý do; accept rồi đóng thì client nhận đúng mã 4401/4403 và biết phải xin
   token mới thay vì nối lại vô hạn.

### Mã đóng kết nối

Dải 4000–4999 dành cho ứng dụng; ở đây cố ý ánh xạ 1-1 với HTTP để đọc log không
phải tra bảng.

| Mã | Nghĩa | Client nên làm gì |
|---|---|---|
| 1000 | đóng bình thường | **không** nối lại |
| 1001 | server đang tắt/restart | nối lại ngay |
| 1013 | client đọc quá chậm, hàng đợi đầy | nối lại rồi **tải lại trạng thái** |
| 4400 | khung tin sai khuôn | sửa code client |
| 4401 | chưa xác thực | xin token mới **rồi mới** nối lại |
| 4403 | không đủ quyền | **không** nối lại |
| 4408 | im lặng quá lâu | nối lại — và sửa client cho biết trả `pong` |
| 4429 | vượt trần kết nối | nối lại sau, có backoff |

---

## 7. Giới hạn và cấu hình

**Không biến nào bắt buộc** — WebSocket chạy được mà không cần đặt gì trong
`.env`. Xoá dòng nào thì biến đó quay về mặc định. Xem đang chạy với gì:
`fam info`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `APP_WS__ADAPTER` | `local` | `local` \| `redis` — xem [mục 8](#8-chạy-nhiều-worker) |
| `APP_WS__REDIS_URL` | `redis://localhost:6379/0` | chỉ dùng khi adapter là `redis` |
| `APP_WS__CHANNEL` | `ws:broadcast` | kênh pub/sub dùng chung |
| `APP_WS__SEND_QUEUE_SIZE` | `100` | trần tin chờ gửi mỗi kết nối |
| `APP_WS__OVERFLOW` | `close` | `close` \| `drop_oldest` |
| `APP_WS__HEARTBEAT_SECONDS` | `25.0` | chu kỳ server gửi ping |
| `APP_WS__IDLE_TIMEOUT_SECONDS` | `70.0` | im lặng quá lâu thì đóng (4408) |
| `APP_WS__MAX_MESSAGE_BYTES` | `65536` | khung dài hơn bị từ chối |
| `APP_WS__MAX_MESSAGES_PER_SECOND` | `50.0` | `0` để tắt |
| `APP_WS__BURST_MESSAGES` | `100` | mức bùng tức thời |
| `APP_WS__MAX_CONNECTIONS` | `5000` | mỗi worker |
| `APP_WS__MAX_CONNECTIONS_PER_USER` | `10` | `0` để tắt |
| `APP_WS__MAX_ROOMS_PER_SOCKET` | `64` | trần số phòng một kết nối được vào |

`fam install ws-redis` ghi sẵn nhóm biến này vào `.env` kèm giải thích từng dòng.

<details>
<summary><b>Bốn cơ chế bảo vệ chạy sẵn — bấm để đọc vì sao chúng tồn tại</b></summary>

**Hàng đợi gửi có trần.** Mỗi kết nối có một hàng đợi và đúng một task ghi
(Starlette không cho hai task cùng ghi vào một socket). Client đọc chậm mà server
cứ đẩy thì hàng đợi phình tới lúc hết RAM — **một client hỏng kéo sập cả worker**.
Đầy hàng đợi thì xử theo `APP_WS__OVERFLOW`:

- `close` (mặc định): ngắt kết nối, mã 1013. Client nối lại và tải lại trạng thái
  — trung thực hơn là âm thầm nuốt tin khiến client tưởng vẫn đang đồng bộ.
- `drop_oldest`: bỏ tin cũ nhất. Hợp với dữ liệu chỉ cần bản mới nhất (vị trí,
  nhiệt độ, tiến độ).

**Nhịp tim + hạn im lặng.** Xem [giải thích đầy đủ ở mục 2](#vì-sao-phải-trả-lời-ping).
Chu kỳ ping 25s cố ý đặt dưới ngưỡng ~60s mà nginx/ALB/Cloudflare cắt kết nối
nhàn rỗi.

**Trần tần suất.** Mỗi kết nối có một gáo token: `MAX_MESSAGES_PER_SECOND`
(50/giây) với mức bùng `BURST_MESSAGES`. Vượt thì nhận khung `error` code
`too_many_requests`, **kết nối vẫn giữ**.

**Trần kết nối.** `MAX_CONNECTIONS` mỗi worker và `MAX_CONNECTIONS_PER_USER` cho
một tài khoản — chặn cảnh một client lỗi mở kết nối vô hạn.

</details>

---

## 8. Chạy nhiều worker

**Đây là cái bẫy lớn nhất của realtime, và nó chỉ lộ ra khi lên production.**

Sổ kết nối nằm trong **RAM của một tiến trình**. `fam run --workers 4` là bốn
tiến trình riêng biệt: client A nối vào worker 1, client B nối vào worker 3. A
gửi tin cho phòng thì worker 1 chỉ thấy kết nối của chính nó — **B không nhận
được gì**. Trên máy dev một worker thì mọi thứ chạy hoàn hảo, lên staging mới hỏng.

Cách chữa (đúng vai trò của Redis adapter trong NestJS):

```bash
fam install ws-redis     # cài redis + ghi sẵn APP_WS__* vào .env
```

```dotenv
APP_WS__ADAPTER=redis
APP_WS__REDIS_URL=redis://localhost:6379/0
```

Mỗi lần phát tin, worker vừa gửi cho kết nối tại chỗ vừa đăng lên kênh Redis; các
worker khác nghe và gửi tiếp cho phần kết nối của mình. Redis rớt thì adapter tự
nối lại kèm backoff, và tin **trong cùng worker vẫn chạy bình thường** — mất kênh
chung chứ không mất chức năng.

Khung sẽ cảnh báo lúc khởi động nếu `env=prod` mà adapter vẫn là `local`.

### Cấu hình nginx

Reverse proxy phải được bảo là đường dẫn này cần nâng cấp giao thức, nếu không
kết nối sẽ chết ở bước bắt tay hoặc bị cắt sau 60 giây:

```nginx
location /ws/ {
    proxy_pass http://app;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;     # dài hơn nhịp tim rất nhiều
    proxy_send_timeout 3600s;
}
```

Nếu đứng sau load balancer, bật **sticky session** hoặc dùng adapter redis
(khuyến nghị dùng cả hai).

---

## 9. Client Next.js đầy đủ

Bản ở [mục 2](#2-client-tối-thiểu-chạy-được-ngay) đủ để chạy. Bản dưới thêm:
ack dạng Promise, TypeScript, và React hook dọn dẹp đúng cách.

### `.env.local`

```dotenv
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/chat
```

Lên production dùng `wss://` — trang HTTPS mà mở `ws://` sẽ bị trình duyệt chặn.

### `lib/ws-client.ts`

```ts
export type Frame<T = unknown> = { event: string; data?: T; id?: string; ack?: string };
type Handler = (data: any, frame: Frame) => void;

export type WsStatus = "connecting" | "open" | "closed";

export class WsClient {
  private ws: WebSocket | null = null;
  private seq = 0;
  private attempt = 0;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly pending = new Map<string, { ok: (v: any) => void; fail: (e: Error) => void; t: any }>();
  private readonly handlers = new Map<string, Set<Handler>>();
  private readonly rooms = new Set<string>();

  constructor(
    private readonly url: string,
    private readonly onStatus: (s: WsStatus) => void = () => {},
  ) {}

  connect() {
    this.stopped = false;
    this.onStatus("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.onStatus("open");
      // Kết nối mới = phòng cũ không còn. Phải vào lại, nếu không sẽ "im lặng
      // sau khi mất mạng" — lỗi hay gặp nhất và khó lần ra nhất.
      for (const room of this.rooms) this.send("room.join", { room });
    };

    ws.onmessage = (e) => {
      const frame: Frame = JSON.parse(e.data);

      // Trả lời nhịp tim của server, nếu không sẽ bị đóng với mã 4408.
      if (frame.event === "ping") { this.send("pong"); return; }

      if (frame.ack) {
        const waiter = this.pending.get(frame.ack);
        if (waiter) {
          this.pending.delete(frame.ack);
          clearTimeout(waiter.t);
          if (frame.event === "error") {
            waiter.fail(Object.assign(new Error((frame.data as any)?.message), { data: frame.data }));
          } else {
            waiter.ok(frame.data);
          }
          return;
        }
      }

      for (const h of this.handlers.get(frame.event) ?? []) h(frame.data, frame);
    };

    ws.onclose = (e) => {
      this.onStatus("closed");
      for (const [, w] of this.pending) { clearTimeout(w.t); w.fail(new Error("mất kết nối")); }
      this.pending.clear();

      // 1000 = đóng chủ động, 4403 = không đủ quyền: nối lại cũng vô ích.
      if (this.stopped || e.code === 1000 || e.code === 4403) return;
      const delay = Math.min(30_000, 500 * 2 ** this.attempt++) + Math.random() * 300;
      this.timer = setTimeout(() => this.connect(), delay);
    };
  }

  /** Gửi một chiều, không chờ trả lời. */
  send(event: string, data?: unknown) {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ event, data }));
    return true;
  }

  /** Gửi và chờ ack — dùng cho lệnh cần biết kết quả. */
  request<T = any>(event: string, data?: unknown, timeoutMs = 10_000): Promise<T> {
    return new Promise((ok, fail) => {
      if (this.ws?.readyState !== WebSocket.OPEN) return fail(new Error("chưa kết nối"));
      const id = String(++this.seq);
      const t = setTimeout(() => { this.pending.delete(id); fail(new Error(`${event} quá hạn`)); }, timeoutMs);
      this.pending.set(id, { ok, fail, t });
      this.ws.send(JSON.stringify({ event, data, id }));
    });
  }

  on(event: string, handler: Handler) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event)!.add(handler);
    return () => this.handlers.get(event)?.delete(handler);
  }

  async join(room: string) { this.rooms.add(room); return this.request("room.join", { room }); }
  async leave(room: string) { this.rooms.delete(room); return this.request("room.leave", { room }); }

  close() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close(1000, "client đóng");
  }
}
```

### `hooks/use-socket.ts`

```ts
"use client";

import { useEffect, useRef, useState } from "react";
import { WsClient, type WsStatus } from "@/lib/ws-client";

export function useSocket(token: string | null) {
  const ref = useRef<WsClient | null>(null);
  const [status, setStatus] = useState<WsStatus>("closed");

  useEffect(() => {
    if (!token) return;                       // chưa đăng nhập thì chưa nối
    const url = `${process.env.NEXT_PUBLIC_WS_URL}?client_id=${encodeURIComponent(token)}`;
    const client = new WsClient(url, setStatus);
    ref.current = client;
    client.connect();

    // Bắt buộc: React StrictMode mount hai lần ở dev, không dọn thì thành hai
    // kết nối song song và tin nhắn bị nhân đôi.
    return () => { client.close(); ref.current = null; };
  }, [token]);

  return { socket: ref.current, status };
}
```

### Dùng trong component

```tsx
"use client";

import { useEffect, useState } from "react";
import { useSocket } from "@/hooks/use-socket";

type ChatMessage = { room: string; from: string; text: string; at: string };

export function ChatRoom({ room, token }: { room: string; token: string }) {
  const { socket, status } = useSocket(token);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  useEffect(() => {
    if (!socket || status !== "open") return;
    socket.join(room).catch(console.error);
    const off = socket.on("message.new", (m: ChatMessage) => {
      setMessages((prev) => [...prev, m]);
    });
    return () => { off(); socket.leave(room).catch(() => {}); };
  }, [socket, status, room]);

  async function send(text: string) {
    try {
      const ack = await socket!.request("message.send", { room, text });
      console.log("đã tới", ack.delivered, "kết nối");
    } catch (e) {
      console.error("gửi hỏng", e);          // hết hạn ack hoặc server trả error
    }
  }

  return (
    <div>
      <span>{status === "open" ? "● trực tuyến" : "○ đang nối lại"}</span>
      <ul>{messages.map((m, i) => <li key={i}><b>{m.from}</b>: {m.text}</li>)}</ul>
      <button onClick={() => send("chào")}>Gửi</button>
    </div>
  );
}
```

---

## 10. Thử tay bằng Postman

Postman **desktop** (không phải bản web) hỗ trợ WebSocket thô.

1. **New → WebSocket** (hoặc `Ctrl+N` rồi chọn WebSocket).
2. Chọn kiểu **Raw** — *không phải* Socket.IO; khung này không dùng giao thức
   socket.io.
3. URL: `ws://localhost:8000/ws/chat`
4. Tab **Params**: thêm `client_id` = `an`. (Bản desktop cũng cho thêm header
   `X-Client-Id` ở tab **Headers** — trình duyệt thì không.)
5. Bấm **Connect**. Ngăn dưới hiện ngay khung `connected`:

   ```json
   {"event":"connected","data":{"socket_id":"…","user_id":"an","namespace":"/ws/chat","heartbeat_seconds":25}}
   ```

6. Ngăn **Message**, chọn **JSON**, dán rồi bấm **Send**.

Bộ tin để thử lần lượt:

```json
{"event": "whoami", "id": "2"}
{"event": "room.join", "data": {"room": "room:1"}, "id": "3"}
{"event": "message.send", "data": {"room": "room:1", "text": "chào"}, "id": "4"}
{"event": "message.direct", "data": {"to_user": "binh", "text": "riêng"}, "id": "5"}
{"event": "presence.list", "data": {"room": "room:1"}, "id": "6"}
{"event": "room.leave", "data": {"room": "room:1"}, "id": "7"}
{"event": "ping", "id": "8"}
```

Mẹo:

- **Postman không tự trả lời `pong`.** Ngồi im quá 70 giây sẽ bị đóng với mã
  4408 — *đó là hành vi đúng, không phải lỗi*. Gửi tay `{"event":"pong"}` hoặc
  bất kỳ tin nào để làm mới đồng hồ.
- Mở **hai tab** với `client_id` khác nhau (`an`, `binh`) để thấy tin chạy qua
  lại: tab `an` gửi `message.send`, tab `binh` nhận `message.new`.
- Muốn thấy đẩy tin từ REST: giữ tab WebSocket đang ở `room:1`, rồi gọi
  `POST localhost:8000/api/chat/broadcast` bằng một request HTTP thường.
- Bấm **Save** để cất từng tin vào collection; lần sau chỉ việc chọn và Send.
- Thử lỗi cho biết: gửi `{"event":"khong-co-that"}` → `unknown_event`; gửi
  `{"event":"message.send","data":{"room":"room:1"}}` → `validation_error` chỉ
  đúng trường thiếu.
- Kết nối không có `?client_id=` sẽ nhận khung `error` rồi bị đóng **4401**.

---

## 11. Số đo và log

```bash
curl localhost:8000/api/metrics | grep ws_
```

| Số đo | Loại | Ý nghĩa |
|---|---|---|
| `ws_connections` | gauge | kết nối đang mở, theo `namespace` |
| `ws_connections_total` | counter | tổng kết nối đã mở |
| `ws_messages_in_total` | counter | khung nhận vào, theo `event` |
| `ws_messages_out_total` | counter | khung đã xếp hàng gửi |
| `ws_send_dropped_total` | counter | khung bị bỏ vì hàng đợi đầy |

`ws_send_dropped_total` tăng nghĩa là có client đọc không kịp: hoặc mạng họ yếu,
hoặc server đang đẩy quá dày.

Mỗi tin nhắn có `request_id` riêng trong log, và `ws.disconnected` ghi lại mã
đóng cùng thời lượng phiên. Thấy nhiều `ws.idle_timeout` trong log tức là có
client chưa biết trả `pong`.

---

## 12. Test

`tests/test_websocket.py` chạy hoàn toàn trong tiến trình, không cần server:

```python
def test_gui_theo_phong(client):
    with client.websocket_connect("/ws/chat?client_id=an") as an, \
         client.websocket_connect("/ws/chat?client_id=binh") as binh:
        an.receive_json()          # khung connected
        binh.receive_json()
        an.send_json({"event": "room.join", "data": {"room": "r"}, "id": "1"})
        ...
```

```bash
fam test
```

Phần xuyên worker (`tests/test_ws_adapter.py`) mặc định được bỏ qua vì cần Redis
thật. Chạy đầy đủ:

```bash
docker run -d --name redis-test -p 6380:6379 redis:7-alpine
fam install ws-redis
TEST_REDIS_URL=redis://localhost:6380/0 fam test
```

Muốn tự mắt thấy cái bẫy nhiều worker: chạy 6 client vào cùng một phòng trên
server 2 worker. Với `APP_WS__ADAPTER=local` chỉ 4/6 client nhận được tin (số
khác nhau mỗi lần, tuỳ client rơi vào worker nào); với `redis` là 6/6.

---

## 13. Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân gần như chắc chắn | Cách chữa |
|---|---|---|
| **Đứt sau ~70 giây, mã 4408**, dù mạng vẫn tốt | Client không trả lời `ping` | Trả `pong` — [mục 2](#vì-sao-phải-trả-lời-ping) |
| Đứt sau ~60 giây ở production, dev thì không | Nginx/Cloudflare cắt kết nối nhàn rỗi | Nâng `proxy_read_timeout` — [mục 8](#cấu-hình-nginx) |
| **Nối lại được nhưng không nhận tin nữa** | Kết nối mới không còn ở phòng cũ | Vào lại phòng trong `onopen` — [mục 5](#5-phòng) |
| Chỉ **một phần** client nhận được tin | Nhiều worker, adapter còn là `local` | `APP_WS__ADAPTER=redis` — [mục 8](#8-chạy-nhiều-worker) |
| Tin nhắn **bị nhân đôi** khi dev React/Next | StrictMode mount hai lần | `client.close()` trong hàm dọn của `useEffect` |
| `WebSocket is not defined` lúc build Next.js | Đang tạo kết nối ở Server Component | Chỉ tạo trong `useEffect`, file có `"use client"` |
| Trang HTTPS không nối được | Dùng `ws://` trên trang `https://` | Đổi sang `wss://` |
| Rewrite trong `next.config.js` không ăn | Rewrite của Next **không proxy được WebSocket** | Trỏ thẳng `NEXT_PUBLIC_WS_URL` vào backend, hoặc đặt nginx phía trước |
| Đóng ngay **4401** khi vừa nối | Guard từ chối: thiếu/sai token | Kiểm tra `?client_id=` hoặc `?token=` |
| Đóng **4403**, nối lại mãi không được | Không đủ quyền — nối lại vô ích | Client phải **ngừng** nối lại ở mã này |
| Đóng **1013** | Client đọc quá chậm, hàng đợi đầy | Nối lại rồi **tải lại trạng thái**; cân nhắc `OVERFLOW=drop_oldest` |
| Nhận `error` code `too_many_requests` | Vượt trần tần suất (50 tin/giây) | Gộp tin lại, hoặc nới `APP_WS__MAX_MESSAGES_PER_SECOND` |
| Nhận `error` code `unknown_event` | Tên sự kiện không khớp `@subscribe` nào | Kiểm tra chính tả tên sự kiện |
| `emit`/`to_room` trả về `0` | Người nhận không online **ở worker này** | Bình thường. Cần chắc tới nơi thì lưu database |
| Gửi được nhưng client không thấy gì | Client chưa `join` đúng phòng | Kiểm tra bằng `curl localhost:8000/api/chat/stats` |
| Lỗi "nhiều gateway, phải chỉ rõ namespace" | Có từ 2 gateway trở lên | Truyền `namespace="/ws/…"` vào `emit` |

Chưa ra thì xem log. `fam dev` in các dòng `ws.*` đáng chú ý sau, và `fam info`
cho biết cấu hình đang chạy thật:

| Dòng log | Nghĩa |
|---|---|
| `ws.idle_timeout` | đóng vì client im lặng quá lâu — client chưa biết trả `pong` |
| `ws.disconnected` | mọi lần đứt, kèm **mã đóng** và thời lượng phiên |
| `ws.guard_failed` | guard từ chối lúc bắt tay (dẫn tới 4401/4403) |
| `ws.slow_consumer` | client đọc không kịp, hàng đợi đang đầy dần |
| `ws.rejected_full` | từ chối kết nối mới vì chạm trần |
| `ws.handler_failed` | handler `@subscribe` ném lỗi ngoài dự kiến |

Riêng lỗi vượt tần suất **không ghi log** — nó chỉ gửi khung `error` code
`too_many_requests` xuống client, nên phải soi ở phía client.

---

## 14. Bảng lệnh

```bash
fam module alerts --gateway        # module mới kèm gateway
fam module alerts --gateway-only   # thêm gateway vào module có sẵn
fam install ws-redis               # adapter cho nhiều worker
fam info                           # đang chạy với cấu hình gì
fam dev
```
