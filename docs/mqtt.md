# MQTT

Tuỳ chọn. Không cài, không bật thì không ảnh hưởng gì tới phần còn lại.

```bash
fam install mqtt     # cài thư viện + ghi APP_MQTT__* vào .env
```

Dùng cho thiết bị IoT: giao thức nhẹ, giữ kết nối lâu, chịu được mạng chập chờn.
Hai việc làm được: **gửi tin** (`MqttClient.publish`) và **nghe topic**
(`@mqtt_subscriber`).

---


> Cần gửi rồi **chờ trả lời** (kiểu `client.send` / `@MessagePattern` của
> NestJS)? Đó là `mqtt.send()` và `@mqtt_responder` — [docs/rpc.md](rpc.md).

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Gửi một tin xuống thiết bị" | [Gửi tin](#gửi-tin) |
| "Cảm biến báo gì thì xử lý nấy" | [Nghe topic](#nghe-topic) |
| "Nghe **cả cụm** topic: `thiet-bi/+/nhiet-do`" | [Luật khớp topic](#luật-khớp-topic) |
| "Thiết bị mới nối vào phải thấy ngay giá trị mới nhất" | [`retain`](#retain--chỗ-hay-dùng-sai) |
| "**Không được mất tin** lúc deploy / mất mạng" | [Mức QoS](#mức-qos) + `CLEAN_SESSION` trong [Tra cứu](#tra-cứu) |
| "Gửi rồi **chờ thiết bị trả lời**" | [rpc.md](rpc.md) — `mqtt.send()` + `@mqtt_responder` |
| "Broker chết thì app có chết theo không" | [Khi broker chưa lên](#khi-broker-chưa-lên) |
| "Broker **đứt thì cảnh báo**, nối lại thì báo gateway online" | [Biết khi nào mất kết nối](#biết-khi-nào-mất-kết-nối) |
| "Hỏi **broker có đang nối không** — gọi liên tục cũng nhẹ" | [Hỏi trạng thái lúc này](#hỏi-trạng-thái-lúc-này) |
| "Bảng biến, số đo" | [Tra cứu](#tra-cứu) |

## Gửi tin

Tiêm `MqttClient` qua `__init__` như mọi provider, rồi gọi `publish`:

```python
# src/api/thiet_bi/device_service.py
from fastapi_modular import injectable
from fastapi_modular.infrastructure.mqtt import MqttClient


@injectable
class DeviceService:
    def __init__(self, mqtt: MqttClient) -> None:
        self._mqtt = mqtt

    async def turn_on_light(self, room: str) -> bool:
        return await self._mqtt.publish(f"nha/{room}/den", "ON", qos=1, retain=True)

    def is_online(self) -> bool:
        return self._mqtt.connected       # đọc RAM, gọi liên tục cũng được
```

**Tiêm, đừng tự dựng.** `MqttClient(settings)` tự tạo là một client chưa bao giờ
được mở: `connected` luôn `False`, `publish` luôn ném `ServiceUnavailableError`.
Container đưa đúng bản mà app đã mở lúc khởi động.

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

## Kiểm xem nó chạy chưa

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

## Biết khi nào mất kết nối

"Broker đứt thì ghi cảnh báo", "nối lại thì báo cho dashboard là gateway đã
online" — viết thế này:

```python
# src/api/thiet_bi/mqtt_status.py
from fastapi_modular import get_logger, injectable
from fastapi_modular.infrastructure.mqtt import MqttClient, mqtt_on_connect, mqtt_on_disconnect

log = get_logger(__name__)


@injectable
class MqttStatus:
    def __init__(self, mqtt: MqttClient) -> None:
        self.mqtt = mqtt

    @mqtt_on_connect
    async def online(self, info: dict) -> None:
        if info["reconnect"]:
            log.info("mqtt_back", downtime=info["downtime_seconds"])
        await self.mqtt.publish("gateway/status", "online", retain=True)

    @mqtt_on_disconnect
    async def offline(self, info: dict) -> None:
        log.warning("mqtt_down", error=info["error"])
```

Không cần `info` thì bỏ đi: `async def online(self) -> None:` cũng chạy.

### Khi nào handler được gọi

| Chuyện xảy ra | Handler chạy |
|---|---|
| App khởi động, nối được broker | `on_connect`, `info["reconnect"]` là `False` |
| Đang chạy thì đứt | `on_disconnect` — **một lần**, dù vòng nối lại thử hỏng bao nhiêu lượt |
| Nối lại được | `on_connect`, `info["reconnect"]` là `True` |
| Broker chưa lên lúc khởi động | **không gọi gì**; lúc broker lên thì `on_connect` với `reconnect=False` |
| Tắt app | **không** gọi `on_disconnect` |

### `info` có gì

| Khoá | Có ở | Ý nghĩa |
|---|---|---|
| `url` | cả hai | URL broker, đã che mật khẩu |
| `client_id` | cả hai | danh tính phiên đang dùng |
| `reconnect` | `on_connect` | `False` ở lần nối đầu, `True` ở mọi lần sau |
| `downtime_seconds` | `on_connect` | đứt bao nhiêu giây; `None` ở lần nối đầu |
| `error` | `on_disconnect` | lỗi làm đứt, dạng `"MqttError: ..."`; có thể là `None` |

- **Đừng dùng handler làm chốt trước khi gửi tin.** Handler chạy trong một task
  riêng, SAU khi trạng thái đã đổi. Cần biết ngay lúc gửi thì đọc
  `mqtt.connected`, hoặc gửi với `fire_and_forget=True`.
- **Đặt timeout cho việc chậm trong handler.** Các handler nối/đứt chạy lần
  lượt: một `on_connect` treo thì `on_disconnect` phía sau phải chờ. Tin MQTT
  vẫn được đọc bình thường, không bị chặn.
- **Rút cáp mạng thì báo chậm hơn tắt broker.** Broker tắt đàng hoàng là biết
  ngay; mất mạng im lặng thì phải chờ nhịp tim `APP_MQTT__KEEPALIVE_SECONDS`.

### Hỏi trạng thái lúc này

Không cần khai handler — hỏi thẳng, ở bất cứ đâu:

```python
from fastapi_modular import broker_status

status = broker_status("mqtt")
if not status["connected"]:
    print("MQTT đang đứt từ", status["since"], "vì", status["last_error"])
```

**Gọi liên tục cũng được** (mỗi khung hình, mỗi request): hàm chỉ đọc biến trong
RAM, không gửi gì qua mạng, không cần `await`.

| Khoá | Ý nghĩa |
|---|---|
| `enabled` | `False` khi `APP_MQTT__ENABLED=false` — lúc đó các khoá khác là mặc định |
| `connected` | đang nối hay không |
| `since` | `datetime` (UTC) của lần đổi trạng thái gần nhất; `None` nếu chưa từng nối |
| `last_error` | lỗi của lần đứt gần nhất; `None` nếu chưa đứt lần nào |
| `disconnects` | số lần đứt từ lúc app khởi động |
| `url`, `client_id` | broker đang nhắm tới; `client_id` có sau lần nối đầu |

Không truyền tên thì trả mọi hạ tầng **đang bật**: `broker_status()` →
`{"mqtt": {...}, "redis": {...}}`.

Đã tiêm `MqttClient` vào service ([Gửi tin](#gửi-tin)) thì đọc thẳng trên nó —
cũng chỉ đọc RAM:

| Gọi | Trả về |
|---|---|
| `self._mqtt.connected` | `bool` — cùng trạng thái với `broker_status("mqtt")["connected"]` |
| `self._mqtt.enabled` | `bool` — `APP_MQTT__ENABLED` |
| `self._mqtt.stats()` | `{"enabled", "connected", "url", "client_id", "topics", "listeners", "disconnects"}` |

`disconnects` trong `stats()` đếm **mọi** lần thử nối hỏng; `broker_status()`
chỉ đếm lần đang nối mà bị đứt.

- **Vẫn bắt lỗi khi gửi, dù vừa thấy `connected=True`.** Broker có thể đứt đúng
  giữa hai dòng code. Đọc trạng thái để bỏ qua sớm, còn lời gửi thì vẫn cần
  `try`/`except ServiceUnavailableError`, hoặc `fire_and_forget=True`.
- **Gõ đúng tên.** `broker_status("mqqt")` ném `ValueError` kèm danh sách tên
  hợp lệ: `kafka`, `mqtt`, `rabbitmq`, `redis`.

### Kiểm xem handler nối/đứt đã được nhận chưa

Lúc khởi động phải thấy:

```
mqtt.connection_hooks_registered  on_connect=['MqttStatus.online'] on_disconnect=['MqttStatus.offline']
```

Không thấy dòng này nghĩa là class thiếu `@injectable`, hoặc file không nằm dưới
`src/api/`. Thử đứt thật: `docker stop <container broker>` — sẽ thấy
`mqtt.connection_lost` rồi `on_disconnect` chạy; `docker start` lại thì thấy
`mqtt.connected` và `on_connect` với `reconnect=True`.

---

## Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân |
|---|---|
| Client cứ nối vào rồi bị đá ra, lặp vô hạn | hai worker dùng chung `CLIENT_ID` — MQTT chỉ cho một phiên mỗi id |
| Gửi được mà bên nghe không thấy gì | bộ lọc không khớp topic — soi `mqtt_unrouted_total` |
| log `mqtt.payload_invalid` | payload không khớp model pydantic của handler — tin bị bỏ, không thử lại |
| log `mqtt.handler_failed` | handler ném lỗi; MQTT không có DLQ, tin không được phát lại |
| Tin QoS 1 biến mất lúc app tắt | `CLEAN_SESSION=true` (mặc định) — broker không giữ gì; xem `CLEAN_SESSION` ở [Tra cứu](#tra-cứu) |
| log `mqtt.session_not_persistent` lúc boot | `CLEAN_SESSION=false` nhưng `CLIENT_ID` trống — phiên mới mỗi lần khởi động, không giữ được gì |
| HTTP 400 khi gửi | topic gửi chứa `+`/`#` — ký tự đại diện chỉ dành cho bên NGHE |
| log `mqtt.starting_degraded` lúc khởi động | broker chưa lên; vòng nối lại chạy ngầm, backoff tới 30s |
| `on_connect`/`on_disconnect` không bao giờ chạy, không có log `mqtt.connection_hooks_registered` | class thiếu `@injectable`, hoặc file không nằm dưới `src/api/` |
| `RuntimeError: ... phải là (self) hoặc (self, info)` lúc khởi động | handler nối/đứt nhận hơn một tham số ngoài `self` |
| log `mqtt.connection_hook_failed` | handler nối/đứt ném lỗi; handler khác và các lần sau vẫn chạy |
| `broker_status("mqtt")` trả `enabled: False` dù đã bật | gọi trước khi app khởi động xong, hoặc `APP_MQTT__ENABLED` chưa đọc được — soi log `mqtt.disabled` |
| `ValueError: broker_status: không có hạ tầng ...` | gõ nhầm tên — dùng `kafka`, `mqtt`, `rabbitmq`, `redis` |
| `self._mqtt.connected` luôn `False`, `publish` ném `ServiceUnavailableError` dù broker sống | tự dựng `MqttClient(settings)` thay vì tiêm qua `__init__` — xem [Gửi tin](#gửi-tin) |
| Broker chưa lên lúc khởi động mà `on_disconnect` không chạy | đúng thiết kế: chưa từng nối được thì không có "mất kết nối" — xem [Khi nào handler được gọi](#khi-nào-handler-được-gọi) |

---

## Tra cứu

### Cấu hình

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
  `mqtt.session_not_persistent` lúc boot.
- **`CLEAN_SESSION=false` + `CLIENT_ID` cố định**: broker giữ tin QoS≥1 lại
  trong lúc app tắt và giao khi nối lại. Đây là cách duy nhất để không mất tin
  lúc deploy.

### Số đo

| Tên | Ý nghĩa |
|---|---|
| `mqtt_published_total` | tin đã gửi |
| `mqtt_publish_failed_total` | gửi thất bại |
| `mqtt_received_total` | tin nhận được |
| `mqtt_handler_failed_total` | handler xử lý lỗi |
| `mqtt_unrouted_total` | nhận được mà không handler nào khớp (gần như luôn là gõ nhầm bộ lọc) |

---

### Chạy thử bằng Docker

```bash
docker run -d --name mqtt-test -p 1893:1883 eclipse-mosquitto:2 \
  sh -c "printf 'listener 1883\nallow_anonymous true\n' > /m.conf && mosquitto -c /m.conf"
fam install mqtt
TEST_MQTT_URL=mqtt://localhost:1893 fam test    # bật nhóm test cần broker thật
```
