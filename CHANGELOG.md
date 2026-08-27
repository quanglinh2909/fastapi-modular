# Thay đổi

Theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); phiên bản theo
[SemVer](https://semver.org/lang/vi/).

## [Chưa phát hành]

### Thêm

- **`repo.update(where, changes)`** — sửa thẳng dưới database, không phải đọc
  bản ghi về trước. Thay vòng ba bước `get` -> sửa -> `save` (hai lượt đi
  database) bằng một câu lệnh:

  ```python
  await cameras.update("cam-01", status="offline")            # theo id
  await cameras.update({"zone": "Tầng 1"}, status="offline")  # theo cột khác, NHIỀU dòng
  ```

  `where` nhận id (chuỗi) hoặc dict điều kiện so bằng trên bất kỳ trường nào;
  giá trị truyền bằng dict hay kwargs đều được. Trả về **số dòng khớp**.
  Thứ tự tham số lấy đúng của TypeORM (`repo.update(criteria, partialEntity)`).

  `updated_at` tự đóng dấu như `save()`. Ràng buộc vẫn được áp trên cả ba
  backend: khoá ngoại trỏ tới cha không tồn tại, hay làm trùng cột `unique`,
  đều bị từ chối.

  Ba thứ bị chặn có chủ đích: đổi `id` (khoá ngoại của bảng khác đang trỏ vào),
  `where` rỗng (gần như luôn là biến rỗng do lỗi lập trình — cố ý sửa cả bảng
  thì nói rõ bằng `match=lambda _: True`), và cột không có thật (gõ sai mà im
  lặng bỏ qua thì câu lệnh báo "đã sửa N dòng" nhưng không sửa gì).

  Trên MongoDB đếm bằng `matched_count` chứ không phải `modified_count`: ghi
  đúng giá trị đang có thì Mongo coi là không sửa gì và trả 0, trong khi SQL vẫn
  đếm dòng đã khớp. Đo trên Mongo thật để chắc: `matched=1, modified=0`.

## [0.3.1] — 2026-08-27

### Sửa

- **Worker ghi database không được commit.** `contextvars` được sao chép khi tạo
  Task/Thread, nên `@worker` sinh ra từ trong một HTTP request — hoặc từ
  `@interval`/`@job`, vốn cũng mở request scope — thừa hưởng đúng store của
  request đó. Mà `SqlUnitOfWork` là provider request-scoped: nó mở transaction
  rồi chỉ commit ở `on_request_end`. Worker sống lâu hơn request nên transaction
  ấy không bao giờ được commit.

  Kiểu hỏng này im lặng đến khó chịu:

  ```python
  print("Deleted:", await repo.delete(row.id))   # -> True
  ```

  `True` là đúng — DELETE khớp một dòng, và câu SELECT ngay sau cũng thấy dữ
  liệu mới vì cùng một connection. Chỉ có điều trên đĩa không đổi gì, và tắt app
  là mất sạch.

  Nay worker được cắt khỏi request scope thừa hưởng (`detach_request_scope`),
  nên mỗi thao tác tự commit như docs vẫn mô tả. Gộp nhiều lệnh ghi thì bọc
  `async with db.transaction():` — vẫn dùng được trong `ctx.run`.

  Hệ quả có thể thấy: `container.resolve(<provider Scope.REQUEST>)` trong worker
  giờ báo lỗi thay vì trả về một instance mồ côi. Đó là cố ý.

## [0.3.0] — 2026-08-27

Bản này thêm **query builder** (JOIN, khoá ngoại, transaction, dữ liệu lồng
nhau), **việc chạy nền** (`@worker`, `@interval`, `@cron`, `@job`, `@on_event`),
**RPC gửi-rồi-chờ-trả-lời** cho cả bốn hạ tầng, và **provider cắm được**.

Nâng cấp từ 0.2.x cần sửa ba chỗ, xem mục "Thay đổi phá vỡ" ở cuối.

### Thêm

- **Provider cắm được** — chọn bản hiện thực bằng TÊN lúc chạy, thứ mà container
  (tra theo kiểu) không làm được: cổng thanh toán lấy từ cột trong đơn hàng, nhà
  mạng SMS lấy từ cấu hình, hãng camera lấy từ bản ghi thiết bị.

  Dùng đúng khuôn `Repository[User]` — service khai **năng lực** nó cần:

  ```python
  def __init__(self, payments: Providers[PaymentGateway]) -> None: ...

  cong = self._payments.get(don.cong_thanh_toan)   # -> PaymentGateway
  ```

  Thêm bản hiện thực mới = thả một file mang `@provider("tên")` vào
  `src/providers/<họ>/`. Không sửa service, không sửa `main.py`.

  `get()` trả 404 nếu không có tên, **501** nếu có tên nhưng thiếu năng lực —
  Hik không mở được cửa thì đó không phải bug của server. `names()` chỉ liệt kê
  provider làm được việc của sổ đó. Xem `docs/providers.md`, trong đó có mục
  "Viết năng lực cho dễ bảo trì": khi nào tách `capabilities.py` thành package,
  và ba nguyên tắc đặt tên / chia nhỏ interface.

- **`fam provider <họ> <tên>`** — sinh khung. Lần đầu dựng cả họ; lần sau đọc
  `capabilities.py` rồi sinh sẵn stub đúng chữ ký các method cần viết.

- **`container.build(cls, key=..., scope=...)`** — dựng một lớp có nối phụ thuộc
  mà KHÔNG đăng ký vào sổ toàn cục. Cần cho provider: sổ toàn cục tra theo tên
  class, trong khi hai họ có quyền cùng có một `OryzaProvider`.

### Thay đổi

- `fam p` giờ **nhập nhằng** giữa `publish` và `provider` nên `fam` hỏi lại. Viết
  tắt mới: `fam pu` cho publish, `fam pr` cho provider.
- `create_app()` và `src/main.py` sinh sẵn gọi `register_providers()` trước khi
  dựng route. Không có `src/providers/` thì bỏ qua, không lỗi.

### Thêm — query builder

- **`repo.query()`** sinh SQL thật: `join` / `left_join` / `right_join` /
  `outer_join`, `where` nối tiếp là AND còn `or_where` mở nhánh OR, `group_by` +
  `having`, `limit` / `offset` / `distinct`. Xem câu sinh ra bằng `.sql()`.
- **Toán tử thường trên cột**: `Camera.score >= 0.9` thay cho `score__gte=0.9`,
  nhờ `class Camera(Entity)`. Bảy toán tử không có ký hiệu (`like`, `ilike`,
  `in_`, `not_in`, `is_null`, `is_not_null`, `between`) có cả ở dạng method của
  builder — `query().like(...)` — để IDE gợi ý được.
- **Khoá ngoại**: `field(metadata=reference(Camera, on_delete="CASCADE"))`, đủ
  bốn hành vi `CASCADE` / `SET NULL` / `SET DEFAULT` / `RESTRICT`. Áp bởi chính
  database với SQL; khung tự áp cho `memory` và `mongodb` để ba backend cùng
  kết quả.
- **Transaction**: `async with db.transaction() as tx:` — khối lồng nhau thành
  SAVEPOINT, `await tx.rollback()` huỷ mà không phải ném lỗi. HTTP handler đã
  nằm sẵn trong một transaction của cả request.
- **Dữ liệu lồng nhau**: `include(X)` khai X trả về những cột nào,
  `nest_under(A, B, C)` khai thứ tự lồng từ NGOÀI vào TRONG. Mỗi mức đúng một
  câu lệnh, không phải một câu cho mỗi dòng.
- **`select(...)`** gộp cả `fields=` / `exclude=` / `rename=` / `add=`.
- **MongoDB** chạy được phần lớn builder (`docs/mongodb.md` liệt kê cái không có:
  JOIN, `group_by`, `distinct`, transaction).
- **Chặn injection ở tầng dùng chung**: giá trị mang toán tử (`{"$ne": ""}` —
  qua được cửa đăng nhập trên Mongo), khoá `$where` chạy JavaScript, và tên cột
  không có thật (SQL trước đây âm thầm bỏ điều kiện, trả về cả bảng).
- **Soi khoá ngoại lúc khởi động** (`db.foreign_keys_stale`): thêm
  `reference(...)` vào entity đã chạy rồi thì database không biết — cascade
  dừng giữa chừng, cháu ở lại thành mồ côi, không lỗi không cảnh báo.

### Thêm — việc chạy nền

- `@worker` (vòng lặp sống mãi, N bản, mỗi bản một tham số), `@interval` /
  `@cron` / `@timeout` (theo lịch), `@job` (hàng đợi trong tiến trình),
  `@on_event` + `EventBus` (fanout trong tiến trình). Cả bốn nhận `thread=True`
  cho hàm chặn, và `ctx: WorkerContext` để dừng đúng cách.
- Khoá `flock` / Redis để nhiều worker không cùng chạy một việc định kỳ.

### Thêm — RPC và hạ tầng

- **`emit` / `send` + `@*_responder`** cho RabbitMQ, Redis, MQTT, Kafka — khuôn
  tin tương thích `@nestjs/microservices`, đã chạy đối chứng hai chiều với
  NestJS 11.2.1.
- RabbitMQ: đủ 5 kiểu exchange, 3 dạng hạn dùng (TTL), `emit_many`.
- SQLite: mặc định WAL + `synchronous=NORMAL` — đo được nhanh gấp 20 lần
  (68 → 1.269 ghi/s) mà vẫn không hỏng file khi mất điện.

### Thêm — CLI

- **`fam install` ghi nhớ thành phần vào `requirements.txt`**
  (`fastapi-modular[redis,sqlite]>=0.3.0`), để người clone repo về chỉ cần
  `pip install -r requirements.txt`. `fam init` sinh sẵn file này. Dự án dùng
  `pyproject.toml` đã khai fastapi-modular thì sửa ngay dòng đó. `fam install
  dev` đi vào `requirements-dev.txt`.
- `fam module` sinh entity kế thừa `Entity`.

### Sửa

- **Controller viết `def` thường** nổ `TypeError: object dict can't be used in
  'await' expression`. Khung bọc mọi method thành endpoint async rồi await
  thẳng, nên mất luôn luật của FastAPI: `def` phải chạy ở thread pool. Guard
  đồng bộ cũng nổ y hệt. Đo lại sau khi sửa: 4 request chặn 0,3s xong trong
  0,31s.
- **`fam install` chạy trước `fam init`** chỉ ghi khối database vào `.env`,
  thiếu sạch `APP_NAME` / `APP_ENV` / `APP_DEBUG` / `APP_HOST` / `APP_PORT` —
  app chạy bằng toàn giá trị mặc định, im lặng.
- **Lọc bằng `Enum` thường** (không phải `StrEnum`) chạy trên `memory` nhưng nổ
  trên sqlite lẫn mongo. Chiều ngược lại cũng lệch: lọc bằng chuỗi `.value` thì
  SQL khớp còn memory trượt.
- **`nest_under` + sắp theo cột của một lớp**: sqlite ném lỗi, memory và mongo
  lặng lẽ bỏ qua.
- **Lồng ba tầng từ repo trong cùng**: `include(Camera)` ném lỗi dù chuỗi
  `nest_under` nối được qua bảng giữa.
- **`include(X, name=...)`** bị bỏ qua khi X nằm trong chuỗi `nest_under`.
- **Hai transaction `memory` đồng thời**: task rollback cuốn luôn bản ghi task
  khác vừa commit.
- **MongoDB cho ghi con trỏ tới cha không tồn tại**, trong khi SQL và memory từ
  chối 409.
- `Ctrl+C` không thoát được khi có worker đang chạy; `@worker(thread=True)` mượn
  pool dùng chung của event loop nên làm treo cả tiến trình.

### Thay đổi phá vỡ

| 0.2.x | 0.3.0 |
|---|---|
| `.order_by("score")` | `.order_by_desc("score")` / `.order_by_asc("score")` — chiều nằm trong TÊN HÀM |
| `.fields([...])` · `.exclude([...])` | `.select(fields=[...], exclude=[...])` |
| `fam p` | nhập nhằng — `fam pu` (publish) hoặc `fam pr` (provider) |

Định danh trong thư viện đổi hết sang tiếng Anh; nếu bạn import hàm `_private`
nào của khung thì kiểm lại tên. API công khai không đổi.

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
