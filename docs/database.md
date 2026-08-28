# Database: chọn driver và kết nối

SQLite, PostgreSQL, và backend `memory` (bản mô phỏng để `fam test` chạy không
cần server). **Dùng MongoDB thì đọc [mongodb.md](mongodb.md)** — bên đó khác đủ
nhiều để trộn chung một trang là hại người đọc: không có query builder, không
có transaction, không có migration.

> **Database chia làm năm trang.** Bạn đang ở **database.md**.
>
> [database.md](database.md) chọn driver và kết nối ·
> [entity.md](entity.md) khai bảng, khoá ngoại, index ·
> [repository.md](repository.md) CRUD ·
> [query.md](query.md) truy vấn phức tạp ·
> [transaction.md](transaction.md) ghi nhiều bảng ·
> [mongodb.md](mongodb.md) riêng cho MongoDB

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Chọn database nào" | [Cách chọn driver](#cách-chọn-driver) |
| "Đổi từ memory sang SQLite / PostgreSQL" | [Cách chọn driver](#cách-chọn-driver) |
| "SQLite có chịu được nhiều người ghi không" | [2. SQLite](#2-sqlite) |
| "**Mất điện thì file .db có hỏng không**" | [Tắt đột ngột](#tắt-đột-ngột-kill--9-và-rút-điện) |
| "Sao lưu dữ liệu" | [Sao lưu](#sao-lưu-đừng-chép-mỗi-file-db) |
| "Thêm/xoá trường mà không mất dữ liệu" | [Tự chỉnh schema](#tự-chỉnh-schema-thêm--xoá-trường) |
| "**Database rớt thì app có chết theo không**" | [Mất kết nối database](#mất-kết-nối-database) |
| "Chạy test trên database thật" | [Test trên database thật](#test-trên-database-thật) |
| "Khai bảng, khoá ngoại, index" | [entity.md](entity.md) |
| "Đọc/ghi trong service" | [repository.md](repository.md) |
| "JOIN, lớn/bé, NULL, gộp nhóm" | [query.md](query.md) |
| "Ghi 2 bảng, hỏng thì huỷ cả hai" | [transaction.md](transaction.md) |
| "Tôi đang dùng MongoDB" | [mongodb.md](mongodb.md) |

---

Template hỗ trợ **4 backend**: `memory` (mặc định), `sqlite`, `postgres`, và
`mongodb` ([trang riêng](mongodb.md)). Tại một thời điểm chỉ **một** backend
được dùng, và **chỉ thư viện của backend đó cần được cài** — chọn Postgres thì
máy không cần `aiosqlite` hay `motor`.

Điều này làm được vì mọi `import sqlalchemy` / `import motor` nằm bên trong hàm
`create_backend()` ở [`fastapi_modular/infrastructure/database/factory.py`](../fastapi_modular/infrastructure/database/factory.py),
chứ không ở đầu file. Chọn driver chưa cài thư viện sẽ nhận thông báo:

```
Driver database 'postgres' cần thư viện 'sqlalchemy' nhưng chưa cài.
Chạy: fam install postgres
```

---

## Bảng tra nhanh

| | memory | sqlite | postgres | [mongodb](mongodb.md) |
|---|---|---|---|---|
| Lệnh cài | (không cần) | `fam install sqlite` | `fam install postgres` | `fam install mongodb` |
| Gói | (không cần) | `fastapi-modular[sqlite]` | `fastapi-modular[postgres]` | `fastapi-modular[mongodb]` |
| Thư viện | – | `sqlalchemy`, `aiosqlite` | `sqlalchemy`, `asyncpg` | `motor` |
| Cần server riêng | không | không | có | có |
| Dữ liệu sống qua restart | **không** | có | có | có |
| Chạy nhiều worker | **không** | có | có | có |
| Transaction | có (chụp ảnh) | có | có | **không**¹ |
| Query builder (`repo.query()`) | có | có | có | trừ `join`, `group_by` |
| Khoá ngoại do database áp | mô phỏng | có | có | **không**, khung tự dọn |
| Dùng cho | test, demo | dev, app một máy | production | production |

¹ Mongo chỉ có transaction đa-document khi chạy replica set; template không bật.
Mỗi thao tác ghi một document vẫn nguyên tử. Những ô khác biệt ở trên là lý do
MongoDB có [trang hướng dẫn riêng](mongodb.md) — đọc trước khi chọn nó.

---

## Cách chọn driver

Mỗi lệnh `make install-*` làm hai việc: cài thư viện, **và ghi sẵn biến vào `.env`**
để bạn chỉ việc sửa giá trị. Khối này nằm giữa hai dòng mốc:

```dotenv
# >>> database (sinh bởi fam env) >>>
APP_DB__DRIVER=sqlite
APP_DB__DSN=sqlite+aiosqlite:///./data/app.db
# <<< database <<<
```

Chạy lệnh cài của driver khác sẽ **thay** khối này, không chồng thêm — nên không
bao giờ có hai driver cùng khai báo. Mọi biến khác trong `.env` (`APP_HOST`,
`APP_PORT`, khoá bí mật...) được giữ nguyên.

Xem đang dùng gì:

```bash
fam info
```

---

## 1. memory (mặc định)

Không cần cài gì, không cần cấu hình gì.

```bash
pip install fastapi-modular      # chỉ thư viện lõi
fam dev
```

**Chỉ dùng cho test và demo.** Hai giới hạn phải nhớ:

- Dữ liệu mất sạch khi restart.
- Mỗi worker giữ một bản riêng, nên `--workers 2` trở lên sẽ trả kết quả
  **khác nhau tuỳ request rơi vào worker nào**. Với backend này chỉ chạy 1 worker.

---

## 2. SQLite

Không cần server, dữ liệu nằm trong một file.

```bash
fam install sqlite
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
| `APP_DB__DSN` | không | `(trống)` → `sqlite+aiosqlite:///./data/app.db` | `///./data/x.db` là tương đối, `////tmp/x.db` (4 gạch) là tuyệt đối. Thư mục phải tồn tại — `fam install sqlite` tự tạo `./data` |
| `APP_DB__SCHEMA_MODE` | không | `create` | `off` / `create` / `sync` — xem [mục schema](#tự-chỉnh-schema-thêm--xoá-trường) |
| `APP_DB__DROP_COLUMNS` | không | `false` | `true` = cho `sync` xoá cột không còn trong entity. Mất dữ liệu |
| `APP_DB__ECHO` | không | `false` | `true` = in mọi câu SQL ra log |
| `APP_DB__SQLITE_JOURNAL_MODE` | không | `WAL` | `DELETE` là mặc định gốc của SQLite và **chậm 20 lần** — xem bên dưới |
| `APP_DB__SQLITE_SYNCHRONOUS` | không | `NORMAL` | `FULL` / `NORMAL` / `OFF`; đi liền với journal mode |
| `APP_DB__SQLITE_BUSY_TIMEOUT_SECONDS` | không | `5.0` | chờ bao lâu khi người khác đang giữ khoá ghi |

Hai PRAGMA nữa luôn bật, không có cờ tắt vì tắt là **lệch với Postgres**:
`foreign_keys=ON` (thiếu nó thì `ON DELETE CASCADE` chỉ nằm trong schema làm
cảnh) và `case_sensitive_like=ON` (thiếu nó thì `LIKE` bỏ qua hoa thường).

```bash
fam dev
```

Khởi động sẽ thấy:

```
db.sqlite_pragmas journal_mode=WAL synchronous=NORMAL busy_timeout=5000
db.connected      backend=sqlite
db.schema_ready   tables=['devices', 'users']
```

### Tốc độ ghi, và vì sao mặc định ở đây khác SQLite gốc

Mặc định gốc của SQLite ghi **68 dòng mỗi giây**. Không phải vì SQLite chậm —
vì mỗi lần commit nó fsync trọn vẹn xuống đĩa. Đo trên máy dev, một
`repo.save()` một dòng:

| journal_mode | synchronous | Tốc độ | Mỗi dòng |
|---|---|---|---|
| DELETE *(gốc SQLite)* | FULL | 68 ghi/s | 14,8 ms |
| WAL | FULL | 111 ghi/s | 9,0 ms |
| **WAL** | **NORMAL** *(mặc định ở đây)* | **1.376 ghi/s** | **0,73 ms** |
| gộp 3.000 dòng trong một transaction | | 7.644 ghi/s | 0,13 ms |

Đánh đổi của `NORMAL`: **cùng WAL thì mất điện KHÔNG hỏng file**, chỉ có thể
mất vài giao dịch cuối chưa kịp checkpoint. Cần bền vững tuyệt đối (dữ liệu tài
chính, không có nguồn phát lại) thì đặt `FULL` và chấp nhận 68 ghi/s.

WAL còn cho **đọc trong lúc đang ghi** — chế độ `DELETE` không có.

Đổi về `DELETE` khi file `.db` nằm trên **ổ mạng** (NFS/SMB): WAL cần shared
memory nên không chạy được ở đó.

### Nhiều nơi cùng ghi có an toàn không

An toàn, nhưng **không nhanh hơn**. SQLite chỉ cho MỘT người ghi tại một thời
điểm — người thứ hai **chờ** tới `busy_timeout` chứ không lỗi ngay. Đo được:

| Tình huống | Kết quả |
|---|---|
| 8 worker async cùng ghi | 1.190 ghi/s, **0 lỗi** |
| 8 worker `thread=True` qua `ctx.run` | 811 ghi/s, **0 lỗi** |
| 4 tiến trình (`fam run --workers 4`) | 897 ghi/s, **0 lỗi** |
| 12 tiến trình | 869 ghi/s, **0 lỗi** |
| một transaction giữ khoá ghi **quá 5 giây** | `database is locked` |

Hai điều rút ra:

1. **Tổng thông lượng không tăng theo số worker.** 12 tiến trình ghi cũng chỉ
   bằng 1. Thêm worker để phục vụ HTTP thì được, để ghi nhanh hơn thì không.
2. Lỗi `database is locked` chỉ xuất hiện khi một giao dịch **giữ khoá lâu hơn
   `busy_timeout`**. Nó không phải chuyện tranh chấp bình thường — nó là dấu
   hiệu có ai đó mở transaction rồi làm việc khác trong lúc còn giữ.

### Tắt đột ngột: `kill -9` và rút điện

Hai chuyện khác hẳn nhau, và trộn chúng lại là nguồn của gần hết những lời đồn
về "SQLite hỏng file".

**`kill -9` không nguy hiểm.** Giết tiến trình không đụng tới page cache của
nhân: dữ liệu đã ghi vẫn được nhân đẩy xuống đĩa như thường. Đo 75 lần giết
giữa lúc đang ghi liên tục, ba cấu hình khác nhau:

| Cấu hình | File hỏng | Mất giao dịch đã commit |
|---|---|---|
| WAL + NORMAL *(mặc định)* | **0/25** | **0/25** |
| DELETE + FULL | 0/25 | 0/25 |
| WAL + OFF | 0/25 | 0/25 |

**Rút điện thì khác**: mất luôn page cache, và lần ghi đang dở bị đứt giữa
chừng. Mô phỏng bằng cách làm đúng hai thứ đó với file trên đĩa — **cắt cụt**
WAL ở một chỗ ngẫu nhiên (phần chưa kịp xuống đĩa), và **đè byte rỗng** lên
đuôi WAL (lần ghi đứt nửa chừng):

| Cấu hình | Cắt cụt WAL | Đè rác đuôi WAL | Mất dữ liệu |
|---|---|---|---|
| **WAL + NORMAL** *(mặc định)* | **0/80 hỏng** | **0/20 hỏng** | 47/80 |
| DELETE + FULL | 0/20 hỏng | 1/20 hỏng | 0/20 |
| WAL + OFF | 1/80 hỏng | 0/20 hỏng | 53/80 |

Ba điều rút ra:

1. **Mặc định của khung không làm hỏng file** — 0 lần trong 100 phép thử.
   SQLite có checksum cho từng khung WAL, nên khung dở dang bị bỏ qua thay vì
   được đọc như thật.
2. **Nó CÓ mất giao dịch cuối**, và đây là chỗ dễ đánh giá thấp: không phải
   "mất một hai giao dịch". `synchronous=NORMAL` chỉ fsync WAL lúc
   *checkpoint*, mà SQLite chỉ checkpoint khi WAL đầy ~4 MB. Khoảng đang treo
   có thể là **hàng nghìn dòng** — trong phép đo có lần chỉ còn 93 trên 200
   dòng đã commit.
3. `synchronous=OFF` hỏng file **1 lần trong 80**, còn NORMAL thì 0. Một lần
   là mẫu nhỏ, không đủ để nói "OFF hỏng gấp bao nhiêu" — nhưng đủ để thấy nó
   ở phía sai của ranh giới, trong khi **không nhanh hơn NORMAL** (1.394 so với
   1.376 ghi/s). Không có lý do gì để chọn nó.

> Phép mô phỏng này **khắc nghiệt hơn thực tế**: nó cắt WAL ở một điểm bất kỳ
> từ 0% tới 100%, còn mất điện thật thì nhân đã kịp đẩy phần lớn WAL xuống đĩa
> (mặc định Linux gột page cache mỗi ~30 giây). Con số "hỏng" đáng tin; con số
> "mất dữ liệu" là chặn trên, thực tế nhẹ hơn.

Có một phép thử thứ ba — đè 512 byte rác vào **giữa** file `.db` — nhưng nó mô
phỏng **ghi lạc chỗ / lỗi đĩa** chứ không phải mất điện, vì mất điện chỉ làm
hỏng đúng trang đang được ghi. Kết quả vẫn đáng biết: DELETE hỏng 10/20 còn
WAL hỏng 0/20. Không phải WAL "chống lỗi đĩa" — mà ở chế độ WAL, file `.db`
chỉ bị ghi vào lúc checkpoint, nên nó nhỏ hơn và ít bị đụng tới hơn nhiều.

Chọn theo việc bạn đang làm:

| Bạn cần | Đặt | Giá phải trả |
|---|---|---|
| sự kiện camera, log, số đo — sinh lại được, hoặc mất vài giây cũng chịu được | *(mặc định)* | có thể mất tới một khoảng checkpoint khi mất điện |
| giao dịch tiền, đơn hàng — mất một dòng là mất thật | `APP_DB__SQLITE_SYNCHRONOUS=FULL` | 95 ghi/s thay vì 1.376 |
| dữ liệu vứt được | ~~`OFF`~~ | **đừng** — không nhanh hơn NORMAL, mà đo được hỏng file |

Trừ `OFF`, không lựa chọn nào ở đây làm hỏng file. Khác nhau chỉ là mất bao
nhiêu giao dịch cuối.

### Sao lưu: đừng chép mỗi file `.db`

Ở chế độ WAL, dữ liệu mới nhất nằm trong `app.db-wal` chứ chưa vào `app.db`.
File `-wal` phình tới ~4 MB rồi mới được gộp vào. Nên:

```bash
cp data/app.db backup.db          # SAI — mất phần còn trong -wal
```

Đo với 500 dòng vừa ghi: bản sao chỉ có **489**. Ba cách đúng:

```bash
sqlite3 data/app.db ".backup backup.db"      # an toàn cả khi app đang chạy
sqlite3 data/app.db "VACUUM INTO 'backup.db'"
cp data/app.db data/app.db-wal data/app.db-shm  ...   # chép CẢ BA
```

Sau khi app **tắt sạch** thì `-wal` về 0 byte và chép mỗi `.db` là đủ — khung
tự gộp WAL lúc đóng kết nối. Nhưng đừng dựa vào điều đó: app bị `kill -9` thì
`-wal` còn nguyên đó.

Cùng lý do, xoá database là xoá cả ba file:

```bash
rm -f data/app.db*        # không phải `rm -f data/app.db`
```

Cần hơn 1.000 ghi/s bền vững, hoặc nhiều máy cùng ghi, thì đó là lúc chuyển
sang [PostgreSQL](#3-postgresql) — không phải lúc đi chỉnh PRAGMA tiếp.

**Lưu ý riêng của SQLite:** kiểu `DATETIME` không lưu múi giờ (MongoDB cũng
vậy). Template gắn lại UTC lúc đọc nên API của cả ba driver đều trả dạng
`2026-08-21T02:52:02.049410Z` như nhau — nhưng nếu bạn truy vấn thẳng vào file
`.db` bằng công cụ khác thì sẽ thấy giá trị trần không có múi giờ.

Xoá và làm lại từ đầu:

```bash
rm -f data/app.db* && fam dev
```

---

## 3. PostgreSQL

```bash
fam install postgres
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
[bảng đầy đủ](#các-biến-điều-chỉnh).

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
fam dev
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
- Chạy nhiều worker được: `fam run --workers 4`.

---

## 4. MongoDB

Có [trang riêng: mongodb.md](mongodb.md) — cài đặt, bộ lệnh dùng được,
và những thứ bên đó KHÔNG có (query builder, transaction, migration).

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

Đổi **độ dài** cũng vậy — khai `column(length=64)` cho cột đang là `VARCHAR(8)`
thì bạn nhận cảnh báo chứ cột không tự nới ra:

```
db.column_type_mismatch  column='cameras.code: VARCHAR(8) -> VARCHAR(64)'
```

Cột đã có `VARCHAR(50)` mà entity để `str` trơn thì **không** bị kêu: khung chỉ
so độ dài khi entity có khai. Xem
[entity.md](entity.md#độ-dài-cột-chữ-varchar50-và-text).

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

MongoDB không có schema cố định nên không cần migrate gì cả — xem
[mongodb.md](mongodb.md#khai-báo-entity).

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
| `APP_DB__QUERY_TIMEOUT_SECONDS` | `15` | Chờ tối đa cho **một câu truy vấn đã gửi đi** | Khác `CONNECT_TIMEOUT`: database treo giữa lúc đang chạy câu lệnh thì connection vẫn "mở", chỉ biến này cứu được. Tăng nếu có báo cáo chạy lâu |
| `APP_DB__STARTUP_RETRIES` | `10` | Số lần thử lại lúc khởi động | Tăng nếu database của bạn khởi động chậm hơn 10 giây |
| `APP_DB__STARTUP_RETRY_DELAY_SECONDS` | `1` | Nghỉ giữa hai lần thử | nt |
| `APP_DB__CIRCUIT_BREAKER` | `true` | Ngắt mạch khi database hỏng liên tiếp, thay vì để mọi request cùng chờ hết timeout. Không áp dụng cho driver `memory` | Hầu như không bao giờ tắt — xem [operations.md](operations.md#circuit-breaker-và-hạn-thời-gian) |
| `APP_DB__CIRCUIT_FAILURE_THRESHOLD` | `5` | Bao nhiêu lỗi **kết nối** liên tiếp thì ngắt | Giảm để chặn nhanh hơn, tăng nếu mạng hay chớp |
| `APP_DB__CIRCUIT_RESET_SECONDS` | `10` | Ngắt bao lâu rồi thử lại một request để dò | Tăng nếu database cần lâu hơn để hồi |

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
fam install postgres   # thay khối trong .env
fam dev
```

Dữ liệu **không** tự chuyển giữa các backend. Cần chuyển thì phải xuất/nhập thủ công.

---

## Test trên database thật

Mặc định `fam test` chạy trên backend `memory` (nhanh, không cần server). Muốn
chạy đúng bộ test đó trên database thật:

```bash
TEST_SQLITE=1 fam test
TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app' fam test
TEST_MONGO_DSN='mongodb://127.0.0.1:27017' fam test      # xem mongodb.md
```

Driver nào chưa cài thư viện hoặc chưa có server sẽ **SKIP** chứ không FAIL, nên
bộ test chạy được trên máy chỉ cài một driver.

---

## Thêm backend mới

1. Viết class cài đủ bộ method của `DatabaseBackend`
   ([`fastapi_modular/infrastructure/database/base.py`](../fastapi_modular/infrastructure/database/base.py)).
2. Thêm một nhánh trong `create_backend()`, **import bên trong hàm**.
3. Thêm một extra `<tên>` trong `pyproject.toml` và một khối trong `fam env`.
4. Thêm một `pytest.param` vào `tests/test_drivers.py` — bộ test CRUD dùng chung
   sẽ tự chạy cho backend mới.
