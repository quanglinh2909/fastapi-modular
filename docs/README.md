# Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [database.md](database.md) | Cài và dùng 4 backend: memory, SQLite, PostgreSQL, MongoDB |
| [config.md](config.md) | Settings: thứ tự ưu tiên, **thêm biến của riêng bạn**, .env |
| [architecture.md](architecture.md) | Cấu trúc module, DI container, đối chiếu với NestJS |
| [migrations.md](migrations.md) | Alembic: sinh, chạy, lùi migration |
| [websocket.md](websocket.md) | Gateway WebSocket: API, phòng, gửi thẳng, Postman, Next.js |
| [rabbitmq.md](rabbitmq.md) | RabbitMQ (tuỳ chọn): `publish`, `@rabbitmq_subscriber`, tham số và mặc định |
| [redis.md](redis.md) | Redis (tuỳ chọn): cache, đếm nguyên tử, pub/sub |
| [mqtt.md](mqtt.md) | MQTT (tuỳ chọn): thiết bị IoT, QoS, retain, luật khớp topic |
| [kafka.md](kafka.md) | Kafka (tuỳ chọn): nhật ký đọc lại được, nhóm consumer, `.dlt` |
| [operations.md](operations.md) | Guard, circuit breaker, metrics, trace |

## Bắt đầu nhanh

```bash
pip install fastapi-modular

fam init                     # đổ file vào THƯ MỤC HIỆN TẠI
fam dev                      # mặc định backend memory

fam install sqlite    # dữ liệu sống qua restart
```

Một chương trình, hai tên: `fastapi-modular` (đầy đủ) và `fam` (gõ tắt). `fam --help`
cho danh sách lệnh; bảng đầy đủ ở [README gốc](../README.vi.md#lệnh).

Realtime chạy sẵn, không phải cài gì thêm:
`ws://localhost:8000/ws/chat?client_id=an` — xem [websocket.md](websocket.md).

Thành phần tuỳ chọn, cài khi cần, không cài thì không ảnh hưởng gì:

```bash
fam install rabbitmq  # hàng đợi bền, thử lại + DLQ
fam install redis     # cache, đếm, pub/sub
fam install mqtt      # thiết bị IoT
fam install kafka     # nhật ký đọc lại được
fam install ws-redis  # WebSocket xuyên worker
```

Chọn cái nào:

| Cần gì | Dùng gì |
|---|---|
| tin không được mất, chia việc cho worker | [RabbitMQ](rabbitmq.md) |
| nhanh, mọi worker nhận một bản sao, mất vài tin cũng được | [Redis](redis.md) |
| thiết bị, mạng chập chờn, kết nối lâu | [MQTT](mqtt.md) |
| đọc lại được lịch sử, nhiều nhóm đọc độc lập | [Kafka](kafka.md) |

Bốn lớp cùng một khuôn, tên đặt theo cùng một quy tắc `<hạ tầng>_subscriber`:

| | Gửi | Nhận | Runner | Bật bằng |
|---|---|---|---|---|
| RabbitMQ | `RabbitBroker.publish` | `@rabbitmq_subscriber` | `RabbitmqRunner` | `APP_RABBITMQ__ENABLED` |
| Redis | `RedisClient.publish` | `@redis_subscriber` | `RedisRunner` | `APP_REDIS__ENABLED` |
| MQTT | `MqttClient.publish` | `@mqtt_subscriber` | `MqttRunner` | `APP_MQTT__ENABLED` |
| Kafka | `KafkaBroker.publish` | `@kafka_subscriber` | `KafkaRunner` | `APP_KAFKA__ENABLED` |

Handler ở cả bốn đều nhận `(self, payload)` hoặc `(self, payload, meta)`, đều
validate bằng pydantic nếu `payload` có kiểu là model, và đều chạy trong một
request scope riêng.

Mỗi lệnh `fam env` ghi biến vào `.env` kèm giải thích, cho biết biến đó
**tuỳ chọn hay bắt buộc** và **mặc định là gì** nếu xoá dòng đi.

`fam --help` để xem toàn bộ lệnh.
