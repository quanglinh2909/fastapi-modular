# Vận hành

Ba thứ giúp API sống sót và giải thích được khi có sự cố: **guard** chặn request
sai, **circuit breaker** chặn thiệt hại lan rộng, **metrics + trace** cho biết
chuyện gì đang xảy ra.

---

## Guard

Tương đương `@UseGuards` của NestJS. Guard chỉ trả lời một câu: request này có
được đi tiếp không. Từ chối thì ném lỗi nghiệp vụ.

```python
@controller(prefix="/devices", tags=["devices"], guards=[RequireHeader])
class DeviceController:
    @delete("/{device_id}", guards=[ChiAdmin])
    async def remove(self, device_id: str) -> None: ...
```

Guard của controller chạy trước, rồi tới guard của riêng route. Guard là provider
bình thường nên nhận được phụ thuộc qua `__init__`.

Cùng lớp guard đó dùng được cho **WebSocket** — tham số là `HTTPConnection`, lớp
cha chung của `Request` và `WebSocket`:

```python
@gateway(path="/ws/alerts", guards=[RequireHeader])
class AlertGateway: ...
```

Khác biệt duy nhất: guard WebSocket chạy **một lần lúc bắt tay**, không phải mỗi
tin nhắn; và trình duyệt không đặt được header trên WebSocket nên token thường
đi qua query. Xem [websocket.md](websocket.md#6-xác-thực).

### Principal

`Principal` là "ai đang gọi request này", vòng đời theo request:

```python
from fastapi_modular.core.guards import current_principal

principal = current_principal()
principal.require_role("admin")          # ném ForbiddenError nếu thiếu vai trò
```

Guard xác thực điền vào bằng `principal.assume(id=..., roles={...})`.

Đọc bằng `current_principal()` **trong thân method**, không nhận qua `__init__`:
service là singleton còn Principal theo request, và container sẽ chặn nếu bạn
cố inject thẳng — chính là để không rò dữ liệu người này sang người khác.

### Template CHƯA có xác thực

`RequireHeader` chỉ là guard mẫu (bắt buộc có header `X-Client-Id`), cố ý chọn
ví dụ không phải xác thực để không ai nhầm là đã có bảo mật. Guard xác thực thật
viết cùng khuôn: đọc request, kiểm tra chữ ký/token, rồi `principal.assume(...)`.

---

## Circuit breaker và hạn thời gian

### Vấn đề

Database treo (không phải từ chối — *treo*). Mỗi request đi tới tận nơi, chờ hết
timeout rồi mới trả 503. Với timeout 10 giây và 100 request/giây, toàn bộ worker
bị giữ chỗ chờ vô ích và API chết theo database.

### Cách chữa

Đếm số lần hỏng liên tiếp; quá ngưỡng thì ngắt mạch, trả 503 **ngay** mà không
chạm database. Sau `reset_seconds` cho đúng một request đi thử.

```
closed  --(hỏng liên tiếp ≥ ngưỡng)-->  open
open    --(hết reset_seconds)-------->  half_open
half_open --(1 request thành công)-->   closed
          --(1 request hỏng)--------->  open
```

Đo thật với PostgreSQL bị `docker pause`, `query_timeout=2s`, ngưỡng ngắt = 2:

```
req 2 -> 503  2021ms      hạn thời gian cắt
req 3 -> 503  2018ms      đủ ngưỡng, mạch ngắt
req 4 -> 503     5ms      không chạm database nữa
req 5 -> 503     5ms
sau khi database sống lại + hết thời gian nghỉ -> 200
```

Trạng thái xem ở `/api/health/ready` và ở `/api/metrics`
(`db_circuit_state`: 0 đóng, 1 nửa mở, 2 ngắt).

### Chỉ lỗi kết nối mới làm ngắt mạch

Trùng khoá, sai dữ liệu... nghĩa là database **đang hoạt động tốt** — không tính
vào số lần hỏng. Nếu tính, một API bị spam dữ liệu sai sẽ tự cắt đường xuống
database của chính nó.

### Hạn thời gian luôn áp dụng

`APP_DB__CIRCUIT_BREAKER=false` chỉ tắt phần ngắt mạch; hạn thời gian
(`APP_DB__QUERY_TIMEOUT_SECONDS`) vẫn giữ. Thiếu nó thì một database treo sẽ giữ
chỗ worker vô thời hạn.

Phân biệt hai hạn:

| Biến | Chặn cái gì |
|---|---|
| `APP_DB__CONNECT_TIMEOUT_SECONDS` | lúc **mở** kết nối |
| `APP_DB__QUERY_TIMEOUT_SECONDS` | **một câu lệnh đã gửi đi** |

### Giới hạn đã biết

Request đang giữ một connection **mở** tới server bị đóng băng giữa chừng thì
không client-side timeout nào cắt được: asyncpg huỷ câu lệnh bằng cách mở **thêm**
một kết nối để gửi lệnh cancel, mà server đóng băng thì kết nối đó cũng treo.
Đo được request đó treo > 45 giây.

Circuit breaker chính là thứ chặn thiệt hại: các request song song vẫn timeout
đúng hạn, mạch ngắt sau vài lần, và mọi request sau đó trả 503 tức thì. Muốn
chặn triệt để phải đặt hạn ở tầng cao hơn (ingress/gateway).

---

## Metrics

`GET /api/metrics` trả định dạng Prometheus. Không thêm thư viện nào — registry
viết tay trong [`fastapi_modular/core/metrics.py`](../fastapi_modular/core/metrics.py).

```
http_requests_total{method="GET",path="/api/users/{user_id}",status="200"} 3
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.05"} 12
http_requests_in_flight 1
app_info{driver="sqlite",env="local",service="fastapi-modular",version="0.1.0"} 1
db_circuit_state{backend="sqlite"} 0
ws_connections{namespace="/ws/chat"} 42
rabbitmq_published_total{exchange="events",routing_key="alert.created"} 71
rabbitmq_dead_lettered_total{queue="alert-mailer"} 0
ws_messages_in_total{event="message.send",namespace="/ws/chat"} 918
ws_send_dropped_total{namespace="/ws/chat"} 0
```

### Nhãn là KHUÔN đường dẫn, không phải đường dẫn thật

`/api/users/abc` và `/api/users/def` là hai nhãn khác nhau; lấy đường dẫn thật
làm nhãn thì mỗi bản ghi tạo một chuỗi số đo mới và làm nổ bộ nhớ Prometheus.
Template dùng `/api/users/{user_id}`, và mọi đường dẫn không khớp route nào gom
chung vào `path="unmatched"`.

### Cấu hình Prometheus

```yaml
scrape_configs:
  - job_name: fastapi-modular
    metrics_path: /api/metrics
    static_configs:
      - targets: ["localhost:8000"]
```

Vài truy vấn hay dùng:

```promql
# tỉ lệ lỗi 5xx
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))

# độ trễ p95
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, path))

# mạch database đang ngắt
db_circuit_state > 0

# kết nối WebSocket đang mở
sum(ws_connections) by (namespace)

# có client đọc không kịp (mạng yếu, hoặc server đẩy quá dày)
rate(ws_send_dropped_total[5m]) > 0

# có tin RabbitMQ không xử lý được, đã rơi vào hàng đợi chết
rate(rabbitmq_dead_lettered_total[15m]) > 0
```

---

## WebSocket

Hướng dẫn đầy đủ ở [5-xác-thực](websocket.md). Hai điều **bắt buộc** khi đưa
lên production:

**1. Nhiều worker thì phải có adapter.** Sổ kết nối nằm trong RAM của một tiến
trình; `fam run --workers 4` là bốn sổ riêng biệt, nên broadcast chỉ tới được
client đang nối vào đúng worker đó. Máy dev một worker chạy hoàn hảo, lên
staging mới lộ.

```bash
fam install ws-redis     # cài redis + ghi APP_WS__* vào .env
```

Khung tự cảnh báo lúc khởi động nếu `env=prod` mà `ws.adapter` vẫn là `local`.

**2. Proxy phải cho nâng cấp giao thức và nới hạn nhàn rỗi**, nếu không kết nối
chết ở bước bắt tay hoặc bị cắt sau ~60 giây:

```nginx
location /ws/ {
    proxy_pass http://app;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

Soi nhanh khi có sự cố:

```bash
fam info                                  # adapter, nhịp tim, các trần
curl localhost:8000/api/chat/stats            # kết nối/phòng của worker đang trả lời
curl -s localhost:8000/api/metrics | grep ws_
```

---

## RabbitMQ

Tuỳ chọn, mặc định tắt. Hướng dẫn đầy đủ ở [rabbitmq.md](rabbitmq.md). Ba điều
cần biết khi vận hành:

**Broker rớt không làm app chết.** `/api/health/ready` vẫn trả 200 (RabbitMQ
không phải điều kiện sẵn sàng — tắt cả API vì hàng đợi rớt là đổi một sự cố nhỏ
lấy một sự cố lớn), nhưng trường `mq.connected` chuyển false và các thao tác
cần nó trả 503 `messaging_unavailable`. App tự nối lại với backoff 2s → 30s,
kể cả khi broker chưa từng lên lúc app khởi động.

**Hàng đợi chết là nơi phải nhìn** — với những consumer có bật nó
(`@rabbitmq_subscriber(..., dead_letter=True)`; mặc định là không). Tin xử lý lỗi quá số
lần cho phép sẽ nằm ở `<queue>.dlq` chứ không quay vòng vô hạn. Consumer không
bật thì tin hỏng biến mất, chỉ còn log `mq.message_dropped` — đáng đặt cảnh báo
cho chính dòng log đó. Đặt cảnh báo cho
`rabbitmq_dead_lettered_total` và soi bằng:

```bash
docker exec rabbit rabbitmqctl list_queues name messages consumers | grep dlq
```

**Đổi tham số hàng đợi phải xoá hàng đợi cũ.** RabbitMQ không cho khai lại hàng
đợi đã tồn tại với tham số khác; khung sẽ báo lỗi kèm đúng lệnh cần chạy.

```bash
fam info                                    # cấu hình đang dùng
curl -s localhost:8000/api/health/ready | jq .mq
curl -s localhost:8000/api/metrics | grep mq_
```

---

## Trace

Mỗi request có **hai** mã, đừng nhầm:

| Mã | Phạm vi | Header |
|---|---|---|
| `request_id` | chỉ dịch vụ này | `X-Request-Id` |
| `trace_id` | xuyên suốt mọi dịch vụ trong một hành trình | `X-Trace-Id` |

Template đọc header `traceparent` theo chuẩn W3C Trace Context. Bên gọi đã có
thì dùng lại, chưa có thì sinh mới:

```bash
curl -D - http://localhost:8000/api/users \
  -H 'traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'

x-request-id: db2395f26b52455f802e321c7fd96d91
x-trace-id:   4bf92f3577b34da6a3ce929d0e0e4736      ← giữ nguyên của bên gọi
```

Cả hai mã có trong **mọi dòng log** và trong **mọi response lỗi**:

```json
{"code": "not_found", "message": "Không tìm thấy user khong-co",
 "request_id": "6cac22c4...", "trace_id": "29e73815..."}
```

Người dùng báo lỗi kèm `trace_id` là tra được toàn bộ hành trình qua các dịch vụ.

### Chưa có

Template mới truyền `trace_id`, chưa sinh span và chưa gửi đi đâu. Muốn xem dạng
biểu đồ thì cắm OpenTelemetry: `trace_id` đã đúng khuôn W3C nên nối được ngay.
