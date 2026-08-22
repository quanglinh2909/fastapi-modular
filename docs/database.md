# Hướng dẫn database

Template hỗ trợ **4 backend**: `memory` (mặc định), `sqlite`, `postgres`, `mongodb`.
Tại một thời điểm chỉ **một** backend được dùng, và **chỉ thư viện của backend đó
cần được cài** — chọn Postgres thì máy không cần `aiosqlite` hay `motor`.

Điều này làm được vì mọi `import sqlalchemy` / `import motor` nằm bên trong hàm
`create_backend()` ở [`pymodular/infrastructure/database/factory.py`](../pymodular/infrastructure/database/factory.py),
chứ không ở đầu file. Chọn driver chưa cài thư viện sẽ nhận thông báo:

```
Driver database 'postgres' cần thư viện 'sqlalchemy' nhưng chưa cài.
Chạy: pym install postgres
```

---

## Bảng tra nhanh

| | memory | sqlite | postgres | mongodb |
|---|---|---|---|---|
| Lệnh cài | (không cần) | `pym install sqlite` | `pym install postgres` | `pym install mongodb` |
| Thư viện | (không cần) | `pymodular[sqlite]` | `pymodular[postgres]` | `pymodular[mongodb]` |
| Thư viện | – | `sqlalchemy`, `aiosqlite` | `sqlalchemy`, `asyncpg` | `motor` |
| Cần server riêng | không | không | có | có |
| Dữ liệu sống qua restart | **không** | có | có | có |
| Chạy nhiều worker | **không** | có | có | có |
| Transaction mỗi request | không | có | có | không¹ |
| Dùng cho | test, demo | dev, app một máy | production | production |

¹ MongoDB chỉ có transaction đa-document khi chạy replica set; template không bật.
Mỗi thao tác ghi một document vẫn nguyên tử.

---

## Cách chọn driver

Mỗi lệnh `make install-*` làm hai việc: cài thư viện, **và ghi sẵn biến vào `.env`**
để bạn chỉ việc sửa giá trị. Khối này nằm giữa hai dòng mốc:

```dotenv
# >>> database (sinh bởi pym env) >>>
APP_DB__DRIVER=sqlite
APP_DB__DSN=sqlite+aiosqlite:///./data/app.db
# <<< database <<<
```

Chạy lệnh cài của driver khác sẽ **thay** khối này, không chồng thêm — nên không
bao giờ có hai driver cùng khai báo. Mọi biến khác trong `.env` (`APP_HOST`,
`APP_PORT`, khoá bí mật...) được giữ nguyên.

Xem đang dùng gì:

```bash
pym info
```

---

## 1. memory (mặc định)

Không cần cài gì, không cần cấu hình gì.

```bash
pip install pymodular      # chỉ thư viện lõi
pym dev
```

**Chỉ dùng cho test và demo.** Hai giới hạn phải nhớ:

- Dữ liệu mất sạch khi restart.
- Mỗi worker giữ một bản riêng, nên `--workers 2` trở lên sẽ trả kết quả
  **khác nhau tuỳ request rơi vào worker nào**. Với backend này chỉ chạy 1 worker.

---

## 2. SQLite

Không cần server, dữ liệu nằm trong một file.

```bash
pym install sqlite
```

Biến sinh ra trong `.env`:

```dotenv
APP_DB__DRIVER=sqlite
APP_DB__DSN=sqlite+aiosqlite:///./data/app.db
APP_DB__SCHEMA_MODE=create
APP_DB__DROP_COLUMNS=false
APP_DB__ECHO=false
```

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_DB__DRIVER` | **có** | `memory` | phải là `sqlite`, nếu không app dùng bộ nhớ tạm |
| `APP_DB__DSN` | không | `(trống)` → `sqlite+aiosqlite:///./data/app.db` | `///./data/x.db` là tương đối, `////tmp/x.db` (4 gạch) là tuyệt đối. Thư mục phải tồn tại — `pym install sqlite` tự tạo `./data` |
| `APP_DB__SCHEMA_MODE` | không | `create` | `off` / `create` / `sync` — xem [mục schema](#tự-chỉnh-schema-thêm--xoá-trường) |
| `APP_DB__DROP_COLUMNS` | không | `false` | `true` = cho `sync` xoá cột không còn trong entity. Mất dữ liệu |
| `APP_DB__ECHO` | không | `false` | `true` = in mọi câu SQL ra log |

```bash
pym dev
```

Khởi động sẽ thấy:

```
db.connected      backend=sqlite
db.schema_ready   tables=['devices', 'users']
```

**Lưu ý riêng của SQLite:** kiểu `DATETIME` không lưu múi giờ (MongoDB cũng
vậy). Template gắn lại UTC lúc đọc nên API của cả ba driver đều trả dạng
`2026-08-21T02:52:02.049410Z` như nhau — nhưng nếu bạn truy vấn thẳng vào file
`.db` bằng công cụ khác thì sẽ thấy giá trị trần không có múi giờ. Ngoài ra
SQLite khoá toàn bộ file khi ghi, nên không hợp với tải ghi cao nhiều tiến trình.

Xoá và làm lại từ đầu:

```bash
rm -f data/app.db && pym dev
```

---

## 3. PostgreSQL

```bash
pym install postgres
```

Biến sinh ra trong `.env` (mỗi dòng kèm giải thích và giá trị mặc định):

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_DB__DRIVER` | **có** | `memory` | phải là `postgres` |
| `APP_DB__DSN` | **có** | `(trống)` → `postgresql+asyncpg://postgres:postgres@localhost:5432/app` | xem cấu trúc bên dưới |
| `APP_DB__SCHEMA_MODE` | không | `create` | `off` / `create` / `sync` |
| `APP_DB__DROP_COLUMNS` | không | `false` | cho `sync` xoá cột — mất dữ liệu |
| `APP_DB__ECHO` | không | `false` | in câu SQL ra log |

Kèm theo 11 biến về pool, timeout và ngắt mạch — tất cả đều **tuỳ chọn**, xem
[bảng đầy đủ](#cấu-hình-kết-nối).

Cấu trúc DSN:

```
postgresql+asyncpg://NGƯỜI_DÙNG:MẬT_KHẨU@HOST:CỔNG/TÊN_DB
                 ^^^^^^^ bắt buộc là asyncpg — template chạy bất đồng bộ
```

Nếu mật khẩu có ký tự đặc biệt (`@`, `:`, `/`) thì phải URL-encode: `p@ss` → `p%40ss`.

### Dựng server nhanh bằng Docker

```bash
docker run -d --name ss-pg \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=app \
  -p 5432:5432 postgres:16-alpine
```

Kiểm tra sẵn sàng: `docker exec ss-pg pg_isready -U postgres`

### Chạy

```bash
pym dev
curl localhost:8000/api/health/ready
# {"status":"ready","driver":"postgres","database":true}
```

Xem bảng đã tạo:

```bash
docker exec ss-pg psql -U postgres -d app -c '\dt'
```

### Lên production

- Đặt `APP_DB__SCHEMA_MODE=off` và dùng Alembic để migrate. Lý do ở
  [mục schema](#tự-chỉnh-schema-thêm--xoá-trường).
- Chạy nhiều worker được: `pym run --workers 4`.

---

## 4. MongoDB

```bash
pym install mongodb
```

Biến sinh ra trong `.env`:

```dotenv
APP_DB__DRIVER=mongodb
APP_DB__DSN=mongodb://localhost:27017
APP_DB__NAME=app
```

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `APP_DB__DRIVER` | **có** | `memory` | phải là `mongodb` |
| `APP_DB__DSN` | **có** | `(trống)` → `mongodb://localhost:27017` | `mongodb://HOST:CỔNG`, hoặc `mongodb+srv://USER:PASS@CỤM` cho Atlas |
| `APP_DB__NAME` | không | `app` | tên database bên trong Mongo. Collection lấy theo entity: `users`, `devices` |

### Dựng server nhanh bằng Docker

```bash
docker run -d --name ss-mongo -p 27017:27017 mongo:7
```

### Chạy

```bash
pym dev
curl localhost:8000/api/health/ready
# {"status":"ready","driver":"mongodb","database":true}
```

Xem collection:

```bash
docker exec ss-mongo mongosh --quiet app --eval 'db.getCollectionNames()'
```

### Khác biệt cần biết

- Trường `id` của entity được lưu vào `_id` của document, nên không tốn thêm index.
- `APP_DB__SCHEMA_MODE` không có tác dụng: Mongo không có schema cố định.
- Không có transaction đa-document (cần replica set). Cụ thể: xoá user kèm
  `?cascade=true` sẽ xoá thiết bị trước rồi mới xoá user — nếu tiến trình chết
  giữa chừng, thiết bị đã mất mà user vẫn còn. Với SQL thì cả hai cùng rollback.
- Chưa có index nào ngoài `_id`. Khi dữ liệu lớn, tự tạo index cho trường hay lọc:
  ```bash
  docker exec ss-mongo mongosh app --eval \
    'db.users.createIndex({email: 1}); db.devices.createIndex({owner_id: 1})'
  ```

---

## Tự chỉnh schema (thêm / xoá trường)

Tương đương `synchronize: true` của TypeORM trong NestJS. Điều khiển bằng
`APP_DB__SCHEMA_MODE`:

| Mức | Làm gì | Dùng khi |
|---|---|---|
| `off` | Không đụng schema | Production (đi kèm Alembic) |
| `create` | Chỉ `CREATE TABLE` cho bảng còn thiếu. Thêm/xoá trường trong entity **không** ảnh hưởng bảng đã có | Mặc định |
| `sync` | Thêm cột mới bằng `ALTER TABLE ADD COLUMN`; cột thừa thì xoá (nếu bật `DROP_COLUMNS`) hoặc cảnh báo; cột lệch kiểu chỉ cảnh báo | Đang phát triển, entity còn đổi nhiều |

```dotenv
APP_DB__SCHEMA_MODE=sync
APP_DB__DROP_COLUMNS=false   # true = cho phép xoá cột (MẤT DỮ LIỆU)
```

### Thêm trường

Thêm field vào dataclass entity rồi khởi động lại:

```python
@entity
@dataclass(slots=True)
class Camera:
    id: str
    name: str
    status: str = "offline"      # <-- trường mới
```

```
db.schema_ready  mode=sync  added=['cameras.status']
```

Bản ghi cũ **giữ nguyên**, và trường mới đọc ra bằng **default của entity**
(`"offline"`) chứ không phải `NULL` — dù dưới database cột đó đang là NULL. Ngoại
lệ: kiểu chấp nhận `None` (`str | None`) thì giữ `None`, vì đó là giá trị hợp lệ.

### Xoá trường

Bỏ field khỏi entity. Mặc định cột **không** bị xoá, chỉ cảnh báo:

```
db.extra_column_kept  column=cameras.port  hint='đặt APP_DB__DROP_COLUMNS=true nếu muốn xoá (mất dữ liệu)'
```

Muốn xoá thật thì `APP_DB__DROP_COLUMNS=true`:

```
db.schema_ready  mode=sync  dropped=['cameras.port']
```

Cột biến mất, các hàng vẫn còn nguyên. SQLite cần bản ≥ 3.35 mới `DROP COLUMN`
được; bản cũ hơn sẽ log `db.drop_column_failed` và giữ cột lại.

### Đổi kiểu trường

**Không tự động.** Chỉ cảnh báo:

```
db.column_type_mismatch  column='cameras.port: VARCHAR -> INTEGER'
```

Cố ý làm vậy: mỗi database một cú pháp `ALTER COLUMN` khác nhau, phép đổi có thể
mất dữ liệu (`VARCHAR` → `INTEGER` với dữ liệu không phải số) và có thể khoá bảng
rất lâu trên bảng lớn. Đó là việc của một migration được review, không phải của
lúc khởi động.

### Vì sao đừng dùng `sync` ở production

Giống hệt lý do TypeORM khuyến cáo không bật `synchronize` ở prod:

- Đổi tên field trông giống hệt "xoá cột cũ + thêm cột mới" → dữ liệu cột cũ mất sạch.
- Không có bước review, không rollback được, không có lịch sử thay đổi.
- Hai tiến trình cùng khởi động có thể chạy DDL đồng thời.
- Không đổi được kiểu, nên schema vẫn lệch dần mà chỉ có một dòng log cảnh báo.

Ở prod: `APP_DB__SCHEMA_MODE=off` và dùng Alembic. Khởi động với `env=prod` mà
schema_mode khác `off` sẽ bị log cảnh báo `config.unsafe_for_production`.

### MongoDB thì sao

Mongo không có schema cố định nên **không cần migrate gì cả**:

- **Thêm trường**: document cũ thiếu khoá đó, đọc ra sẽ dùng default của entity.
- **Xoá trường**: document cũ vẫn còn khoá thừa dưới database, đọc ra thì bị bỏ qua.
  Muốn dọn thật thì tự chạy `db.cameras.updateMany({}, {$unset: {port: ""}})`.

---

## Mất kết nối database

### Có tự kết nối lại không

**Có**, cả ba driver, không cần restart app:

| Tình huống | Hành vi |
|---|---|
| Database restart nhanh (pool còn giữ connection chết) | `pool_pre_ping` phát hiện và thay connection mới — client **không thấy lỗi nào** |
| Database tắt hẳn | Request trả `503 database_unavailable`; `/health/ready` trả 503, `/health` vẫn 200 |
| Database bật lại | Request kế tiếp thành công ngay, không cần restart app |
| Database chưa lên lúc app khởi động | Thử lại `STARTUP_RETRIES` lần, hết lượt thì thoát kèm lý do |

### Vì sao 503 chứ không phải 500

Database rớt là tình trạng vận hành **tạm thời**. `503` nói với load balancer và
client rằng thử lại là có ích; `500` nghĩa là "gửi lại cũng vô ích". Body giữ
nguyên khuôn dạng lỗi chung:

```json
{"code": "database_unavailable",
 "message": "Không kết nối được cơ sở dữ liệu, vui lòng thử lại",
 "request_id": "..."}
```

Chi tiết kỹ thuật chỉ hiện khi `APP_DEBUG=true`.

### Các biến điều chỉnh

**Không truyền gì thì đã có mặc định dùng được ngay** — bảng dưới là giá trị
mặc định và lúc nào cần sửa.

Không biến nào dưới đây bắt buộc — xoá dòng là quay về mặc định.

| Biến | Mặc định | Ý nghĩa | Khi nào cần sửa |
|---|---|---|---|
| `APP_DB__POOL_PRE_PING` | `true` | Thử connection trước khi giao cho request | Hầu như không bao giờ. Tắt đi thì **mỗi lần database restart sẽ có đúng một request lỗi**; đổi lại tiết kiệm một round-trip nhỏ mỗi lần lấy connection |
| `APP_DB__POOL_SIZE` | `5` | Connection giữ thường trực **mỗi worker** | Xem [ngân sách connection](#ngân-sách-connection) |
| `APP_DB__MAX_OVERFLOW` | `10` | Connection mở thêm khi quá tải, **mỗi worker** | nt |
| `APP_DB__POOL_RECYCLE_SECONDS` | `1800` | Mở lại connection cũ hơn ngưỡng này | Giảm xuống nếu proxy/firewall của bạn cắt kết nối nhàn rỗi sớm hơn 30 phút |
| `APP_DB__CONNECT_TIMEOUT_SECONDS` | `10` | Chờ tối đa khi mở kết nối. Với MongoDB đây cũng là hạn chọn server | Giữ ≥10s nếu Mongo chạy replica set — lúc bầu primary mới cần chừng đó thời gian. Giảm xuống 3–5s nếu muốn request thất bại nhanh |
| `APP_DB__STARTUP_RETRIES` | `10` | Số lần thử lại lúc khởi động | Tăng nếu database của bạn khởi động chậm hơn 10 giây |
| `APP_DB__STARTUP_RETRY_DELAY_SECONDS` | `1` | Nghỉ giữa hai lần thử | nt |

### Ngân sách connection

Trần connection là **(pool_size + max_overflow) × số worker**, không phải tổng
của cả app — mỗi worker là một tiến trình riêng với pool riêng.

| Số worker | Trần lý thuyết | So với `max_connections=100` mặc định |
|---|---|---|
| 1 | 15 | thoải mái |
| 4 | 60 | vừa |
| 6 | 90 | sát trần |
| 8 | 120 | **vượt** — sẽ có request lỗi vì hết slot |

Thực tế thấp hơn nhiều: đo với 4 worker và 60 luồng đồng thời chỉ dùng **20**
connection (lúc rảnh là 4), vì overflow chỉ mở khi pool thường trực hết chỗ.
Nhưng nên tính theo trần lý thuyết, vì đúng lúc tải đỉnh mới là lúc chạm nó.

Từ 6 worker trở lên: giảm `POOL_SIZE`/`MAX_OVERFLOW`, hoặc nâng
`max_connections` của Postgres, hoặc đặt pgbouncer ở giữa.

### Lỗi cấu hình không bị thử lại

Retry chỉ dành cho lỗi **có thể tự hết**: `ConnectionRefused`, `ConnectionReset`,
timeout, `ServerSelectionTimeoutError` của Mongo. Sai mật khẩu hay sai tên
database thì dừng ngay, vì thử lại chỉ làm chậm lúc bạn phát hiện cấu hình sai:

```
db.config_error_at_startup  error='InvalidPasswordError: password authentication failed for user "postgres"'
                            hint='lỗi này không tự hết khi thử lại — kiểm tra APP_DB__DSN'
```

So với database chưa kịp lên — thử lại, và mỗi dòng log đều nói rõ lý do:

```
db.retry_connect         attempt=1 of=10 error='ConnectionRefusedError: [Errno 111] Connection refused' retry_in=1.0
db.retry_connect         attempt=4 of=10 error='ConnectionResetError: [Errno 104] Connection reset by peer' retry_in=1.0
db.connected_after_retry attempts=5
```

### Khởi động khi database chưa sẵn sàng

Hay gặp với docker compose: app lên trước database.

```
db.retry_connect  attempt=1 of=8 backend=postgres retry_in=1.0
db.retry_connect  attempt=2 of=8 ...
db.schema_ready   mode=create tables=['devices', 'users']
app.started       driver=postgres
```

Hết lượt thì thoát rõ ràng thay vì chạy tiếp với database chết:

```
db.unreachable_at_startup  attempts=2 backend=postgres error='ConnectionRefusedError: ...'
Application startup failed. Exiting.
```

### Khác biệt theo driver

- **PostgreSQL** — SQLAlchemy quản pool; `pool_pre_ping` lo phần phát hiện connection chết.
- **SQLite** — file trên đĩa, không có "mất kết nối" theo nghĩa mạng. Lỗi thường gặp là file bị khoá khi có tiến trình khác đang ghi.
- **MongoDB** — motor/pymongo tự dò lại server và kết nối lại; template chỉ siết
  `serverSelectionTimeoutMS` để request không treo 30 giây khi Mongo chết.

### Điều template CHƯA làm

- **Không tự thử lại một request đã lỗi.** Một request rơi đúng lúc database
  chết sẽ nhận 503; client tự quyết định thử lại. Cố retry ở tầng server rất dễ
  gây ghi trùng với các thao tác không idempotent (POST tạo bản ghi).
- **Không có circuit breaker.** Database chết thì mọi request đều đi tới database
  và cùng nhận 503, thay vì chặn sớm.

---

## Đổi driver khi đang chạy dở

```bash
pym install postgres   # thay khối trong .env
pym dev
```

Dữ liệu **không** tự chuyển giữa các backend. Cần chuyển thì phải xuất/nhập thủ công.

---

## Dùng Repository trong code

Service chỉ khai báo kiểu, không cần biết backend nào:

```python
@injectable
class CameraService:
    def __init__(self, cameras: Repository[Camera]) -> None:
        self._cameras = cameras

    async def find_online(self) -> list[Camera]:
        return await self._cameras.find(status="online", limit=50)
```

Entity phải là dataclass có `@entity`:

```python
@entity                              # hoặc @entity(name="camera_list") để tự đặt tên bảng
@dataclass(slots=True)
class Camera:
    id: str
    name: str
    status: str
    created_at: datetime = field(default_factory=utcnow)
```

Tên bảng/collection mặc định là tên class viết thường cộng `s`: `Camera` → `cameras`.

### Bộ hàm có sẵn

| Hàm | Việc |
|---|---|
| `get(id)` | Lấy theo id, không có thì `None` |
| `find(**equals, match=, order_by=, limit=, offset=)` | Danh sách |
| `find_one(**equals, match=)` | Bản ghi đầu tiên khớp |
| `count(**equals, match=)` | Đếm |
| `exists(**equals, match=)` | Có hay không |
| `save(obj)` | Upsert, tự sinh id nếu chưa có |
| `delete(id)` | Xoá một, trả `True/False` |
| `delete_where(**equals, match=)` | Xoá nhiều, trả số bản ghi |

### Hai quy ước dễ vấp

**`None` nghĩa là "không lọc"**, không phải "bằng NULL":

```python
await repo.find(owner_id=None)     # trả về TẤT CẢ, không phải bản ghi có owner_id NULL
await repo.find(match=lambda o: o.owner_id is None)   # đây mới là lọc NULL
```

**`match=` chạy trong Python, không đẩy được xuống database.** Backend sẽ lấy dữ
liệu về rồi mới lọc, nên với bảng lớn thì chậm và tốn RAM. Chỉ dùng cho điều kiện
không diễn đạt được bằng so sánh bằng nhau (ví dụ so email không phân biệt hoa
thường). Khi dữ liệu lớn, thay bằng cách chuẩn hoá dữ liệu lúc ghi (lưu sẵn
`email_lower`) rồi lọc bằng `email_lower=...`.

---

## Ràng buộc duy nhất và index

Khai báo ngay trên entity, tương đương `@Index`/`@Column({unique:true})` của TypeORM:

```python
@entity(unique=["email"])
@dataclass(slots=True)
class User:
    ...
```

Mỗi phần tử là **một cột** (chuỗi) hoặc **một cụm cột** (tuple). Ví dụ đầy đủ,
một bảng vừa có index đơn vừa có cụm index:

```python
@entity(
    unique=[
        "serial",                    # ĐƠN  : không hai thiết bị nào trùng serial
        ("owner_id", "name"),        # CỤM  : một chủ không đặt trùng tên hai thiết bị,
                                     #        nhưng hai chủ khác nhau vẫn được trùng tên
    ],
    indexes=[
        ("owner_id", "status"),      # CỤM  : "thiết bị của chủ X đang ở trạng thái Y"
        "status",                    # ĐƠN  : "mọi thiết bị đang bảo trì" (trang quản trị)
    ],
)
@dataclass(slots=True)
class Device:
    ...
```

Sinh ra 4 index:

```
uq_devices_serial            ['serial']                unique=True
uq_devices_owner_id_name     ['owner_id', 'name']      unique=True
ix_devices_owner_id_status   ['owner_id', 'status']    unique=False
ix_devices_status            ['status']                unique=False
```

### Cụm unique chặn theo CẢ CỤM

```
chủ1 + "Cam A"        -> 201
chủ1 + "Cam A" lần 2  -> 409     cùng chủ, trùng tên
chủ2 + "Cam A"        -> 201     khác chủ thì trùng tên vẫn được
chủ1 + "Cam B"        -> 201
```

### Thứ tự cột trong cụm rất quan trọng

Quy tắc **tiền tố trái**: cụm `(owner_id, status)` phục vụ được truy vấn lọc
theo `owner_id`, hoặc `owner_id + status` — nhưng **không** phục vụ được truy
vấn chỉ lọc theo `status`. Đo bằng `EXPLAIN ANALYZE` trên PostgreSQL với 60.000
bản ghi:

| Truy vấn | Index được dùng |
|---|---|
| `WHERE owner_id = ...` | `ix_devices_owner_id_status` (tiền tố trái) |
| `WHERE owner_id = ... AND status = ...` | `ix_devices_owner_id_status` |
| `WHERE status = ...` | `ix_devices_status` — **không** phải cụm |

Bỏ index đơn `status` đi thì truy vấn cuối rơi xuống quét toàn bảng:

```
CÓ  index đơn : Bitmap Index Scan on ix_devices_status  (0.33 ms)
BỎ  index đơn : Seq Scan on devices                     (5.18 ms)
```

Vì vậy **không cần** thêm index riêng cho `owner_id` (cụm đã lo), nhưng **cần**
index riêng cho `status`. Đặt cột chọn lọc cao hơn lên trước trong cụm.

### Tên index

Sinh tự động theo mẫu `uq_<bảng>_<các cột>` / `ix_<bảng>_<các cột>`. Tên vượt
63 ký tự (giới hạn định danh của PostgreSQL) được rút gọn bằng mã băm ổn định.

Ràng buộc được tạo **dưới database** (`CREATE UNIQUE INDEX IF NOT EXISTS` với
SQL, `create_index(unique=True)` với Mongo), và backend `memory` mô phỏng lại
để test chạy trên memory cũng bắt được lỗi.

### Vì sao không thể chỉ kiểm tra trong service

Kiểm tra rồi mới ghi là một cuộc đua: hai request đồng thời đều thấy "chưa có"
rồi cùng ghi. Đo thật với 20 request đồng thời cùng email:

| | Kết quả | Số bản ghi trong DB |
|---|---|---|
| Chỉ kiểm tra ở service | 15 × `201`, 5 × `409` | **15 bản trùng** |
| Có unique index | 1 × `201`, 19 × `409` | 1 |

Lớp kiểm tra trong service vẫn giữ, nhưng chỉ để cho thông báo lỗi dễ hiểu ở
trường hợp thường; database mới là nơi đảm bảo.

### Email được hạ chữ thường ở cửa vào

Unique index phân biệt hoa thường, nên `UserBase` có validator hạ chữ thường.
Nhờ vậy `AN@Example.com` và `an@example.com` là một, và tra cứu theo email dùng
được index thay vì phải quét toàn bảng bằng `match=`. Đo trên 5.000 bản ghi:

```
match= (quét toàn bảng) : 22.09 ms
email= (dùng index)     :  0.55 ms      -> nhanh hơn 40 lần
```

### Cảnh báo khi schema_mode="off"

Ở chế độ `off`, index **không** được tạo tự động — chúng phải nằm trong
migration. App sẽ soi và kêu nếu thiếu:

```
db.indexes_missing  indexes=['devices.owner_id', 'users.email (UNIQUE)']
                    hint="schema_mode='off' nên index không được tạo tự động — hãy thêm chúng
                          vào migration, nếu không ràng buộc duy nhất sẽ không có hiệu lực"
```

---

## Dấu thời gian

Entity nào có trường `updated_at` thì `Repository.save()` **tự đóng dấu** — không
service nào phải tự gán, nên không có chỗ nào quên. Tương đương
`@UpdateDateColumn` của TypeORM.

```python
# service chỉ cần
changed = apply_changes(user, payload)
return await self._repo.save(user)          # updated_at do repository lo
```

Hai quy ước:

- Bản ghi **mới** có `updated_at == created_at`, nên "chưa từng sửa" nhận ra được
  bằng cách so hai mốc.
- `datetime` đọc ra **luôn có tzinfo UTC**, bất kể driver. SQLite và MongoDB lưu
  UTC nhưng không kèm múi giờ; template gắn lại lúc đọc để một response không
  lẫn lộn `"...Z"` với `"..."` không hậu tố.

---

## Transaction

Với `sqlite`/`postgres`, mỗi HTTP request dùng chung **một** connection và **một**
transaction, do `SqlUnitOfWork` giữ (provider `Scope.REQUEST`):

- Handler chạy xong không lỗi → `COMMIT`
- Handler ném exception → `ROLLBACK`

Commit xảy ra **trước khi** response được gửi đi, nên client ghi xong đọc lại ngay
sẽ thấy dữ liệu mới. Chi tiết vì sao chỗ này quan trọng: xem ghi chú đầu file
[`pymodular/middleware/request_context.py`](../pymodular/middleware/request_context.py).

Gọi repository **ngoài** một HTTP request (script, worker, test) vẫn được — backend
sẽ tự mở một connection tạm và commit ngay sau thao tác.

---

## Test trên database thật

Mặc định `pym test` chạy trên backend `memory` (nhanh, không cần server). Muốn
chạy đúng bộ test đó trên database thật:

```bash
TEST_SQLITE=1 pym test
TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app' pym test
TEST_MONGO_DSN='mongodb://127.0.0.1:27017' pym test
```

Driver nào chưa cài thư viện hoặc chưa có server sẽ **SKIP** chứ không FAIL, nên
bộ test chạy được trên máy chỉ cài một driver.

---

## Thêm backend mới

1. Viết class cài đủ bộ method của `DatabaseBackend`
   ([`pymodular/infrastructure/database/base.py`](../pymodular/infrastructure/database/base.py)).
2. Thêm một nhánh trong `create_backend()`, **import bên trong hàm**.
3. Thêm một extra `<tên>` trong `pyproject.toml` và một khối trong `pym env`.
4. Thêm một `pytest.param` vào `tests/test_drivers.py` — bộ test CRUD dùng chung
   sẽ tự chạy cho backend mới.
