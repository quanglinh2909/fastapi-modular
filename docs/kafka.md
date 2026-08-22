# Kafka

Tuỳ chọn. Không cài, không bật thì không ảnh hưởng gì tới phần còn lại.

```bash
pym install kafka     # cài thư viện + ghi APP_KAFKA__* vào .env
```

Hai việc làm được: **gửi tin** (`KafkaBroker.publish`) và **đọc nhật ký**
(`@kafka_subscriber`).

---

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

## Cấu hình

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

Chỉ cần liệt kê **vài** broker trong `BOOTSTRAP_SERVERS` — client tự tìm ra phần
còn lại của cụm.

---

## Gửi tin

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
                      max_retries=2, retry_delay=0.5)
    async def giao_hang(self, don: DonHang, meta: dict) -> None:
        ...
```

```python
@kafka_subscriber(topic, *, group, auto_offset_reset="latest",
                  max_retries=3, retry_delay=1.0, dead_letter=True)
```

| Tham số | Mặc định | Không truyền thì | Đổi khi nào |
|---|---|---|---|
| `group` | *bắt buộc* | — | luôn phải đặt; đây là danh tính con trỏ đọc |
| `auto_offset_reset` | `"latest"` | nhóm mới chỉ đọc tin phát sinh **từ giờ** | `"earliest"` để đọc lại từ đầu nhật ký |
| `max_retries` | `3` | lỗi thì thử lại 3 lần rồi sang `.dlt` | `0` = hỏng là bỏ ngay |
| `retry_delay` | `1.0` | chờ 1 giây giữa các lần | để **nhỏ** — thử lại làm đứng cả phân vùng |
| `dead_letter` | `True` | tin lỗi được sao sang `<topic>.dlt` | `False` = bỏ qua hẳn, không lưu lại |

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
| ném lỗi, hết lượt thử | sao sang `<topic>.dlt`, commit, đi tiếp |
| ném `PermanentMessageError` | sang `.dlt` **ngay**, không thử lại |
| payload sai khuôn model | như `PermanentMessageError` |

Đo được với `max_retries=2`:

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

## Ví dụ chạy được

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

## Số đo

| Tên | Ý nghĩa |
|---|---|
| `kafka_published_total` | tin đã gửi |
| `kafka_publish_failed_total` | gửi thất bại |
| `kafka_consumed_total` | tin xử lý xong |
| `kafka_consume_failed_total` | handler lỗi (đếm cả lần thử lại) |
| `kafka_dead_lettered_total` | tin bị đẩy sang `.dlt` |

---

## Chạy thử bằng Docker

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
pym install kafka
TEST_KAFKA_SERVERS=localhost:9094 pym test    # bật nhóm test cần cụm thật
```
