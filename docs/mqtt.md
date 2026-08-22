# MQTT

Tuỳ chọn. Không cài, không bật thì không ảnh hưởng gì tới phần còn lại.

```bash
pym install mqtt     # cài thư viện + ghi APP_MQTT__* vào .env
```

Dùng cho thiết bị IoT: giao thức nhẹ, giữ kết nối lâu, chịu được mạng chập chờn.
Hai việc làm được: **gửi tin** (`MqttClient.publish`) và **nghe topic**
(`@mqtt_subscriber`).

---

## Cấu hình

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_MQTT__ENABLED` | không | `false` | bật/tắt toàn bộ lớp này |
| `APP_MQTT__URL` | **có** | `mqtt://localhost:1883` | `mqtt://[user:pass@]host:port`, hoặc `mqtts://` nếu có TLS |
| `APP_MQTT__CLIENT_ID` | không | *(trống → sinh ngẫu nhiên)* | danh tính phiên trên broker |
| `APP_MQTT__CLEAN_SESSION` | không | `true` | `false` = broker giữ tin QoS≥1 lại trong lúc client ngắt |
| `APP_MQTT__KEEPALIVE_SECONDS` | không | `30` | nhịp tim; quá ~1.5 nhịp không thấy gì thì broker coi như client chết |
| `APP_MQTT__CONNECT_TIMEOUT_SECONDS` | không | `10.0` | chờ lần bắt tay đầu tiên |
| `APP_MQTT__RECONNECT_DELAY_SECONDS` | không | `1.0` | chờ trước lần nối lại đầu tiên |
| `APP_MQTT__MAX_RECONNECT_DELAY_SECONDS` | không | `30.0` | trần thời gian chờ (tăng gấp đôi mỗi lần) |

Hai biến đi với nhau, và sai một cái là hỏng cả hai:

- **`CLIENT_ID` trống + nhiều worker**: mỗi worker tự sinh id riêng → chạy được.
- **`CLIENT_ID` cố định + nhiều worker**: **hai worker đá nhau ra khỏi broker
  liên tục**, vì MQTT chỉ cho một phiên trên mỗi id. Đặt id thì phải kèm số thứ
  tự worker.
- **`CLEAN_SESSION=false` + `CLIENT_ID` trống**: vô nghĩa — mỗi lần khởi động là
  một phiên mới toanh nên chẳng có gì được giữ lại. Khung cảnh báo
  `mqtt.session_khong_ben` lúc boot.
- **`CLEAN_SESSION=false` + `CLIENT_ID` cố định**: broker giữ tin QoS≥1 lại
  trong lúc app tắt và giao khi nối lại. Đây là cách duy nhất để không mất tin
  lúc deploy.

---

## Gửi tin

```python
await mqtt.publish(topic, payload=None, *, qos=1, retain=False, fire_and_forget=False) -> bool
```

| Tham số | Không truyền thì | Truyền khi nào |
|---|---|---|
| `topic` | *bắt buộc* — **không được** chứa `+` hay `#` | — |
| `payload` | `None` | `dict`/`list` được mã hoá JSON; `str`/`bytes` gửi nguyên |
| `qos` | `1` — ít nhất một lần, có xác nhận | `0` cho số đo dày đặc; `2` khi xử lý trùng gây hại |
| `retain` | `False` — chỉ ai đang nghe mới nhận | `True` cho **trạng thái** (xem dưới) |
| `fire_and_forget` | `False` → chưa nối được thì ném lỗi | `True` khi thà mất tin còn hơn hỏng request |

### `retain` — chỗ hay dùng sai

`retain=True` bảo broker **giữ tin này làm giá trị hiện tại của topic**: client
nào đăng ký sau cũng nhận ngay bản mới nhất mà không phải chờ lần cập nhật kế
tiếp.

| Loại dữ liệu | `retain` | Vì sao |
|---|---|---|
| trạng thái: nhiệt độ, bật/tắt, mức pin | `True` | mở dashboard là thấy ngay, không phải chờ |
| sự kiện: nút vừa bấm, cửa vừa mở | `False` | người nối vào sau sẽ tưởng nút **vừa mới** được bấm |

### Mức QoS

| QoS | Bảo đảm | Cái giá |
|---|---|---|
| 0 | gửi rồi thôi | mất cũng không ai biết |
| 1 | ít nhất một lần | **có thể trùng** khi mạng chớp — handler phải chịu được |
| 2 | đúng một lần | hai vòng bắt tay, chậm hơn hẳn |

Lỗi ném ra:

| Lỗi | Khi nào | Mã HTTP |
|---|---|---|
| `ComponentNotEnabledError` | `APP_MQTT__ENABLED=false`, hoặc chưa cài aiomqtt | 503 |
| `BadRequestError` | topic gửi có chứa `+` hoặc `#` | 400 |
| `ServiceUnavailableError` | chưa nối được broker (trừ khi `fire_and_forget=True`) | 503 |

---

## Nghe topic

```python
@injectable
class ThietBiListener:
    @mqtt_subscriber("thiet-bi/+/nhiet-do", qos=1)
    async def nhiet_do(self, payload: NhietDo, meta: dict) -> None:
        ma = meta["topic"].split("/")[1]
```

```python
@mqtt_subscriber(topic, *, qos=1)
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `topic` | *bắt buộc* | bộ lọc, có thể chứa `+` và `#` |
| `qos` | `1` | mức bảo đảm khi broker **giao** tin cho mình |

| Chữ ký handler | Nhận được |
|---|---|
| `async def f(self, payload: MyModel)` | `payload` đã validate bằng pydantic |
| `async def f(self, payload: dict)` | JSON đã giải mã, hoặc **chuỗi thuần** nếu không phải JSON |
| `async def f(self, payload, meta: dict)` | thêm `meta` |

`meta` = `{"topic": "<topic thật>", "filter": "<bộ lọc đã khai>", "qos": 1, "retain": False}`.

Thiết bị hay gửi chuỗi thuần (`"ON"`, `"23.5"`) chứ không phải JSON — khung trả
nguyên chuỗi thay vì ném lỗi.

### Luật khớp topic

Khác routing key của AMQP dù nhìn na ná:

```
AMQP    "alert.*"     * = đúng một TỪ,   ngăn bằng dấu chấm
MQTT    "thiet-bi/+"  + = đúng một TẦNG, ngăn bằng dấu /
```

| Bộ lọc | Topic | Khớp? |
|---|---|---|
| `nha/+/den` | `nha/bep/den` | ✅ |
| `nha/+/den` | `nha/bep/tang2/den` | ❌ `+` đúng **một** tầng |
| `nha/#` | `nha/bep/den` | ✅ |
| `nha/#` | `nha` | ✅ `#` nuốt cả **không** tầng nào |
| `#` | `$SYS/broker/uptime` | ❌ đại diện không chạm topic hệ thống |

`#` phải là tầng **cuối** và chiếm trọn một tầng. Khai sai (`nha/#/den`, `nha#`)
bị từ chối ngay lúc nạp module, không phải đợi tới lúc chạy.

### Bộ lọc chồng nhau — chỗ này khung tự lo

Khai hai handler, một nghe `thiet-bi/#`, một nghe `thiet-bi/+/nhiet-do`. Nếu
đăng ký **cả hai** lên broker thì mosquitto giao **một tin thành hai bản** — mỗi
đăng ký một bản — và mọi handler khớp sẽ chạy hai lượt. Đo được: gửi 1 tin,
handler chạy **4 lượt**.

Khung chỉ đăng ký bộ lọc **rộng nhất** rồi tự chia tin trong tiến trình:

```json
"topics":    ["thiet-bi/#"],                             // gửi lên broker
"listeners": ["thiet-bi/#", "thiet-bi/+/nhiet-do"]       // handler đang có
```

QoS dồn về bộ lọc còn lại, lấy **mức cao nhất**, để tin không bị hạ cấp. Kết quả:
mỗi handler khớp chạy đúng **một lần**.

### Không có DLQ, và vì sao

MQTT **không cho client từ chối một tin**: nhận là xong, broker coi như đã giao.
Nên khi handler ném lỗi, khung ghi `mqtt.handler_failed` và **đi tiếp**. Ném ra
ngoài sẽ làm đứt vòng đọc, tức mọi handler khác im theo cho tới lần nối lại — một
tin hỏng không đáng giá vậy.

Cần chắc chắn không mất việc thì đẩy sang chỗ có hàng đợi ngay trong handler:

```python
@mqtt_subscriber("thiet-bi/+/canh-bao")
async def canh_bao(self, payload: dict, meta: dict) -> None:
    await self._rabbit.publish("events", "alert.created", payload)   # xử lý nặng ở đó
```

---

## Ví dụ chạy được

`src/api/mqtt_test/` có sẵn hai handler chồng nhau và hai endpoint:

```bash
curl -X POST localhost:8002/api/mqtt-test/gui -H 'Content-Type: application/json' \
     -d '{"topic":"thiet-bi/bep/nhiet-do","payload":{"gia_tri":31.2},"qos":1}'
curl localhost:8002/api/mqtt-test/da-nhan
```

Đo được:

```
gửi thiet-bi/bep/nhiet-do       -> nhiet_do (1 lần) + moi_thu (1 lần)
gửi thiet-bi/bep/tang2/do-am    -> chỉ moi_thu   (+ không khớp một tầng)
gửi thiếu trường gia_tri        -> chỉ moi_thu   (nhiet_do bỏ tin, log payload_invalid)
gửi vào topic "thiet-bi/#"      -> HTTP 400      (không gửi vào ký tự đại diện)
```

---

## Khi broker chưa lên

App **vẫn khởi động**: log `mqtt.starting_degraded`, và vòng
`nối → đăng ký topic → đọc → đứt → chờ → lặp lại` chạy ngầm với backoff
`1s → 2s → ... → 30s`. Mọi đăng ký topic được khai **lại** sau mỗi lần nối, vì
broker chỉ nhớ chúng khi phiên là persistent.

aiomqtt cố ý **không** tự nối lại (khác aio-pika), nên phần đó là của khung.

Không có hàng đợi chờ gửi ở phía client: giữ tin trong RAM rồi hứa gửi sau là
một lời hứa mà tiến trình chết là mất. Muốn bảo đảm thì đó là việc của QoS 1/2
với phiên persistent.

---

## Số đo

| Tên | Ý nghĩa |
|---|---|
| `mqtt_published_total` | tin đã gửi |
| `mqtt_publish_failed_total` | gửi thất bại |
| `mqtt_received_total` | tin nhận được |
| `mqtt_handler_failed_total` | handler xử lý lỗi |
| `mqtt_unrouted_total` | nhận được mà không handler nào khớp (gần như luôn là gõ nhầm bộ lọc) |

---

## Chạy thử bằng Docker

```bash
docker run -d --name mqtt-test -p 1893:1883 eclipse-mosquitto:2 \
  sh -c "printf 'listener 1883\nallow_anonymous true\n' > /m.conf && mosquitto -c /m.conf"
pym install mqtt
TEST_MQTT_URL=mqtt://localhost:1893 pym test    # bật nhóm test cần broker thật
```
