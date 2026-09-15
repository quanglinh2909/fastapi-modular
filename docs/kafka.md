# Kafka

Tuỳ chọn. Không cài, không bật thì không ảnh hưởng gì tới phần còn lại.

```bash
fam install kafka     # cài thư viện + ghi APP_KAFKA__* vào .env
```

Hai việc làm được: **gửi tin** (`KafkaBroker.publish`) và **đọc nhật ký**
(`@kafka_subscriber`).

---


> Cần gửi rồi **chờ trả lời**? Có `kafka.send()` và `@kafka_responder`, nhưng
> đọc [docs/rpc.md](rpc.md) trước: Kafka là nhật ký để đọc lại, không phải
> đường gọi hàm.

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Chọn Kafka hay RabbitMQ đây?" | [Kafka khác RabbitMQ ở đâu](#kafka-khác-rabbitmq-ở-đâu) |
| "Ghi một sự kiện vào nhật ký" | [Gửi tin](#gửi-tin) |
| "Xử lý sự kiện, mỗi nhóm một con trỏ riêng" | [Đọc nhật ký](#đọc-nhật-ký) |
| "**Hai hệ khác nhau cùng đọc** một dòng tin" | [Nhiều nhóm, một dòng tin](#nhiều-nhóm-một-dòng-tin) |
| "Tin hỏng thì thử lại / đẩy đi đâu" | [Xử lý lỗi](#xử-lý-lỗi) |
| "Gửi rồi **chờ trả lời**" | [rpc.md](rpc.md) — nhưng đọc kỹ, Kafka không hợp việc đó |
| "Cụm chết thì app có chết theo không" | [Khi cụm chưa lên](#khi-cụm-chưa-lên) |
| "Cụm **đứt thì cảnh báo**, nối lại thì ghi đứt bao lâu" | [Biết khi nào mất kết nối](#biết-khi-nào-mất-kết-nối) |
| "Hỏi **cụm có đang nối không** — gọi liên tục cũng nhẹ" | [Hỏi trạng thái lúc này](#hỏi-trạng-thái-lúc-này) |
| "Bảng biến, số đo" | [Tra cứu](#tra-cứu) |

## Kafka khác RabbitMQ ở đâu

Đây là thứ phải nắm trước khi viết dòng code nào, vì nó quyết định chọn cái nào:

| | RabbitMQ | Kafka |
|---|---|---|
| Tin sau khi xử lý | **biến mất** khỏi hàng đợi | **nằm lại** tới khi hết hạn giữ |
| Ai đọc được | consumer lấy được thì thôi | mọi nhóm, mỗi nhóm một con trỏ riêng |
| Thêm consumer mới | chỉ nhận tin từ giờ trở đi | **đọc lại được cả lịch sử** |
| Thử lại một tin | hàng đợi `.retry` riêng, không cản ai | **làm đứng cả phân vùng** |
| Xoá một tin lỗi | được (`.dlq`) | không — chỉ sao sang `.dlt`, bản gốc vẫn nằm đó |
| Song song tối đa | bao nhiêu worker cũng được | **bằng số phân vùng** |
| Thứ tự | không bảo đảm | bảo đảm **trong một phân vùng** |

Chọn Kafka khi cần đọc lại lịch sử, cần nhiều hệ thống độc lập cùng ăn một dòng
sự kiện, hoặc cần thứ tự theo từng thực thể. Chọn [RabbitMQ](rabbitmq.md) khi
cần chia việc và xử lý đúng một lần.

---

## Gửi tin

Tiêm `KafkaBroker` qua `__init__` như mọi provider, rồi gọi `publish`.
`fam module orders --kafka` sinh sẵn khung này, kèm handler đọc topic và
`on_connect`/`on_disconnect` — module chưa có thì tạo, có rồi thì thêm vào.

```python
# src/api/don_hang/order_events.py
from fastapi_modular import injectable
from fastapi_modular.infrastructure.kafka import KafkaBroker


@injectable
class OrderEvents:
    def __init__(self, kafka: KafkaBroker) -> None:
        self._kafka = kafka

    async def paid(self, order_id: str, amount: int) -> bool:
        return await self._kafka.publish(
            "don-hang", {"order_id": order_id, "amount": amount}, key=order_id
        )

    def is_online(self) -> bool:
        return self._kafka.connected      # đọc RAM, gọi liên tục cũng được
```

**Tiêm, đừng tự dựng.** `KafkaBroker(settings)` tự tạo là một broker chưa bao
giờ được mở: `connected` luôn `False`, `publish` luôn ném
`ServiceUnavailableError`. Container đưa đúng bản mà app đã mở lúc khởi động.

```python
await kafka.publish(topic, payload=None, *, key=None, headers=None,
                    timeout=None, fire_and_forget=False) -> bool
```

| Tham số | Không truyền thì | Truyền khi nào |
|---|---|---|
| `topic` | *bắt buộc* | — |
| `payload` | `None` | `dict`/`list` mã hoá JSON; `bytes` gửi nguyên |
| `key` | `None` → tin **rải đều**, thứ tự giữa chúng không bảo đảm | id thực thể (`key=order_id`) khi thứ tự có ý nghĩa |
| `headers` | không có | metadata dạng chuỗi đi kèm, không đụng vào body |
| `timeout` | lấy `REQUEST_TIMEOUT_SECONDS` (20s) | lần gửi đặc biệt nặng |
| `fire_and_forget` | `False` → chưa nối được thì ném lỗi | `True` khi thà mất tin còn hơn hỏng request |

**`key` là tham số quan trọng nhất ở đây.** Mọi tin cùng key rơi vào cùng một
phân vùng, nên chúng được xử lý **đúng thứ tự**. `{"trạng thái": "đã trả tiền"}`
và `{"trạng thái": "đã huỷ"}` của cùng một đơn mà rơi vào hai phân vùng thì hai
worker có thể xử lý ngược thứ tự.

`publish()` **chờ cụm xác nhận** theo mức `ACKS` rồi mới trả về.

---

## Đọc nhật ký

```python
@injectable
class KhoVanConsumer:
    @kafka_subscriber("don-hang", group="kho-van", auto_offset_reset="earliest",
                      max_retries=2, retry_delay=0.5, dead_letter=True)
    async def giao_hang(self, don: DonHang, meta: dict) -> None:
        ...
```

```python
@kafka_subscriber(topic, *, group, auto_offset_reset="latest",
                  max_retries=0, retry_delay=1.0, dead_letter=False)
```

| Tham số | Mặc định | Không truyền thì | Đổi khi nào |
|---|---|---|---|
| `group` | *bắt buộc* | — | luôn phải đặt; đây là danh tính con trỏ đọc |
| `auto_offset_reset` | `"latest"` | nhóm mới chỉ đọc tin phát sinh **từ giờ** | `"earliest"` để đọc lại từ đầu nhật ký |
| `max_retries` | `0` | **hỏng là bỏ ngay**, không thử lại | tăng lên khi lỗi hay gặp là chập chờn mạng — nhớ nó làm đứng cả phân vùng |
| `retry_delay` | `1.0` | chờ 1 giây giữa các lần | chỉ có tác dụng khi `max_retries > 0`. Để **nhỏ** — thử lại làm đứng cả phân vùng |
| `dead_letter` | `False` | tin lỗi **bị bỏ hẳn**, chỉ còn log `kafka.message_dropped` | `True` để sao sang `<topic>.dlt` — bật khi tin đáng tiền |

> **Mặc định là "nhanh và quên".** `max_retries=0, dead_letter=False` nghĩa là
> tin nào handler ném lỗi thì **mất luôn**, con trỏ đi tiếp và chỉ để lại một
> dòng log `kafka.message_dropped`. Đó là lựa chọn đúng cho dòng tin đo đạc,
> vị trí, nhiệt độ — nơi thông lượng quan trọng hơn từng tin lẻ.
>
> Tin **đáng tiền** thì phải tự bật:
>
> ```python
> @kafka_subscriber("don-hang", group="kho-van", max_retries=3, dead_letter=True)
> async def xu_ly(self, don: DonHang) -> None: ...
> ```
>
> Nhớ cái giá: mỗi lượt thử lại **chặn cả phân vùng** trong `retry_delay` giây.

`group` cố ý **không tự sinh**, cùng lý do với `queue` bên RabbitMQ: tên tự sinh
sẽ đổi sau mỗi lần deploy, và mỗi lần deploy sẽ đọc lại từ đầu (hoặc bỏ qua sạch
phần cũ, tuỳ `auto_offset_reset`).

`auto_offset_reset` **chỉ có tác dụng với nhóm CHƯA có con trỏ**. Nhóm đã chạy
rồi thì cụm nhớ vị trí, tham số này bị bỏ qua — đổi nó không "tua lại" được.

| Chữ ký handler | Nhận được |
|---|---|
| `async def f(self, payload: MyModel)` | `payload` đã validate bằng pydantic |
| `async def f(self, payload: dict)` | dữ liệu thô |
| `async def f(self, payload, meta: dict)` | thêm `meta` |

`meta` = `{"topic", "partition", "offset", "key", "timestamp", "attempt"}`.

### Nhiều nhóm, một dòng tin

```python
@kafka_subscriber("don-hang", group="kho-van", ...)     # lo giao hàng
@kafka_subscriber("don-hang", group="ke-toan", ...)     # lo ghi sổ
```

Khác `group` → **mỗi bên nhận đủ một bản sao** của mọi tin, con trỏ độc lập. Đo
được trên ví dụ:

```
{"nhom": "kho-van", "ma_don": "D1", "partition": 0, "offset": 0}
{"nhom": "ke-toan", "ma_don": "D1", "partition": 0, "offset": 0}
```

Cùng `group` nhưng nhiều worker → **chia nhau phân vùng**, mỗi tin đúng một
worker. Số worker có việc **không vượt quá số phân vùng** của topic: topic một
phân vùng thì chạy mười worker cũng chỉ một worker chạy.

### Xử lý lỗi

| Handler làm gì | Kết quả |
|---|---|
| trả về bình thường | commit offset, đi tiếp |
| ném lỗi, còn lượt thử | **chờ `retry_delay` rồi chạy lại ngay tại chỗ** |
| ném lỗi, hết lượt thử | `dead_letter=True` -> sao sang `<topic>.dlt`; mặc định `False` -> **bỏ hẳn**, chỉ còn log. Cả hai đều commit và đi tiếp |
| ném `PermanentMessageError` | xử như hết lượt **ngay**, không thử lại |
| payload sai khuôn model | như `PermanentMessageError` |

Đo được với `max_retries=2, dead_letter=True`:

```
kieu=ok               -> lan_thu=1                    (xong)
kieu=hong-vinh-vien   -> lan_thu=1        -> .dlt     (không thử lại)
kieu=hong-tam-thoi    -> lan_thu=1,2,3    -> .dlt     (1 lần đầu + 2 lần thử lại)
```

Tin trong `<topic>.dlt` giữ nguyên body và key, kèm header truy vết:

```
x-original-topic:don-hang, x-original-partition:0, x-original-offset:2,
x-error:RuntimeError: kho chưa phản hồi (lần 3)
{"ma_don": "D3", "tien": 100.0, "kieu": "hong-tam-thoi"}
```

**Thử lại chạy ngay trong vòng đọc, nên nó làm đứng cả phân vùng đó.** Kafka
không cho ack lẻ từng tin — con trỏ chỉ tiến lên — nên thử lại tin thứ 5 nghĩa
là tin 6, 7, 8... phải chờ. Đó là cái giá của việc giữ đúng thứ tự, và cũng là lý
do `retry_delay` ở đây nên nhỏ hơn nhiều so với bên RabbitMQ.

Offset được **commit tay sau khi handler xong** (`enable_auto_commit=False`). Tự
commit theo đồng hồ sẽ commit cả tin chưa xử lý xong — tiến trình chết đúng lúc
đó là mất tin. Ngữ nghĩa vì vậy là **ít nhất một lần**: handler phải chịu được
tin trùng.

---

## Kiểm xem nó chạy chưa

`src/api/kafka_test/` có hai nhóm consumer trên cùng một topic:

```bash
curl -X POST localhost:8002/api/kafka-test/gui -H 'Content-Type: application/json' \
     -d '{"ma_don":"D1","tien":100,"kieu":"ok"}'
curl localhost:8002/api/kafka-test/da-nhan
```

`kieu` nhận `ok` | `hong-tam-thoi` | `hong-vinh-vien` để xem ba đường đi ở trên.

Đọc topic chết:

```bash
docker exec kafka-test /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic don-hang.dlt \
  --from-beginning --property print.headers=true
```

---

## Khi cụm chưa lên

App **vẫn khởi động**: log `kafka.starting_degraded`, vòng nối lại chạy ngầm với
backoff `1s → 2s → ... → 30s`, consumer được bật ngay khi producer nối được.
Consumer chết vì bất cứ lý do gì cũng được dựng lại — mỗi spec một task riêng,
một topic khai sai không kéo các topic khác chết theo.

`/api/health/ready` cho biết trạng thái nhưng **không** vì Kafka rớt mà trả 503.

---

## Biết khi nào mất kết nối

"Cụm Kafka đứt thì ghi cảnh báo", "nối lại thì ghi đứt bao lâu" — viết thế này:

```python
# src/api/don_hang/kafka_status.py
from fastapi_modular import get_logger, injectable
from fastapi_modular.infrastructure.kafka import kafka_on_connect, kafka_on_disconnect

log = get_logger(__name__)


@injectable
class KafkaStatus:
    @kafka_on_connect
    async def online(self, info: dict) -> None:
        if info["reconnect"]:
            log.info("kafka_back", downtime=info["downtime_seconds"])

    @kafka_on_disconnect
    async def offline(self, info: dict) -> None:
        log.warning("kafka_down", error=info["error"])
```

Không cần `info` thì bỏ đi: `async def online(self) -> None:` cũng chạy.

**Khi Kafka bật, khung hỏi metadata của cụm 5 giây một lần.** aiokafka tự tìm
lại broker mà không báo gì, nên không hỏi thì không bao giờ biết mình đứt. Chỉnh
nhịp bằng `APP_KAFKA__HEALTH_CHECK_SECONDS` ([Tra cứu](#tra-cứu)).

### Khi nào handler được gọi

| Chuyện xảy ra | Handler chạy |
|---|---|
| App khởi động, nối được cụm | `on_connect`, `info["reconnect"]` là `False` |
| Đang chạy thì đứt | `on_disconnect` — **một lần**, ở lần hỏi metadata kế tiếp |
| Nối lại được | `on_connect`, `info["reconnect"]` là `True` |
| Cụm chưa lên lúc khởi động | **không gọi gì**; lúc cụm lên thì `on_connect` với `reconnect=False` |
| Tắt app | **không** gọi `on_disconnect` |

### `info` có gì

| Khoá | Có ở | Ý nghĩa |
|---|---|---|
| `servers` | cả hai | `APP_KAFKA__BOOTSTRAP_SERVERS` |
| `reconnect` | `on_connect` | `False` ở lần nối đầu, `True` ở mọi lần sau |
| `downtime_seconds` | `on_connect` | đứt bao nhiêu giây; `None` ở lần nối đầu |
| `error` | `on_disconnect` | lỗi làm đứt, ví dụ `"KafkaError: ... Unable to get cluster metadata ..."` hoặc `"TimeoutError"` |

- **Đừng coi đây là trạng thái của từng consumer.** Sự kiện nói về kết nối tới
  CỤM. Một consumer chết riêng lẻ thì xem log `kafka.consumer_lost`, khung tự
  dựng lại nó.
- **Broker treo thì báo chậm hơn broker tắt.** Broker tắt là lần hỏi kế tiếp
  hỏng ngay; broker treo (không từ chối mà cũng không trả lời) thì mỗi lần hỏi
  phải chờ hết `APP_KAFKA__CONNECT_TIMEOUT_SECONDS` (mặc định 10s). Cần báo
  nhanh hơn thì hạ con số đó.
- **Đừng dùng handler làm chốt trước khi gửi tin.** Handler chạy trong một task
  riêng, SAU khi trạng thái đã đổi — và có thể trễ tới một nhịp hỏi. Cần biết
  ngay lúc gửi thì dùng `fire_and_forget=True`.
- **Đặt timeout cho việc chậm trong handler.** Các handler nối/đứt chạy lần
  lượt: một `on_connect` treo thì `on_disconnect` phía sau phải chờ.

### Hỏi trạng thái lúc này

Không cần khai handler — hỏi thẳng, ở bất cứ đâu:

```python
from fastapi_modular import broker_status

status = broker_status("kafka")
if not status["connected"]:
    print("Kafka đang đứt từ", status["since"], "vì", status["last_error"])
```

**Gọi liên tục cũng được** (mỗi request, mỗi vòng lặp): hàm chỉ đọc biến trong
RAM, không gửi gì qua mạng, không cần `await`. Việc hỏi metadata chạy ngầm,
không nằm trong lời gọi này.

| Khoá | Ý nghĩa |
|---|---|
| `enabled` | `False` khi `APP_KAFKA__ENABLED=false` — lúc đó các khoá khác là mặc định |
| `connected` | đang nối hay không |
| `since` | `datetime` (UTC) của lần đổi trạng thái gần nhất; `None` nếu chưa từng nối |
| `last_error` | lỗi của lần đứt gần nhất; `None` nếu chưa đứt lần nào |
| `disconnects` | số lần đứt từ lúc app khởi động |
| `servers` | `APP_KAFKA__BOOTSTRAP_SERVERS` |

Không truyền tên thì trả mọi hạ tầng **đang bật**: `broker_status()` →
`{"kafka": {...}, "redis": {...}}`.

Đã tiêm `KafkaBroker` vào service ([Gửi tin](#gửi-tin)) thì đọc thẳng trên nó —
cũng chỉ đọc RAM:

| Gọi | Trả về |
|---|---|
| `self._kafka.connected` | `bool` — cùng trạng thái với `broker_status("kafka")["connected"]` |
| `self._kafka.enabled` | `bool` — `APP_KAFKA__ENABLED` |
| `self._kafka.stats()` | `{"enabled", "connected", "servers", "acks"}` |

- **Chấp nhận trễ tối đa một nhịp hỏi.** Cụm vừa chết thì `connected` còn `True`
  thêm tới `HEALTH_CHECK_SECONDS` giây — cộng `CONNECT_TIMEOUT_SECONDS` nếu
  broker treo. Đọc trạng thái để bỏ qua sớm, còn lời gửi thì vẫn cần bắt lỗi.
- **Đừng đặt `HEALTH_CHECK_SECONDS=0` nếu dùng hàm này.** Tắt vòng hỏi là
  `connected` sẽ `True` mãi sau lần nối đầu — không còn cách nào biết cụm đứt.
- **Gõ đúng tên.** `broker_status("kafak")` ném `ValueError` kèm danh sách tên
  hợp lệ: `kafka`, `mqtt`, `rabbitmq`, `redis`.

### Kiểm xem handler nối/đứt đã được nhận chưa

Lúc khởi động phải thấy:

```
kafka.connection_hooks_registered  on_connect=['KafkaStatus.online'] on_disconnect=['KafkaStatus.offline']
```

Không thấy dòng này nghĩa là class thiếu `@injectable`, hoặc file không nằm dưới
`src/api/`. Thử đứt thật: `docker stop <container kafka>` — trong vòng một nhịp
sẽ thấy `kafka.connection_lost` rồi `on_disconnect` chạy; `docker start` lại thì
thấy `kafka.reconnected` và `on_connect` với `reconnect=True`.

---

## Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân |
|---|---|
| Consumer nhận chậm hẳn, tin dồn ứ một phân vùng | một tin đang thử lại — thử lại chạy TRONG vòng đọc, chặn cả phân vùng; giảm `retry_delay` hoặc `max_retries` |
| Tin lỗi biến mất không dấu vết | `dead_letter=False` (mặc định) — hết lượt thử là bỏ hẳn, chỉ còn log; bật `dead_letter=True` |
| Xử lý một tin HAI lần sau khi tiến trình chết | ngữ nghĩa ít-nhất-một-lần: offset commit SAU khi handler xong — handler phải chịu được tin trùng |
| Thêm worker mà không nhanh lên | song song tối đa = số phân vùng của topic, thêm worker quá số đó là thừa |
| Hai service "tranh nhau" tin | cùng `group_id` — muốn mỗi bên một bản sao thì mỗi bên một nhóm riêng |
| log `kafka.starting_degraded` lúc khởi động | cụm chưa lên; vòng nối lại chạy ngầm, consumer bật ngay khi nối được |
| Tin sai khuôn model đi thẳng `.dlt` | payload không khớp pydantic = lỗi vĩnh viễn, thử lại cũng vô ích |
| `on_connect`/`on_disconnect` không bao giờ chạy, không có log `kafka.connection_hooks_registered` | class thiếu `@injectable`, hoặc file không nằm dưới `src/api/` |
| Cụm đã tắt mà vài giây sau `on_disconnect` mới chạy | chờ lần hỏi metadata kế tiếp — nhịp là `HEALTH_CHECK_SECONDS` (mặc định 5s) |
| Broker treo mà hơn chục giây sau `on_disconnect` mới chạy | mỗi lần hỏi chờ hết `CONNECT_TIMEOUT_SECONDS` (mặc định 10s) — hạ nó xuống |
| `on_disconnect` không bao giờ chạy dù cụm tắt hẳn | `HEALTH_CHECK_SECONDS=0` tắt vòng hỏi metadata |
| log `kafka.connection_hook_failed` | handler nối/đứt ném lỗi; handler khác và các lần sau vẫn chạy |
| `broker_status("kafka")["connected"]` vẫn `True` vài giây sau khi cụm tắt | chờ lần hỏi metadata kế tiếp — nhịp là `HEALTH_CHECK_SECONDS` |
| `broker_status("kafka")["connected"]` `True` mãi dù cụm đã tắt | `HEALTH_CHECK_SECONDS=0` tắt vòng hỏi metadata |
| `broker_status("kafka")` trả `enabled: False` dù đã bật | gọi trước khi app khởi động xong, hoặc `APP_KAFKA__ENABLED` chưa đọc được — soi log `kafka.disabled` |
| `ValueError: broker_status: không có hạ tầng ...` | gõ nhầm tên — dùng `kafka`, `mqtt`, `rabbitmq`, `redis` |
| `self._kafka.connected` luôn `False`, `publish` ném `ServiceUnavailableError` dù cụm sống | tự dựng `KafkaBroker(settings)` thay vì tiêm qua `__init__` — xem [Gửi tin](#gửi-tin) |

---

## Tra cứu

### Cấu hình

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_KAFKA__ENABLED` | không | `false` | bật/tắt toàn bộ lớp này |
| `APP_KAFKA__BOOTSTRAP_SERVERS` | **có** | `localhost:9092` | `host:port` ngăn bằng dấu phẩy |
| `APP_KAFKA__CLIENT_ID` | không | `fastapi-modular` | tên hiện trong log/số đo của cụm |
| `APP_KAFKA__ACKS` | không | `all` | `all` an toàn nhất \| `1` chỉ leader \| `0` bắn đi rồi thôi |
| `APP_KAFKA__REQUEST_TIMEOUT_SECONDS` | không | `20.0` | trần cho một lần gửi/nhận |
| `APP_KAFKA__CONNECT_TIMEOUT_SECONDS` | không | `10.0` | chờ lần nối đầu tiên |
| `APP_KAFKA__RECONNECT_DELAY_SECONDS` | không | `1.0` | chờ trước lần nối lại đầu tiên |
| `APP_KAFKA__MAX_RECONNECT_DELAY_SECONDS` | không | `30.0` | trần thời gian chờ |
| `APP_KAFKA__HEALTH_CHECK_SECONDS` | không | `5.0` | nhịp hỏi metadata để biết [đứt/nối lại](#biết-khi-nào-mất-kết-nối); `0` = tắt (không còn cách nào biết cụm đứt) |

Chỉ đọc nếu bạn đang cân nhắc chỉnh `HEALTH_CHECK_SECONDS`: đo trên máy dev, một
broker, `HEALTH_CHECK_SECONDS=1` và `CONNECT_TIMEOUT_SECONDS=3`:

| Làm đứt bằng | `on_disconnect` sau | Khôi phục bằng | `on_connect` sau |
|---|---|---|---|
| `docker stop` | 1,0 s | `docker start` | 2,2 s (gồm lúc broker khởi động) |
| `docker pause` (broker treo) | 4,0 s — một nhịp + trọn `CONNECT_TIMEOUT_SECONDS` | `docker unpause` | 1,0 s |

Chỉ cần liệt kê **vài** broker trong `BOOTSTRAP_SERVERS` — client tự tìm ra phần
còn lại của cụm.

### Số đo

| Tên | Ý nghĩa |
|---|---|
| `kafka_published_total` | tin đã gửi |
| `kafka_publish_failed_total` | gửi thất bại |
| `kafka_consumed_total` | tin xử lý xong |
| `kafka_consume_failed_total` | handler lỗi (đếm cả lần thử lại) |
| `kafka_dead_lettered_total` | tin bị đẩy sang `.dlt` |

---

### Chạy thử bằng Docker

```bash
docker run -d --name kafka-test -p 9094:9094 \
  -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093,EXTERNAL://:9094 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092,EXTERNAL://localhost:9094 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,EXTERNAL:PLAINTEXT \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
  -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \
  apache/kafka:3.9.0
fam install kafka
TEST_KAFKA_SERVERS=localhost:9094 fam test    # bật nhóm test cần cụm thật
```
