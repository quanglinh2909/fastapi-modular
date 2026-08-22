# Thay đổi

Theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); phiên bản theo
[SemVer](https://semver.org/lang/vi/).

## [Chưa phát hành]

## [0.2.0] — 2026-08-22

**Đổi tên toàn bộ.** Tên `pymodular` bị PyPI từ chối vì trùng với project
`py-modular` đã có, nên cả dự án đổi sang `fastapi-modular` cho thống nhất từ
PyPI, GitHub, thư mục nguồn cho tới tên lệnh.

### Phá vỡ

- Gói trên PyPI: `pymodular` -> **`fastapi-modular`**.
- Tên import: `import pymodular` -> **`import fastapi_modular`** (gạch dưới,
  vì gạch ngang không hợp lệ trong tên module Python).
- Lệnh CLI: `pymodular` / `pym` -> **`fastapi-modular` / `fam`**.
- Thư mục nguồn `pymodular/` -> `fastapi_modular/`.
- `APP_NAME` mặc định: `pymodular` -> `fastapi-modular`.
- `APP_KAFKA__CLIENT_ID` mặc định: `pymodular` -> `fastapi-modular`.

Nâng cấp từ 0.1.0: gỡ gói cũ (`pip uninstall pymodular`), cài
`pip install fastapi-modular`, rồi đổi mọi `pymodular` trong import thành
`fastapi_modular` và mọi lệnh `pym` thành `fam`.

### Sửa

- `fam lint` không tham số trỏ vào thư mục `app` không tồn tại nên lỗi ngay;
  mặc định đổi thành `src`, đúng thứ `fam init` sinh ra.
- `fam migrate-create` trong docs/migrations.md không phải lệnh có thật; lệnh
  đúng là `fam migrate create`.
- Link trong README đổi sang URL tuyệt đối: README là trang hiển thị trên PyPI,
  ở đó link tương đối `docs/...` phân giải sai và trả 404.
- Job đóng gói trong CI không bao giờ xanh được: import sai đường dẫn
  (`src.api.main`) và liệt kê route bằng `r.path`, hỏng từ FastAPI 0.141.

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
- **CLI** `fastapi-modular`, gõ tắt là `fam`: `init` (dựng dự án ngay trong thư mục
  hiện tại) · `new` · `dev` / `run` · `module` · `env` · `info` · `migrate` ·
  `test` / `lint`. `init` không bao giờ ghi đè file đã có.
- **Tài liệu tiếng Việt** trong `docs/`, viết theo lối tra cứu: mỗi hàm nói rõ
  truyền gì, không truyền thì mặc định là gì.
