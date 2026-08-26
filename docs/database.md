# Hướng dẫn database

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Khai một bảng dữ liệu mới" | [Khai báo entity](#khai-báo-entity) |
| "Sự kiện phải thuộc về một camera" | [Khoá ngoại](#khoá-ngoại-nối-hai-bảng-với-nhau) |
| "**Xoá camera thì xoá luôn sự kiện của nó**" | [`CASCADE`](#cascade--xoá-cha-thì-con-đi-theo) |
| "**Xoá khu vực thì camera ở lại, chỉ mất chỗ gắn**" | [`SET NULL`](#set-null--con-ở-lại-mất-chỗ-gắn) |
| "**Xoá camera thì thẻ về giá trị mặc định**" | [`SET DEFAULT`](#set-default--về-giá-trị-bạn-đặt-sẵn) |
| "Còn hoá đơn thì KHÔNG cho xoá khách hàng" | [`RESTRICT`](#restrict--chặn-không-cho-xoá) |
| "Đọc/ghi dữ liệu trong service" | [Dùng Repository trong code](#dùng-repository-trong-code) |
| "Lọc lớn hơn, nhỏ hơn, NULL, nối bảng" | [Truy vấn phức tạp](#truy-vấn-phức-tạp--join-lớnbé-null) |
| "**Điều kiện này HOẶC điều kiện kia**" | [OR và AND lồng nhau](#or-và-and-lồng-nhau) |
| "**Mỗi camera có bao nhiêu sự kiện**" | [Gộp nhóm](#gộp-nhóm-đếm-tính-trung-bình) |
| "**Chỉ lấy camera có hơn 5 sự kiện**" | [HAVING](#having--lọc-theo-kết-quả-gộp) |
| "Camera cha của camera này tên gì" | [Nối bảng với chính nó](#nối-bảng-với-chính-nó-self-join) |
| "Chọn database nào" | [Cách chọn driver](#cách-chọn-driver) |
| "Thêm/xoá trường mà không mất dữ liệu" | [Tự chỉnh schema](#tự-chỉnh-schema-thêm--xoá-trường) |

---

Template hỗ trợ **4 backend**: `memory` (mặc định), `sqlite`, `postgres`, `mongodb`.
Tại một thời điểm chỉ **một** backend được dùng, và **chỉ thư viện của backend đó
cần được cài** — chọn Postgres thì máy không cần `aiosqlite` hay `motor`.

Điều này làm được vì mọi `import sqlalchemy` / `import motor` nằm bên trong hàm
`create_backend()` ở [`fastapi_modular/infrastructure/database/factory.py`](../fastapi_modular/infrastructure/database/factory.py),
chứ không ở đầu file. Chọn driver chưa cài thư viện sẽ nhận thông báo:

```
Driver database 'postgres' cần thư viện 'sqlalchemy' nhưng chưa cài.
Chạy: fam install postgres
```

---

## Bảng tra nhanh

| | memory | sqlite | postgres | mongodb |
|---|---|---|---|---|
| Lệnh cài | (không cần) | `fam install sqlite` | `fam install postgres` | `fam install mongodb` |
| Thư viện | (không cần) | `fastapi-modular[sqlite]` | `fastapi-modular[postgres]` | `fastapi-modular[mongodb]` |
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

```bash
fam install mongodb
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
fam dev
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

## Khai báo entity

Một entity là một bảng. Viết vào `src/api/<module>/entities/`:

```python
from dataclasses import dataclass, field
from datetime import datetime

from fastapi_modular import Entity, entity
from fastapi_modular.core.clock import utcnow


@entity()
@dataclass(slots=True)
class Camera(Entity):
    id: str                                              # BẮT BUỘC, là khoá chính
    name: str
    ip: str
    is_active: bool = True
    fps: int = 25
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
```

Chạy `fam dev` là bảng được tạo. Log khởi động:

```
db.schema_ready  mode=create  tables=['cameras']
```

**Không thấy tên bảng của bạn** nghĩa là file entity chưa được import — nó phải
nằm trong `src/api/<module>/entities/` để khung quét thấy.

### Bốn quy ước phải biết

**`id: str` là bắt buộc và là khoá chính.** Để trống lúc `save()` thì khung tự
sinh (UUID):

```python
cam = await repo.save(Camera(id="", name="Cổng chính", ip="10.0.0.1"))
print(cam.id)     # "3f2a...", khung vừa sinh
```

**`created_at` / `updated_at` là tuỳ chọn, nhưng có thì khung tự lo.** Mỗi lần
`save()` đóng dấu lại `updated_at` — không có chỗ nào quên, vì mọi đường ghi
đều đi qua đó. Tương đương `@UpdateDateColumn` của TypeORM. Bản ghi mới thì hai
mốc bằng nhau, nên "chưa từng sửa" nhận ra được bằng `created_at == updated_at`.

**Kế thừa `Entity` để viết điều kiện bằng toán tử thường.** Đây là thứ duy
nhất `Entity` làm: `Camera.fps >= 25` cho ra một điều kiện thay vì lỗi. Không
kế thừa cũng chạy bình thường, chỉ là phải viết `.where(fps__gte=25)` hoặc
`F(Camera).fps >= 25`. Nó **không** thêm method nào vào entity, và không làm
đối tượng nặng thêm một byte nào — xem [Tra cứu](#tra-cứu).

**Tên bảng mặc định là tên class viết thường + `s`**: `Camera` → `cameras`. Đổi
bằng `@entity(name="camera_list")`.

**Kiểu trường quyết định kiểu cột.** Năm kiểu được ánh xạ thật:

| Khai trong Python | Cột trong SQL |
|---|---|
| `str` | `VARCHAR` |
| `int` | `INTEGER` |
| `float` | `FLOAT` |
| `bool` | `BOOLEAN` |
| `datetime` | `TIMESTAMP WITH TIME ZONE` |
| `Enum` | `VARCHAR(64)`, lưu bằng `.value` cho dễ đọc và dễ migrate |
| *(kiểu khác)* | `VARCHAR` |

Trường cho phép trống thì khai `| None` và cho mặc định `None`:

```python
zone_id: str | None = None
```

### Duy nhất và index

Khai trên `@entity`, và ràng buộc được tạo **dưới database** chứ không chỉ kiểm
trong service — kiểm rồi mới ghi là một cuộc đua, hai request đồng thời đều thấy
"chưa có" rồi cùng ghi:

```python
@entity(
    unique=["serial", ("owner_id", "name")],   # cụm: duy nhất theo CẶP
    indexes=[("owner_id", "status")],          # cụm: hay lọc theo cả hai
)
```

Ghi trùng thì nhận lỗi **409**, không phải 500. Chi tiết thứ tự cột trong cụm:
[Ràng buộc duy nhất và index](#ràng-buộc-duy-nhất-và-index).

---

## Khoá ngoại: nối hai bảng với nhau

Sự kiện phải thuộc về một camera. Camera thuộc về một khu vực. Khai bằng
`reference(...)` đặt ngay trên cột:

```python
from fastapi_modular import Entity, reference


@entity()
@dataclass(slots=True)
class Event(Entity):
    id: str
    label: str
    camera_id: str = field(metadata=reference(Camera, on_delete="CASCADE"))
    created_at: datetime = field(default_factory=utcnow)
```

Sinh ra khoá ngoại **thật** trong database:

```sql
FOREIGN KEY(camera_id) REFERENCES cameras (id) ON DELETE CASCADE
```

Đặt trên cột chứ không phải trên `@entity(...)`: khoá ngoại nói về **một cột cụ
thể**, để cạnh nhau thì đọc một dòng là biết. TypeORM và Django đều đặt ở đây.

### Nó chặn dữ liệu rác ngay lúc GHI

```python
await events.save(Event(id="", label="person", camera_id="khong-co-that"))
# -> lỗi 409: không có Camera nào mang id đó
```

Không cần viết dòng kiểm tra nào. Cả `memory` lẫn SQL đều chặn như nhau, nên
`fam test` bắt được đúng lỗi mà production sẽ gặp.

`None` thì được — nó nghĩa là "chưa gắn", không phải "gắn sai":

```python
zone_id: str | None = field(default=None, metadata=reference(Zone, on_delete="SET NULL"))
```

### Xoá cha thì con thế nào?

Đây là câu hỏi **nghiệp vụ**, không phải chi tiết kỹ thuật — nên khung bắt bạn
nói rõ thay vì đoán hộ. Bốn lựa chọn:

| Bạn muốn | Khai | Xoá cha thì |
|---|---|---|
| con không còn nghĩa gì nữa | `on_delete="CASCADE"` | **xoá luôn con** |
| con vẫn có nghĩa, chỉ mất chỗ gắn | `on_delete="SET NULL"` | con ở lại, cột về `NULL` |
| con phải trỏ đi đâu đó | `on_delete="SET DEFAULT"` | con ở lại, cột về giá trị mặc định |
| con là dữ liệu không được mất | `on_delete="RESTRICT"` *(mặc định)* | **chặn, không cho xoá cha** |

Mặc định là `RESTRICT` — cố ý chọn cái an toàn nhất. Không khai gì thì bạn được
báo lỗi, chứ không mất dữ liệu trong im lặng.

#### `CASCADE` — xoá cha thì con đi theo

```python
camera_id: str = field(metadata=reference(Camera, on_delete="CASCADE"))
```

```python
await cameras.delete("cam-01")
# mọi Event có camera_id = "cam-01" biến mất theo
```

Dùng khi con **không tồn tại độc lập được**: sự kiện của một camera đã gỡ, dòng
chi tiết của một hoá đơn đã xoá, ảnh thu nhỏ của một bài viết đã xoá.

> **Cẩn thận: nó lan theo chuỗi.** Xoá khu vực → xoá camera (nếu camera cũng khai
> CASCADE) → xoá sự kiện. Một lệnh `delete()` có thể quét sạch một nhánh. Muốn
> chuỗi dừng lại ở đâu thì khai `SET NULL` hoặc `RESTRICT` ở đúng bậc đó.

#### `SET NULL` — con ở lại, mất chỗ gắn

```python
zone_id: str | None = field(default=None, metadata=reference(Zone, on_delete="SET NULL"))
```

```python
await zones.delete("tang-1")
# camera VẪN CÒN, chỉ là zone_id thành None
```

Dùng khi con **vẫn có nghĩa khi không có cha**: camera vẫn là camera dù khu vực
bị xoá; bài viết vẫn còn dù chuyên mục bị xoá.

Cột phải cho phép `None` — khai `str | None` và cho mặc định `None`. Khai
`str` rồi đòi `SET NULL` là mâu thuẫn, database sẽ từ chối.

#### `SET DEFAULT` — về giá trị bạn đặt sẵn

```python
camera_id: str = field(default="chua-gan", metadata=reference(Camera, on_delete="SET DEFAULT"))
```

```python
await cameras.delete("cam-01")
# thẻ vẫn còn, camera_id = "chua-gan"
```

Dùng khi bạn muốn con trỏ về một **bản ghi thay thế** thay vì để trống — ví dụ
một camera "chưa phân loại". Trường bắt buộc phải có giá trị mặc định
(`default=...`), nếu không khung báo lỗi ngay lúc khai báo.

#### `RESTRICT` — chặn, không cho xoá

```python
customer_id: str = field(metadata=reference(Customer, on_delete="RESTRICT"))
```

```python
await customers.delete("kh-01")
# -> lỗi 409: "Không xoá được Customer vì còn 3 Invoice trỏ tới nó"
```

Đây là **mặc định**, và đúng cho dữ liệu tài chính hay pháp lý: hoá đơn không
được biến mất chỉ vì có người bấm nhầm nút xoá khách hàng. Muốn xoá thật thì
phải dọn con trước — và đó chính là điều bạn muốn phải xảy ra một cách có ý thức.

### Ba điều phải biết trước

**SQLite tắt khoá ngoại mặc định — khung tự bật.** Đây là cái bẫy đắt nhất của
SQLite: không bật `PRAGMA foreign_keys=ON` thì DDL vẫn ghi `ON DELETE CASCADE`
nhưng nó **chỉ nằm đó làm cảnh**. Đo được: xoá cha xong con vẫn còn nguyên,
không lỗi, không cảnh báo, chỉ là dữ liệu mồ côi. Khung bật sẵn cho **mọi
connection** trong pool — kiểm bằng `PRAGMA foreign_keys` phải trả về `1`.

**MongoDB không có khoá ngoại.** Khung tự dọn bằng nhiều lệnh nối nhau, nên nó
**không nguyên tử**: tiến trình chết giữa chừng thì còn lại cha đã xoá mà con
chưa dọn. Cần bảo đảm thật thì dùng postgres.

| Backend | Ai áp ràng buộc |
|---|---|
| `postgres`, `sqlite` | **chính database**, trong cùng transaction |
| `memory` | khung, để `fam test` cho cùng kết quả |
| `mongodb` | khung, **không nguyên tử** — xem trên |

**Xoá nhiều cha một lúc cũng áp ràng buộc.** `delete_where(...)` chạy đúng luật
như `delete(id)`, không phải đường tắt bỏ qua khoá ngoại.

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
| `query()` | Builder cho JOIN, lớn/bé, NULL — xem [mục dưới](#truy-vấn-phức-tạp--join-lớnbé-null) |

### Hai quy ước dễ vấp

**`None` nghĩa là "không lọc"**, không phải "bằng NULL":

```python
await repo.find(owner_id=None)     # trả về TẤT CẢ, không phải bản ghi có owner_id NULL
await repo.query().where(owner_id__isnull=True).all()    # đây mới là lọc NULL
```

**`match=` chạy trong Python, KHÔNG đẩy được xuống database.** Backend lấy **cả
bảng** về rồi mới lọc, và `limit` cũng chỉ cắt sau khi đã lấy về — nên với bảng
lớn thì vừa chậm vừa tốn RAM.

Gần như mọi thứ trước đây phải dùng `match=` thì nay viết được bằng
[query builder](#truy-vấn-phức-tạp--join-lớnbé-null), và nó chạy dưới database:

| Cần gì | Đừng | Hãy |
|---|---|---|
| lớn hơn / nhỏ hơn | `match=lambda o: o.score >= 0.8` | `.query().where(score__gte=0.8)` |
| bằng NULL | `match=lambda o: o.owner_id is None` | `.query().where(owner_id__isnull=True)` |
| nằm trong danh sách | `match=lambda o: o.label in [...]` | `.query().where(label__in=[...])` |
| nối bảng khác | *(không làm được)* | `.query().join(Camera)` |

`match=` chỉ còn đúng cho điều kiện không có trong SQL — ví dụ gọi một hàm Python
để tính. Khi dữ liệu lớn, cách tốt hơn là chuẩn hoá lúc ghi (lưu sẵn
`email_lower`) rồi lọc bằng cột đó.

---

## Truy vấn phức tạp — JOIN, lớn/bé, NULL

`find()` chỉ so bằng (`=`). Cần hơn thế thì dùng `repo.query()`.

### Làm thế nào

```python
from fastapi_modular.infrastructure.database import F, or_

events = await (
    repo.query()
    .join(Camera)                              # cột nối đọc từ `reference(Camera)`
    .where(score__gte=0.8, label="person")     # >= và =
    .where(reviewed_at__isnull=True)           # IS NULL
    .where(camera__name__like="Cổng%")         # lọc theo cột bảng đã join
    .order_by("-created_at")                   # dấu trừ = giảm dần
    .limit(20)
    .all()
)
```

Kết quả là `list[Event]` như `find()`. **JOIN ở đây để LỌC**, không đổi kiểu trả
về — nên `.score`, `.label` vẫn gõ được như thường.

Không có gì chạy cho tới khi bạn gọi `.all()`, `.first()`, `.count()` hoặc
`.exists()`.

**Cặp `()` bọc ngoài chỉ để xuống dòng**, không phải cú pháp của builder. Viết
một dòng thì bỏ luôn:

```python
return await self._repo.query().join(Camera).where(label="person").all()
```

Muốn nhiều dòng mà không thích dấu ngoặc thì đặt tên cho câu truy vấn — đọc còn
dễ hơn, và tách được điều kiện theo nhánh `if`:

```python
query = self._repo.query().join(Camera).where(label="person")
if zone:
    query = query.where(camera__zone=zone)
return await query.order_by("-created_at").limit(20).all()
```

### Xem câu SQL sinh ra

Đây là cách nhanh nhất khi truy vấn cho kết quả lạ — nhanh hơn đọc lại builder:

```python
print(repo.query().join(Camera).where(score__gte=0.8).limit(5).sql())
```

```sql
SELECT events.id, events.camera_id, events.label, events.score, ...
FROM events JOIN cameras ON events.camera_id = cameras.id
WHERE events.score >= 0.8 ORDER BY events.created_at DESC
LIMIT 5 OFFSET 0
```

Mọi thứ — `JOIN`, `WHERE`, `ORDER BY`, `LIMIT` — đều **chạy dưới database**.
Không có bước nào lọc bằng Python.

### Toán tử

Viết ở đuôi tên cột, sau hai dấu gạch dưới:

| Viết | SQL |
|---|---|
| `score=0.8` | `= 0.8` |
| `score__ne=0.8` | `!= 0.8` |
| `score__gt` `__gte` `__lt` `__lte` | `>` `>=` `<` `<=` |
| `label__in=["a","b"]` / `__nin=` | `IN` / `NOT IN` |
| `reviewed_at__isnull=True` / `=False` | `IS NULL` / `IS NOT NULL` |
| `score__between=[0.5, 0.9]` | `BETWEEN 0.5 AND 0.9` |
| `name__like="Cổng%"` / `__ilike=` | `LIKE` / `ILIKE` |
| `name__startswith` `__endswith` `__contains` | `LIKE` với `%` đặt sẵn |

Tiền tố là tên bảng đã `join`, mặc định là tên class viết thường:
`camera__name__like="Cổng%"`. Đặt tên khác bằng `.join(Camera, ..., alias="cam")`.

### Toán tử thường thay cho đuôi `__gte`

Entity kế thừa `Entity` thì viết thẳng phép so sánh:

```python
await repo.query().where(Event.score >= 0.8, Event.label == "person").all()
await repo.query().where(Event.reviewed_at == None).all()        # IS NULL
```

Giống hệt `.where(score__gte=0.8, label="person")`, chọn kiểu nào là tuỳ bạn.
Cái được: IDE gợi ý tên cột, và gõ sai tên là hỏng ngay lúc import.

Entity **chưa** kế thừa `Entity` thì lấy cột bằng `F(Event)` — y hệt, chỉ dài hơn:

```python
E = F(Event)
await repo.query().where(E.score >= 0.8).all()
```

**Chưa kế thừa mà cứ viết `Event.score == 0.8` thì không phải lỗi cú pháp** —
Python cho so sánh hai đối tượng bất kỳ, kết quả là `False`, và câu truy vấn
lặng lẽ sai. Builder chặn ngay chỗ đó:

```
Điều kiện là False chứ không phải phép so sánh. Viết `Event.score == 0.8` chỉ
ra điều kiện khi entity kế thừa `Entity` (`class Event(Entity):`); chưa kế thừa
thì dùng `F(Event).score == 0.8` hoặc `.where(score=0.8)`.
```

**IDE có thể gạch đỏ một dòng hoàn toàn đúng.** PyCharm/mypy đọc khai báo
`score: float` rồi kết luận `Event.score > 0.8` là `bool` — chúng không thấy
metaclass, thứ chỉ tồn tại lúc chạy. Cảnh báo *"Expected type 'Condition', got
'bool' instead"* đã hết vì `.where()` khai tham số là `Any`, nhưng **mypy chặt**
vẫn còn kêu hai câu không sửa được từ phía thư viện:

```
error: Cannot access instance-only attribute "score" on class object
error: "score" in __slots__ conflicts with class variable access
```

Chạy mypy trong CI thì dùng `F(Event).score > 0.8` (hàm `F` trả `Any` nên im
lặng) hoặc kiểu ngắn `.where(score__gt=0.8)`. Cả hai chạy y hệt.

### OR và AND lồng nhau

Nhiều `.where()` nối với nhau bằng **AND**. Muốn OR thì gọi `or_(...)`:

```python
# score >= 0.8 AND (label = 'fire' OR label = 'car')
await (repo.query()
       .where(Event.score >= 0.8)
       .where(or_(Event.label == "fire", Event.label == "car"))
       .all())
```

**Hai điều kiện rồi OR ở giữa** — ca hay gặp nhất, `and_` lồng trong `or_`:

```python
# (label = 'person' AND score >= 0.9) OR (label = 'fire' AND score >= 0.3)
await (repo.query()
       .where(or_(and_(Event.label == "person", Event.score >= 0.9),
                  and_(Event.label == "fire",   Event.score >= 0.3)))
       .all())
```

Sinh ra đúng câu này, và dấu ngoặc do database tự hiểu theo thứ tự ưu tiên
`AND` trước `OR`:

```sql
WHERE events.label = 'person' AND events.score >= 0.9
   OR events.label = 'fire' AND events.score >= 0.3
```

Viết bằng `&` `|` `~` cũng được, kết quả y hệt — nhưng **phải có ngoặc quanh
từng phép so sánh**, vì trong Python `&` ưu tiên cao hơn `>=`:

```python
.where(((Event.label == "person") & (Event.score >= 0.9))
       | ((Event.label == "fire") & (Event.score >= 0.3)))
```

Thiếu ngoặc thì `Event.score >= 0.9 & Event.label` chạy trước và bạn nhận
`TypeError: unsupported operand type(s) for &`. Không thích đếm ngoặc thì dùng
`and_()` / `or_()` — không có bẫy nào.

`~` là NOT: `.where(~or_(Event.label == "fire", Event.label == "car"))`.

### So CỘT với CỘT

```python
await (repo.query()
       .join(Camera)
       .where(Event.score > Camera.threshold)   # events.score > cameras.threshold
       .all())
```

### Nối theo cột nào

Khai `reference(Camera)` ở entity rồi thì **không phải nói gì thêm** — builder
đọc khoá ngoại đó ra:

```python
.join(Camera)
```

Muốn nói rõ thì đưa thẳng **cột thật** vào. Đây là cách nên dùng khi bảng có hơn
một cột trỏ sang cùng một bảng, vì gõ sai tên là hỏng ngay lúc import, không đợi
tới lúc câu lệnh chạy:

```python
.join(Camera, on=Event.camera_id)           # Event.camera_id = Camera.id
.join(Event,  on=Event.camera_id)           # đảo chiều, một-nhiều: Camera.id = Event.camera_id
```

`Event.camera_id` viết được là nhờ `@dataclass(slots=True)`. Entity của bạn
không khai `slots=True` thì dùng `F(Event).camera_id` — y hệt, chỉ dài hơn.

Ba cách cũ vẫn chạy nguyên:

```python
.join(Camera, on="camera_id")               # chuỗi
.join(Camera, on=("camera_id", "id"))       # nói rõ cả hai vế
.join(Camera, on=F(Event).camera_id == F(Camera).id)
```

**Chưa khai `reference` mà `.join(X)` trần thì báo lỗi, không đoán.** Lỗi nói
đúng cái thiếu:

```
Không biết nối Camera vào đâu: giữa nó và Event chưa có khoá ngoại nào khai
bằng `reference(...)`. Nói rõ bằng `on=Camera.ten_cot` hoặc `on="ten_cot"`.
```

Hai cột cùng trỏ sang một bảng (`vao_id`, `ra_id` cùng trỏ `Camera`) cũng vậy —
liệt kê cả hai rồi bắt bạn chọn.

### Bốn kiểu nối

| Viết | Ra SQL | Dùng khi |
|---|---|---|
| `.join(Camera)` | `JOIN` | chỉ cần dòng khớp cả hai bên |
| `.join(Event, outer=True)` | `LEFT JOIN` | giữ cả dòng **không** có bên phải |
| `.join(Event, full=True)` | `FULL OUTER JOIN` | giữ cả hai bên; bắt buộc `.select(...)` |
| `.join(Camera, alias="cha", on=…)` | `JOIN … AS cha` | nối bảng với **chính nó** |

**`RIGHT JOIN` không có, và không thiếu.** Builder trả về entity của bảng GỐC,
nên "RIGHT JOIN" chỉ là đổi bảng nào làm gốc rồi dùng `outer=True`:

```python
# thay vì  events.query().join(Camera, right=True)
await cameras.query().join(Event, outer=True)...
```

Chọn bảng gốc là việc bạn phải làm dù thế nào — nó quyết định bạn nhận về cái
gì — nên chọn xong là hết chuyện RIGHT.

**`LEFT JOIN` là cách tìm "cái nào còn trống":**

```python
# camera CHƯA có sự kiện nào
await (cameras.query()
       .join(Event, outer=True)
       .where(F(Event).id.is_null())
       .all())
```

**`FULL JOIN` bắt buộc `.select(...)`**, vì nó sinh cả những dòng không có bản
ghi nào bên bảng gốc — trả về một `Camera` toàn `None` thì chỉ là bịa. Không
có `.select` thì báo lỗi ngay chứ không trả rác. SQLite phải từ 3.39 trở lên
mới có `FULL OUTER JOIN`.

### Nối bảng với chính nó (self join)

Camera có `parent_id` trỏ sang một camera khác. Cùng một bảng đóng hai vai, nên
phải đặt tên cho vai thứ hai bằng `alias=`, và lấy cột của nó bằng
`F(Camera, "cha")`:

```python
Cha = F(Camera, "cha")

rows = await (cameras.query()
              .join(Camera, on=Camera.parent_id, alias="cha")
              .select("id", "name", ten_cha=Cha.name)
              .where(Cha.zone == "Tầng 1")
              .all())
```

```sql
SELECT cameras.id, cameras.name, cha.name AS ten_cha
FROM cameras JOIN cameras AS cha ON cameras.parent_id = cha.id
WHERE cha.zone = 'Tầng 1'
```

**`alias=` ở `.join()` và `F(Camera, "cha")` phải trùng chuỗi.** Lệch nhau thì
báo lỗi kèm danh sách tên bảng đang có, không sinh câu lệnh sai.

Kiểu ngắn cũng dùng alias làm tiền tố: `.where(cha__zone="Tầng 1")`.

### Gộp nhóm: đếm, tính trung bình

```python
from fastapi_modular.infrastructure.database import avg, count, max_, min_, sum_

rows = await (events.query()
              .group_by(Event.camera_id)
              .select("camera_id", so_luong=count(), diem_tb=avg(Event.score))
              .order_by(count().desc())
              .all())
# [{"camera_id": "c1", "so_luong": 12, "diem_tb": 0.83}, ...]
```

`group_by` **bắt buộc đi với `.select(...)`**: sau khi gộp thì một dòng không
còn là một bản ghi nữa. Không có `.select` thì báo lỗi chứ không trả entity bịa.

| Hàm | SQL | Ghi chú |
|---|---|---|
| `count()` | `count(*)` | đếm dòng |
| `count(Event.label)` | `count(label)` | **bỏ qua NULL** |
| `count(Event.label, distinct=True)` | `count(DISTINCT label)` | đếm giá trị khác nhau |
| `sum_(Event.score)` | `sum(score)` | |
| `avg(Event.score)` | `avg(score)` | |
| `min_(...)` `max_(...)` | `min` `max` | |

Tên có gạch dưới (`sum_`, `min_`, `max_`) vì `sum`, `min`, `max` là hàm sẵn có
của Python.

**Nhóm rỗng cho `NULL` chứ không phải 0.** `sum_` của một nhóm không có dòng
nào là `None` — đúng luật SQL, và backend `memory` giữ y hệt để `fam test`
không nói dối. Chỉ `count()` mới ra 0.

**Không có `group_by` mà vẫn dùng hàm gộp** thì cả bảng là một nhóm:

```python
await events.query().select(tong_so=count(), diem_tb=avg(Event.score)).all()
# [{"tong_so": 1042, "diem_tb": 0.77}]
```

### HAVING — lọc theo kết quả gộp

```python
# camera nào có hơn 5 sự kiện
rows = await (events.query()
              .group_by(Event.camera_id)
              .select("camera_id", so_luong=count())
              .having(count() > 5)
              .all())
```

**`where` bỏ bớt DÒNG trước khi gộp, `having` bỏ bớt NHÓM sau khi gộp.** Đây là
chỗ nhầm nhiều nhất, và hai cách cho ra con số khác hẳn nhau:

```python
# "camera có hơn 5 sự kiện ĐIỂM CAO"  -> lọc dòng trước, rồi đếm
.where(Event.score >= 0.8).having(count() > 5)

# "camera có hơn 5 sự kiện, và điểm trung bình cao" -> đếm hết, rồi lọc nhóm
.having(count() > 5, avg(Event.score) >= 0.8)
```

Nhiều điều kiện trong một `having` nối với nhau bằng AND, y như `where`.
`or_(...)` dùng được ở đây luôn.

`.count()` trên một truy vấn có `group_by` đếm **số nhóm**, đúng như
`SELECT count(*) FROM (...)` của SQL.

### Lấy cột của bảng đã join

Mặc định chỉ trả entity gốc. Cần cột bảng kia thì nói rõ — khi đó trả `list[dict]`:

```python
rows = await (repo.query()
              .join(Camera)
              .select("id", "score", camera_name=Camera.name)
              .all())
# [{"id": "e1", "score": 0.95, "camera_name": "Cổng chính"}]
```

`select` và `order_by` nhận cột thật y như `join`: `select(Event.id)`,
`order_by(Event.score)`.

### Lưu ý

**Join một-nhiều thì nhớ `.distinct()`.** Một camera có 10 sự kiện thì camera đó
hiện 10 lần — đúng theo SQL, nhưng thường không phải ý bạn:

```python
await cameras.query().join(Event).distinct().all()
```

**`count()` là `SELECT count(*)` thật**, không kéo dòng nào về. Đừng viết
`len(await q.all())`.

**So sánh với NULL luôn cho sai, giống hệt SQL.** `where(reviewed_at__gt=...)`
bỏ qua mọi dòng có `reviewed_at` NULL. Backend `memory` giữ y hệt luật này, nên
`fam test` và production cho cùng kết quả.

**MongoDB chưa dùng được.** Nó ném lỗi nói rõ vì sao: Mongo có `$lookup` nhưng
ngữ nghĩa join lệch đủ nhiều để một bản giả lập sẽ đúng ở demo và sai ở
production. Dùng `find()` cho truy vấn một collection, hoặc đổi sang
postgres/sqlite.

| Backend | Query builder |
|---|---|
| `postgres`, `sqlite` | đầy đủ, sinh SQL thật |
| `memory` | đầy đủ, tính bằng Python — để `fam test` chạy được, cỡ O(n×m) nên chỉ hợp với dữ liệu test |
| `mongodb` | **không** |

### Tra cứu

| Dựng | |
|---|---|
| `.join(Entity)` | nối bảng, cột lấy từ `reference(...)` |
| `.join(Entity, outer=True)` · `full=True` | LEFT JOIN · FULL OUTER JOIN (cần `.select`) |
| `.join(Entity, alias="cha")` | nối bảng với chính nó; lấy cột bằng `F(Entity, "cha")` |
| `.group_by(X.cot)` | gộp nhóm; bắt buộc kèm `.select(...)` |
| `.having(count() > 5)` | lọc theo kết quả gộp |
| `.join(Entity, on=…, outer=False, alias="")` | `on=` nhận `Entity.cot`, `"cot"`, `("cot","cot_kia")`, hoặc cả một điều kiện |
| `.where(*điều_kiện, **kwargs)` | nhiều lần `where` nối bằng AND |
| `.order_by("-created_at")` | dấu trừ = giảm dần; nhận cả `X.cot` và `F(X).cot.desc()` |
| `.limit(n)` · `.offset(n)` · `.distinct()` | |
| `.select("id", ten_khac=X.cot)` | đổi kiểu trả về sang `list[dict]` |
| `.select(so=count(), tb=avg(X.cot))` | hàm gộp, phải đặt tên |

| Cột | |
|---|---|
| `Event.score` | cần `class Event(Entity)` |
| `F(Event).score` | không cần gì |
| `F(Event, "cha").score` | cột của bảng đã đặt `alias="cha"` |
| `"score"` | chỉ ở `on=`, `group_by`, `order_by`, `select` — chỗ đã biết bảng |

| Hàm gộp | |
|---|---|
| `count()` · `count(X.cot)` · `count(X.cot, distinct=True)` | `count(*)` · bỏ NULL · `DISTINCT` |
| `sum_(X.cot)` · `avg(X.cot)` · `min_(X.cot)` · `max_(X.cot)` | nhóm rỗng ra `None`, không phải 0 |

**Giá của việc kế thừa `Entity`** — chỉ đọc nếu bạn đang cân nhắc có nên kế thừa
hay không. Đo trên máy dev, trung vị 7 lần:

| | không kế thừa | kế thừa `Entity` |
|---|---|---|
| `event.score` (đọc thuộc tính) | 6.8ns | 6.8ns |
| `event.score = x` (ghi) | 7.3ns | 7.3ns |
| dựng một entity | 112ns | 112ns |
| `sizeof` một entity 8 trường | 96B | 96B |
| `Event.score` (đọc TỪ LỚP) | 12.6ns | 152ns |

Đối tượng entity không mất gì vì `Entity` chen vào **metaclass**, tức chỉ ở
đường đọc-từ-lớp — thứ chỉ chạy lúc bạn dựng câu truy vấn. Cách làm hiển nhiên
hơn (đặt descriptor vào thân lớp) thì mọi lần đọc thuộc tính đội lên 66ns và
`find()` 5000 dòng chậm thêm 24%, nên không dùng.

| Chạy | Trả về |
|---|---|
| `await .all()` | `list[Entity]`, hoặc `list[dict]` nếu có `.select()` |
| `await .first()` | một cái hoặc `None`; tự đặt `LIMIT 1` |
| `await .one()` | một cái, không có thì ném 404 |
| `await .count()` | `int`; có `group_by` thì là SỐ NHÓM |
| `await .exists()` | `bool` |
| `.sql()` | chuỗi SQL sẽ chạy (chỉ có ở sqlite/postgres) |

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
[`fastapi_modular/middleware/request_context.py`](../fastapi_modular/middleware/request_context.py).

Gọi repository **ngoài** một HTTP request (script, worker, test) vẫn được — backend
sẽ tự mở một connection tạm và commit ngay sau thao tác.

---

## Test trên database thật

Mặc định `fam test` chạy trên backend `memory` (nhanh, không cần server). Muốn
chạy đúng bộ test đó trên database thật:

```bash
TEST_SQLITE=1 fam test
TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app' fam test
TEST_MONGO_DSN='mongodb://127.0.0.1:27017' fam test
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
