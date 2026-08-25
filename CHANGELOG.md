# Thay đổi

Theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); phiên bản theo
[SemVer](https://semver.org/lang/vi/).

## [Chưa phát hành]

### Thêm

- **Provider cắm được** — chọn bản hiện thực bằng TÊN lúc chạy, thứ mà container
  (tra theo kiểu) không làm được: cổng thanh toán lấy từ cột trong đơn hàng, nhà
  mạng SMS lấy từ cấu hình. API: `@provider`, `ProviderFamily`, `Registry`,
  `register_providers`. Xem `docs/providers.md`.
  Lớp token CHÍNH LÀ sổ đăng ký — không còn `Registry` tách riêng, bớt một
  khái niệm và annotation `payments: PaymentProviders` trở thành sự thật.
- **`fam provider <họ> <tên>`** — sinh khung. Lần đầu dựng cả họ; lần sau đọc
  `capabilities.py` rồi sinh sẵn stub đúng chữ ký các method cần viết.
- Họ khai **năng lực chính** qua tham số generic:
  `class PaymentProviders(ProviderFamily[PaymentGateway], family="payment")`.
  Nhờ đó `require(tên)` chỉ cần một tham số và trả về đúng kiểu đó — IDE gợi ý
  được method, thay vì `Any` như trước. Năng lực tuỳ chọn vẫn khai tường minh:
  `require(tên, HoanTien)` — và overload khiến kiểu trả về khi đó là CHÍNH
  `HoanTien`, nếu không thì IDE vẫn chỉ gợi ý method của năng lực chính.
- **`container.build(cls, key=..., scope=...)`** — dựng một lớp có nối phụ thuộc
  mà KHÔNG đăng ký vào sổ toàn cục. Cần cho provider: sổ toàn cục tra theo tên
  class, trong khi hai họ có quyền cùng có một `OryzaProvider`.

### Thay đổi

- `fam p` giờ **nhập nhằng** giữa `publish` và `provider` nên `fam` hỏi lại. Viết
  tắt mới: `fam pu` cho publish, `fam pr` cho provider.
- `create_app()` gọi `register_providers()` trước khi dựng route. Không có
  `src/providers/` thì bỏ qua, không lỗi.

## [0.2.1] — 2026-08-22

### Thay đổi

- Tác giả và chủ bản quyền: Oryza <developer@oryza.vn> -> quanglinh
  <hackcoquanglinh2000@gmail.com>, ở cả `pyproject.toml` lẫn `LICENSE`.
- README tách làm hai bản song ngữ: `README.md` (tiếng Anh, là bản hiện trên
  PyPI) và `README.vi.md` (tiếng Việt). Hai bản giữ cùng thứ tự mục.
- Metadata PyPI viết lại cho tìm kiếm: summary sang tiếng Anh, keywords từ 9 lên
  39 từ, thêm 7 classifier.

### Tài liệu

- Viết lại `docs/websocket.md` theo hướng làm-theo thay vì tra-cứu: đưa "bốn
  việc client bắt buộc phải làm" lên đầu trang, thêm client tối thiểu 30 dòng
  chạy được ngay, giải thích cơ chế nhịp tim bằng sơ đồ thời gian, và thêm mục
  tra sự cố theo triệu chứng.

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
