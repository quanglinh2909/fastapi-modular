# Thay đổi

Theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); phiên bản theo
[SemVer](https://semver.org/lang/vi/).

## [Chưa phát hành]

## [0.1.0] — 2026-08-21

Bản đầu tiên. Cần **Python 3.10 trở lên**.

### Có gì

- **Kiến trúc module kiểu NestJS**: DI container (`@injectable`, `Lazy[...]`,
  `Scope.REQUEST`), controller dạng class (`@controller`, `@get`/`@post`/...),
  tự quét module — thêm thư mục là có route, không phải đăng ký ở đâu cả.
- **Repository dùng chung cho 4 backend**: memory, SQLite, PostgreSQL, MongoDB.
  Đổi backend không phải sửa service. Kèm circuit breaker và hạn thời gian.
- **WebSocket**: `@gateway` / `@subscribe`, phòng, gửi thẳng tới một người,
  nhịp tim, giới hạn tần suất, adapter Redis để phát tin xuyên worker.
- **Bốn lớp hạ tầng tuỳ chọn, cùng một khuôn**: RabbitMQ, Redis, MQTT, Kafka.
  Mặc định TẮT; thư viện chỉ được import khi bật. Tất cả tự nối lại.
- **CLI** `pymodular`, gõ tắt là `pym`: `init` (dựng dự án ngay trong thư mục
  hiện tại) · `new` · `dev` / `run` · `module` · `env` · `info` · `migrate` ·
  `test` / `lint`. `init` không bao giờ ghi đè file đã có.
- **Tài liệu tiếng Việt** trong `docs/`, viết theo lối tra cứu: mỗi hàm nói rõ
  truyền gì, không truyền thì mặc định là gì.
