# Hướng dẫn database SQL

SQLite, PostgreSQL, và backend `memory` (bản mô phỏng để `fam test` chạy
không cần server). **Dùng MongoDB thì đọc [mongodb.md](mongodb.md)** — bên đó
khác đủ nhiều để trộn chung một trang là hại người đọc: không có query
builder, không có transaction, không có migration.

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Khai một bảng dữ liệu mới" | [Khai báo entity](#khai-báo-entity) |
| "Sự kiện phải thuộc về một camera" | [Khoá ngoại](#khoá-ngoại-nối-hai-bảng-với-nhau) |
| "**Xoá camera thì xoá luôn sự kiện của nó**" | [`CASCADE`](#cascade--xoá-cha-thì-con-đi-theo) |
| "**Xoá khu vực thì camera ở lại, chỉ mất chỗ gắn**" | [`SET NULL`](#set-null--con-ở-lại-mất-chỗ-gắn) |
| "**Xoá camera thì thẻ về giá trị mặc định**" | [`SET DEFAULT`](#set-default--về-giá-trị-bạn-đặt-sẵn) |
| "Còn hoá đơn thì KHÔNG cho xoá khách hàng" | [`RESTRICT`](#restrict--chặn-không-cho-xoá) |
| "**Xoá cha mà cháu vẫn còn**" | [Cascade dừng giữa chừng](#cascade-dừng-giữa-chừng-database-chưa-biết-khoá-ngoại) |
| "Đọc/ghi dữ liệu trong service" | [Dùng Repository trong code](#dùng-repository-trong-code) |
| "**Sửa một dòng mà không phải đọc nó về trước**" | [`update`](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| "**Sửa hàng loạt: mọi camera Tầng 1 thành offline**" | [`update`](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| "**Truyền thẳng DTO của PATCH vào để sửa**" | [`update`](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| "**Ghi 2 bảng, hỏng thì huỷ cả hai**" | [Transaction](#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không) |
| "Lọc lớn hơn, nhỏ hơn, NULL, nối bảng" | [Truy vấn phức tạp](#truy-vấn-phức-tạp--join-lớnbé-null) |
| "**Điều kiện này HOẶC điều kiện kia**" | [`or_where`](#or-or_where-mở-nhánh-mới) |
| "**Mỗi camera có bao nhiêu sự kiện**" | [Gộp nhóm](#gộp-nhóm-đếm-tính-trung-bình) |
| "**Chỉ lấy camera có hơn 5 sự kiện**" | [HAVING](#having--lọc-theo-kết-quả-gộp) |
| "Camera cha của camera này tên gì" | [Nối bảng với chính nó](#nối-bảng-với-chính-nó-self-join) |
| "Giữ cả camera chưa có sự kiện nào" | [Bốn kiểu nối](#bốn-kiểu-nối-bốn-method) |
| "**Trả về camera kèm danh sách sự kiện của nó**" | [Dữ liệu lồng nhau](#dữ-liệu-lồng-nhau-include) |
| "**Trả về sự kiện kèm object camera**" | [Dữ liệu lồng nhau](#dữ-liệu-lồng-nhau-include) |
| "**Lọc theo sự kiện nhưng trả về camera ở ngoài**" | [`nest_under`](#đảo-chiều-nest_under) |
| "**Camera > log > item, lồng ba tầng**" | [Lồng nhiều mức](#lồng-nhiều-mức) |
| "**Bảng nhiều cột quá, tôi muốn bỏ bớt một cột**" | [`select(exclude=…)`](#chọn-cột-trả-về) |
| "**Đổi tên trường trả về**" | [`select(rename=…)`](#chọn-cột-trả-về) |
| "**Giữ đủ cột, thêm một cột của bảng đã join**" | [`select(add=…)`](#chọn-cột-trả-về) |
| "Chỉ lấy dòng có cột này để trống" | [`is_null`](#like-in-is-null-between--bảy-toán-tử-không-có-ký-hiệu) |
| "Tìm theo tên gần đúng" | [`like` / `ilike`](#like-in-is-null-between--bảy-toán-tử-không-có-ký-hiệu) |
| "**Có chống được SQL injection không**" | [Injection](#injection-cái-gì-được-chặn-chặn-ở-đâu) |
| "Chọn database nào" | [Cách chọn driver](#cách-chọn-driver) |
| "Tôi đang dùng MongoDB" | [mongodb.md](mongodb.md) |
| "Thêm/xoá trường mà không mất dữ liệu" | [Tự chỉnh schema](#tự-chỉnh-schema-thêm--xoá-trường) |

---

Template hỗ trợ **4 backend**: `memory` (mặc định), `sqlite`, `postgres`, và
`mongodb` ([trang riêng](mongodb.md)). Tại một thời điểm chỉ **một** backend được
dùng, và **chỉ thư viện của backend đó cần được cài** — chọn Postgres thì máy
không cần `aiosqlite` hay `motor`.

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

Cột `Enum` **lọc bằng gì cũng được** — thành viên Enum hay chuỗi `.value` đều
khớp, trên cả ba backend:

```python
class DeviceKind(Enum):
    NORMAL = "thuong"
    SPECIAL = "dac_biet"


@entity()
@dataclass(slots=True)
class Device(Entity):
    id: str
    kind: DeviceKind = DeviceKind.NORMAL
```

```python
await devices.find(kind=DeviceKind.SPECIAL)   # thành viên Enum
await devices.find(kind="dac_biet")           # chuỗi từ JSON của client — cũng khớp
```

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

Chuỗi đi **hết bao nhiêu tầng cũng được**, miễn tầng nào cũng khai `CASCADE`:

```python
@entity()
@dataclass(slots=True)
class Camera(Entity):
    id: str
    name: str = ""


@entity()
@dataclass(slots=True)
class CameraLog(Entity):
    id: str
    camera_id: str = field(default="", metadata=reference(Camera, on_delete="CASCADE"))


@entity()
@dataclass(slots=True)
class ItemLog(Entity):
    id: str
    camera_log_id: str = field(
        default="", metadata=reference(CameraLog, on_delete="CASCADE")
    )
```

```python
await cameras.delete("cam-01")
# camera_log của cam-01 đi theo, VÀ item_log của những camera_log đó cũng đi theo
```

#### Cascade dừng giữa chừng: database chưa biết khoá ngoại

Triệu chứng rất riêng: xoá camera thì `camera_log` biến mất, còn `item_log`
**ở lại** — không lỗi, không cảnh báo. Ba tầng đều khai `CASCADE` mà chỉ chạy
được hai.

Nguyên nhân gần như luôn là: **bảng `item_logs` đã tồn tại từ trước khi bạn khai
`reference(...)`**. Khung chỉ `CREATE TABLE` cho bảng **còn thiếu**, còn
`schema_mode="sync"` chỉ thêm/bớt **cột** — không chế độ nào sửa ràng buộc của
bảng đã có. Khai báo nằm lại trong Python, database không hề biết.

Kiểm bằng log lúc khởi động — khung soi và kêu:

```
[warning] db.foreign_keys_stale
    problems=['item_logs.camera_log_id -> camera_logs: database KHÔNG có khoá ngoại này']
```

hoặc, khi khoá ngoại có nhưng hành vi khác:

```
    problems=['item_logs.camera_log_id: khai ON DELETE CASCADE, database đang NO ACTION']
```

Không thấy dòng này nghĩa là database khớp với khai báo — lỗi nằm chỗ khác.

Xem thẳng trong database cũng được:

```bash
sqlite3 app.db "PRAGMA foreign_key_list(item_logs)"     # cột on_delete
psql -c "\d item_logs"                                  # dòng 'Foreign-key constraints'
```

**Sửa thế nào** — khung không tự sửa, vì thêm khoá ngoại vào bảng đang có dữ
liệu có thể hỏng giữa chừng (còn dòng mồ côi) và có thể khoá bảng rất lâu:

```sql
-- PostgreSQL: dọn mồ côi trước, rồi thêm ràng buộc
DELETE FROM item_logs WHERE camera_log_id NOT IN (SELECT id FROM camera_logs);
ALTER TABLE item_logs
    ADD CONSTRAINT item_logs_camera_log_id_fkey
    FOREIGN KEY (camera_log_id) REFERENCES camera_logs (id) ON DELETE CASCADE;
```

SQLite **không** có `ALTER TABLE ... ADD CONSTRAINT`; phải tạo bảng mới rồi
chép dữ liệu sang, hoặc ở môi trường dev thì `DROP TABLE item_logs` cho khung
tạo lại.

Còn hai nguyên nhân nữa, hiếm hơn nhưng kiểm nhanh:

- **Khoá ngoại trỏ nhầm bảng.** `reference(ItemLog)` thay vì
  `reference(CameraLog)` vẫn chạy, chỉ là chuỗi không nối tới đâu. Đọc lại
  đúng dòng `metadata=reference(...)` của tầng bị bỏ sót.
- **Entity chưa được import.** Với `memory` và `mongodb`, khung tự cascade dựa
  trên danh sách entity đã đăng ký; module chưa được nạp thì bảng đó vô hình.
  SQL không dính vì database giữ danh sách này.

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

| Backend | Ai áp ràng buộc |
|---|---|
| `postgres`, `sqlite` | **chính database**, trong cùng transaction |
| `memory` | khung, để `fam test` cho cùng kết quả |
| [`mongodb`](mongodb.md#khoá-ngoại-khung-tự-làm-không-phải-database) | khung, **không nguyên tử** |

**Xoá nhiều cha một lúc cũng áp ràng buộc.** `delete_where(...)` chạy đúng luật
như `delete(id)`, không phải đường tắt bỏ qua khoá ngoại.

**Đổi khai báo khoá ngoại KHÔNG tự tới được database.** Thêm `reference(...)`
hay đổi `on_delete` cho một bảng đã tạo thì phải viết migration — khung chỉ soi
rồi cảnh báo `db.foreign_keys_stale` lúc khởi động. Xem [Cascade dừng giữa
chừng](#cascade-dừng-giữa-chừng-database-chưa-biết-khoá-ngoại).

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
| `update(where, changes, **set)` | **Sửa thẳng dưới database** (nhận cả DTO), trả số dòng khớp — [xem dưới](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| `delete(id)` | Xoá một, trả `True/False` |
| `delete_where(**equals, match=)` | Xoá nhiều, trả số bản ghi |
| `query()` | Builder cho JOIN, lớn/bé, NULL — xem [mục dưới](#truy-vấn-phức-tạp--join-lớnbé-null) |

### Sửa dữ liệu không cần đọc về trước

Vòng ba bước quen thuộc — đọc, sửa, ghi — tốn **hai** lượt đi database:

```python
item = await self._repo.get(camera_id)
item.status = "offline"
await self._repo.save(item)
```

`update()` làm cùng việc đó bằng **một** câu lệnh:

```python
await self._repo.update(camera_id, status="offline")
```

Điều kiện là **id, hoặc bất kỳ trường nào khác** — và khi đó nó sửa **mọi dòng
khớp**:

```python
# theo id
await cameras.update("cam-01", {"name": "Cổng chính"})

# theo cột khác: mọi camera ở Tầng 1 chuyển sang offline
so_dong = await cameras.update({"zone": "Tầng 1"}, status="offline")

# nhiều điều kiện = AND; giá trị viết bằng dict hay kwargs đều được
await cameras.update({"zone": "T1", "status": "online"}, threshold=0.9)
```

Trả về **số dòng khớp** (`0` nghĩa là không có dòng nào thoả điều kiện).
`updated_at` tự đóng dấu, y như `save()`.

**DTO truyền thẳng vào được**, không phải `model_dump()` nữa — cả handler còn
một dòng:

```python
@patch("/{camera_id}")
async def update(self, camera_id: CameraId, payload: CameraUpdate) -> int:
    return await self._service.update(camera_id, payload)

# service:
async def update(self, camera_id: str, payload: CameraUpdate) -> int:
    return await self._repo.update(camera_id, payload)
```

DTO được đọc bằng `exclude_unset=True`, y như [`apply_changes`](#hai-quy-ước-dễ-vấp):
**chỉ field client thực sự gửi lên mới được ghi**. Đây là chỗ đắt nhất của
PATCH — `model_dump()` trần trả về cả field không gửi (giá trị `None` mặc định
của `partial_of`), nên đổi mỗi `name` sẽ ghi `None` đè lên mọi cột còn lại. Gửi
`null` tường minh thì vẫn xoá được cột, vì `null` đã gửi là đã "set".

Trộn được cả ba cách trong một lời gọi: `update(id, dto, status="off")`.
`where` cũng nhận DTO — hợp với bộ lọc sinh bằng `partial_of(...)`.

Truyền **entity** vào thì bị từ chối, kèm lời chỉ đường: đã có sẵn cả bản ghi
thì `save(obj)` mới đúng.

Thứ tự tham số lấy đúng của TypeORM — `repo.update(criteria, partialEntity)` —
nên người từ NestJS sang không phải nhớ thêm gì.

**Nó chỉ GHI ĐÈ, không đọc giá trị cũ.** Cần tính từ giá trị đang có
(`so_lan = so_lan + 1`) thì đây không phải chỗ: đọc rồi ghi trong
`async with db.transaction():`, hoặc dùng [`RedisClient.incr`](redis.md#khoá--giá-trị)
nếu chỉ là bộ đếm.

**`where` rỗng bị chặn.** `update({}, ...)` gần như luôn là biến rỗng do lỗi
lập trình chứ không phải ý định sửa cả bảng — cố ý thì nói rõ:

```python
await cameras.update({}, zone="X", match=lambda _: True)
```

**Không đổi được `id`.** Nó là danh tính bản ghi và là thứ khoá ngoại của bảng
khác đang trỏ tới; muốn đổi thật thì tạo bản ghi mới rồi chuyển các bản ghi con
sang.

**Ràng buộc vẫn được áp.** Sửa một cột khoá ngoại sang giá trị không tồn tại,
hay làm trùng một cột `unique`, đều bị từ chối (409) — trên cả ba backend.

**Điều kiện chỉ so BẰNG.** Cần `>=`, `LIKE`, `IN` thì lọc bằng
[`query()`](#truy-vấn-phức-tạp--join-lớnbé-null) rồi `update` theo id, hoặc
truyền `match=` (lọc bằng Python, phải đọc dòng về trước nên chậm hơn).

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
events = await (
    repo.query()
    .join(Camera)                              # cột nối đọc từ `reference(Camera)`
    .where(Event.score >= 0.8, Event.label == "person")
    .is_null(Event.reviewed_at)                # IS NULL
    .like(Camera.name, "Cổng%")                # lọc theo cột bảng đã join
    .order_by_desc("created_at")               # chiều nằm trong tên hàm
    .limit(20)
    .all()
)
```

Kết quả là `list[Event]` như `find()`. **JOIN ở đây để LỌC**, không đổi kiểu trả
về — nên `.score`, `.label` vẫn gõ được như thường.

Không có gì chạy cho tới khi bạn gọi `.all()`, `.first()`, `.count()` hoặc
`.exists()`.

Cùng câu đó viết bằng **kiểu ngắn** — đuôi `__gte`, `__isnull` — cũng chạy y
hệt, chọn kiểu nào là tuỳ bạn:

```python
events = await (
    repo.query()
    .join(Camera)
    .where(score__gte=0.8, label="person", reviewed_at__isnull=True)
    .where(camera__name__like="Cổng%")
    .order_by_desc("created_at")
    .limit(20)
    .all()
)
```

### Cả bộ builder trong một bảng

| Việc | Viết |
|---|---|
| lọc | `.where(...)` · `.or_where(...)` |
| lớn/bé/bằng | `Event.score >= 0.8` · `score__gte=0.8` |
| LIKE, IN, NULL, BETWEEN | `.like()` `.ilike()` `.in_()` `.not_in()` `.is_null()` `.is_not_null()` `.between()` |
| nối bảng | `.join()` · `.left_join()` · `.right_join()` · `.outer_join()` |
| sắp xếp | `.order_by_asc(...)` · `.order_by_desc(...)` |
| phân trang | `.limit(n)` · `.offset(n)` |
| chọn cột | `.select(...)` với `fields=` `exclude=` `rename=` `add=` |
| gộp nhóm | `.group_by(...)` · `.having(...)` · `.or_having(...)` |
| dữ liệu lồng nhau | `.include(X, ...)` · `.nest_under(X, ...)` |
| chạy | `await .all()` `.first()` `.one()` `.count()` `.exists()` · `.sql()` |

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
return await query.order_by_desc("created_at").limit(20).all()
```

### Injection: cái gì được chặn, chặn ở đâu

**Giá trị luôn là tham số buộc, không bao giờ nối chuỗi.** Kiểm được, không
phải tin lời:

```python
spec = repo.query().where(Event.label == "'; DROP TABLE events; --")._spec
print(backend._compile(spec).compile(backend._engine))
# WHERE events.label = ?          <- placeholder
# params: {"label_1": "'; DROP TABLE events; --"}
```

**Tên cột, tên bảng, toán tử không đến từ chuỗi tự do.** Cột phải có trong
entity, toán tử phải nằm trong danh sách — sai thì báo lỗi kèm tên đúng:

```python
.select("name FROM events; DROP TABLE events; --")   # -> Event không có trường đó
.where(**{"score__gte; DROP TABLE": 1})              # -> 'gte; DROP TABLE' không phải toán tử
.order_by_asc("name; DROP TABLE events")             # -> Event không có trường đó
```

`alias=` đi vào câu lệnh dưới dạng định danh có đóng ngoặc kép, dấu nháy bên
trong bị nhân đôi.

**Điều kiện lọc mang toán tử của database bị từ chối.** Đây không phải chuyện
lý thuyết — đo trên MongoDB thật, `find(name="an", token={"$ne": ""})` **qua
được cửa đăng nhập** vì `{"$ne": ""}` không được so bằng mà thành toán tử. Kẻ
tấn công chỉ cần gửi JSON `{"token": {"$ne": ""}}`. Giờ cả ba backend cùng từ
chối:

```
Điều kiện 'token': giá trị chứa toán tử của database ($ne). Gần như luôn là dữ
liệu người dùng gửi lên thẳng vào truy vấn — ép kiểu nó về str/int/bool trước
(pydantic làm sẵn việc này).
```

**Tên cột lạ bị từ chối, không bị bỏ qua.** Bỏ qua nghe hiền nhưng nguy hiểm
ngang injection: đo được `find(**{"$where": "1 == 1"})` trên SQL trả về **toàn
bộ bảng** (bộ lọc biến mất), còn trên Mongo thì **chạy JavaScript** ngay trên
server. Cả hai giờ đều báo lỗi.

**Ba điều bạn vẫn phải tự làm:**

- **`.sql()` là để ĐỌC, không phải để chạy.** Nó nhúng giá trị thẳng vào chuỗi
  cho dễ nhìn. Đừng đem chuỗi đó đi `execute`.
- **Mẫu `like` do người dùng nhập nên được rào.** Nó không thoát ra ngoài truy
  vấn, nhưng `%` là ký tự đại diện: người dùng gõ `%` sẽ quét cả bảng. Muốn tìm
  đúng chữ `%` thì `\%`.
- **Đừng nhận tên cột từ client** (kiểu `?sort=<tên cột>`). Khung chặn tên
  không có thật, nhưng cột có thật mà bạn không muốn lộ thì nó vẫn cho — hãy
  ánh xạ qua một danh sách trắng.

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

### OR: `or_where` mở nhánh mới

**Nhiều `.where()` liền nhau nối với nhau bằng AND. `.or_where()` mở một nhánh
OR mới, rồi các `.where()` sau đó lại nối AND vào nhánh đó.**

```python
# (label = 'person' AND score >= 0.9) OR (label = 'fire' AND score >= 0.3)
await (repo.query()
       .where(Event.label == "person")
       .where(Event.score >= 0.9)
       .or_where(Event.label == "fire")
       .where(Event.score >= 0.3)
       .all())
```

```sql
WHERE events.label = 'person' AND events.score >= 0.9
   OR events.label = 'fire' AND events.score >= 0.3
```

Không có `or_where` nào thì câu lệnh không đổi một chữ so với trước — một nhánh
là chỉ toàn `AND`.

`or_where` nhận cả kiểu ngắn: `.where(label="fire").or_where(label="car")`.
Gọi `or_where` khi chưa có `where` nào thì nó chỉ là `where`.

**Muốn OR nằm BÊN TRONG một điều kiện AND** thì dùng `or_(...)` — hai thứ khác
nhau, đừng lẫn:

```python
# score >= 0.8 AND (label = 'fire' OR label = 'car')
await (repo.query()
       .where(Event.score >= 0.8)
       .where(or_(Event.label == "fire", Event.label == "car"))
       .all())
```

Đây là cả sự khác nhau: `or_where` cắt câu thành hai nhánh **ở tầng ngoài
cùng**; `or_(...)` là một điều kiện đơn lẻ nằm gọn trong một nhánh.

Viết bằng `&` `|` `~` cũng được, kết quả y hệt — nhưng **phải có ngoặc quanh
từng phép so sánh**, vì trong Python `&` ưu tiên cao hơn `>=`:

```python
.where(((Event.label == "person") & (Event.score >= 0.9))
       | ((Event.label == "fire") & (Event.score >= 0.3)))
```

Thiếu ngoặc thì `Event.score >= 0.9 & Event.label` chạy trước và bạn nhận
`TypeError: unsupported operand type(s) for &`. Không thích đếm ngoặc thì dùng
`or_where` hoặc `and_()`/`or_()` — không có bẫy nào.

`~` là NOT: `.where(~or_(Event.label == "fire", Event.label == "car"))`.

### LIKE, IN, IS NULL, BETWEEN — bảy toán tử không có ký hiệu

`>=`, `==`, `<` viết thẳng được. Bảy toán tử còn lại của SQL thì Python không có
ký hiệu tương ứng, nên chúng nằm **ngay trên builder**:

```python
q = cameras.query

await q().like(Camera.name, "Cổng%").all()        # LIKE, phân biệt hoa thường
await q().ilike(Camera.name, "cổng%").all()       # LIKE, bỏ qua hoa thường
await q().is_null(Camera.ip).all()                # IS NULL
await q().is_not_null(Camera.ip).all()            # IS NOT NULL
await q().in_(Camera.fps, [24, 25, 30]).all()     # IN (...)
await q().not_in(Camera.fps, [15]).all()          # NOT IN (...)
await q().between(Camera.fps, 24, 30).all()       # BETWEEN, hai đầu TÍNH VÀO

# nối AND với nhau và với where như thường
await (cameras.query()
       .like(Camera.name, "Cổng%")
       .between(Camera.fps, 24, 30)
       .where(is_active=True)
       .all())
```

Chúng là `where` viết gọn: nối AND với nhau và với mọi `where` khác, và
`or_where` vẫn cắt nhánh như thường.

**Đây là chỗ duy nhất IDE gợi ý được.** `repo.query()` có kiểu `Query[E]` nên
gõ dấu chấm là hiện ra đủ bảy cái. Ba cách viết còn lại thì không:

| Viết | Chạy | IDE |
|---|---|---|
| `.like(Camera.name, "Cổng%")` | mọi entity | **gợi ý được** |
| `.where(like(Camera.name, "Cổng%"))` | mọi entity | gợi ý được tên hàm |
| `.where(Camera.name.like("Cổng%"))` | cần `class Camera(Entity)` | ❌ *"Unresolved attribute reference 'like' for class 'str'"* |
| `.where(name__like="Cổng%")` | mọi entity | ❌ chuỗi thì không gợi ý được |

Vì sao cách thứ ba không bao giờ được gợi ý: type checker đọc khai báo
`name: str` nên với nó `Camera.name` là một `str`, mà `str` không có `.like`.
Câu lệnh vẫn chạy đúng — metaclass chỉ tồn tại lúc chạy — nhưng IDE thì chịu.

**Đừng viết `Camera.rtsp == None`.** Nó chạy đúng nhưng IDE gạch chân
*"Comparison with None performed with equality operators"*, mà `is None` thì
Python không cho nạp chồng. Dùng `.is_null(Camera.rtsp)`.

### `like` phân biệt hoa thường, ở cả ba backend

```python
await cameras.query().like(Camera.name, "kho%").all()     # KHÔNG khớp "Kho hàng"
await cameras.query().ilike(Camera.name, "kho%").all()    # khớp
```

SQLite mặc định làm ngược lại: `LIKE` của nó bỏ qua hoa thường với ký tự ASCII,
nên cùng một câu lệnh ra kết quả khác Postgres. Khung bật
`PRAGMA case_sensitive_like=ON` để ba backend giống nhau — đo được: không bật
thì `LIKE 'kho%'` ra "Kho hàng" ở sqlite mà không ra gì ở memory và Postgres.

### Sắp xếp

Chiều nằm trong **tên hàm**, không nằm trong dấu trừ của chuỗi:

```python
.order_by_desc("created_at")            # mới nhất trước
.order_by_asc(Camera.name)              # cột thật cũng được
.order_by_desc(count())                 # hàm gộp cũng được
```

Nhiều cột thì gọi nối tiếp, **thứ tự gọi là thứ tự ưu tiên**:

```python
.order_by_desc("score").order_by_asc("created_at")
# ORDER BY score DESC, created_at ASC
```

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

### Bốn kiểu nối, bốn method

Đọc tên là biết ra SQL gì — không có cờ `outer=`/`full=` nào phải nhớ:

| Viết | Ra SQL | Giữ dòng không khớp của |
|---|---|---|
| `.join(Camera)` | `JOIN` | không bên nào |
| `.left_join(Event)` | `LEFT JOIN` | bên **trái** (bảng gốc) |
| `.right_join(Event)` | `RIGHT JOIN` | bên **phải** |
| `.outer_join(Event)` | `FULL OUTER JOIN` | **cả hai** bên |

Cả bốn nhận cùng bộ tham số: `on=`, `alias=`.

**`left_join` là cách tìm "cái nào còn trống":**

```python
# camera CHƯA có sự kiện nào
await (cameras.query()
       .left_join(Event)
       .where(F(Event).id.is_null())
       .all())
```

**`right_join` và `outer_join` bắt buộc `.select(...)`.** Chúng sinh cả những
dòng KHÔNG có bản ghi nào của bảng gốc, mà mặc định truy vấn trả về entity của
bảng gốc — trả một `Camera` toàn `None` thì chỉ là bịa. Lỗi báo ngay, và chỉ
luôn cách khác: đảo lại, lấy bảng kia làm gốc rồi `left_join`.

Hai điều đáng biết về hai kiểu này:

- **`right_join` sinh ra `LEFT JOIN` với hai vế đảo chỗ**, vì hai câu đó bằng
  nhau còn `RIGHT JOIN` thì SQLite chỉ có từ 3.39. Kết quả không đổi, chỉ là
  câu lệnh đọc khác lúc bạn xem `.sql()`.
- **`outer_join` cần SQLite từ 3.39 trở lên.** Postgres thì lúc nào cũng có.

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
              .order_by_desc(count())
              .all())
# [{"camera_id": "c1", "so_luong": 12, "diem_tb": 0.83}, ...]
```

`group_by` **bắt buộc đi với `.select(...)`**: sau khi gộp thì một dòng không
còn là một bản ghi nữa. Không có `.select` thì báo lỗi chứ không trả entity bịa.

**Cột trả về phải nằm trong `group_by`, hoặc phải bọc trong hàm gộp.** Không có
đường thứ ba:

```python
.group_by(Event.camera_id).select("camera_id", "label", so=count())   # LỖI
.group_by(Event.camera_id).select("camera_id", nhan=max_(Event.label), so=count())   # được
.group_by(Event.camera_id, Event.label).select("camera_id", "label", so=count())     # được
```

Lý do không phải hình thức: một nhóm có nhiều `label` khác nhau, trả về cái nào
cũng là bịa. SQLite im lặng trả giá trị của **một dòng bất kỳ** trong nhóm, còn
Postgres từ chối hẳn — tức là câu lệnh chạy ngon ở `fam dev` rồi đổ ở
production. Khung chặn ngay để hai nơi giống nhau.

**`include` đi cùng `group_by` được, với điều kiện gộp theo đúng cột ghép:**

```python
# được: mỗi nhóm đúng một camera
await (events.query().group_by(Event.camera_id)
       .select("camera_id", so=count())
       .include(Camera, fields=["name"]).all())
# [{"camera_id": "c1", "so": 12, "camera": {"name": "Cổng chính"}}]

# LỖI: gộp theo label thì một nhóm trải trên nhiều camera
await (events.query().group_by(Event.label)
       .select("label", so=count()).include(Camera).all())
```

| Hàm | SQL | Ghi chú |
|---|---|---|
| `count()` | `count(*)` | đếm DÒNG |
| `count(Event.label)` | `count(label)` | đếm dòng có `label` **khác NULL** |
| `count(Event.label, distinct=True)` | `count(DISTINCT label)` | đếm **giá trị khác nhau** |
| `sum_(Event.score)` | `sum(score)` | |
| `avg(Event.score)` | `avg(score)` | |
| `min_(...)` `max_(...)` | `min` `max` | |

Tên có gạch dưới (`sum_`, `min_`, `max_`) vì `sum`, `min`, `max` là hàm sẵn có
của Python.

**`count()` là `count(*)`; đếm theo cột thì truyền cột vào.** Ba cái này khác
nhau, và khác nhau đúng ở chỗ NULL:

```python
.select("camera_id",
        moi_dong=count(),                              # 4
        da_duyet=count(Event.reviewed_at),             # 1  — bỏ dòng chưa duyệt
        nhan_khac_nhau=count(Event.label, distinct=True))   # 2  — person, fire
```

Cột của bảng đã `join` cũng đếm được: `count(Camera.name)`.

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

Nhiều điều kiện trong một `having` nối với nhau bằng AND, và có `or_having`
mở nhánh mới y hệt `or_where`. `or_(...)` dùng được ở đây luôn.

`.count()` trên một truy vấn có `group_by` đếm **số nhóm**, đúng như
`SELECT count(*) FROM (...)` của SQL.

### Dữ liệu lồng nhau (`include`)

`join` để **lọc**. Muốn trả về dữ liệu **lồng nhau** thì dùng `include`:

```python
# mỗi camera kèm danh sách sự kiện của nó
rows = await cameras.query().include(Event).all()
# [{"id": "c1", "name": "Cổng chính", ..., "events": [{...}, {...}]},
#  {"id": "c3", ..., "events": []}]

# chiều ngược lại: mỗi sự kiện kèm MỘT object camera
rows = await events.query().include(Camera).all()
# [{"id": "e1", "score": 0.95, ..., "camera": {"id": "c1", "name": "Cổng chính", ...}}]
```

**Chiều nào là do khoá ngoại quyết định, không phải bạn khai.** Khoá ngoại nằm
bên `Event` nên một camera có NHIỀU sự kiện (trả `list`), còn một sự kiện chỉ
có MỘT camera (trả object, hoặc `None` nếu cột khoá ngoại đang NULL). Không có
con nào thì là `[]`, không phải `None`.

Tên trường mặc định là tên class viết thường, thêm `s` nếu là danh sách:
`events`, `camera`. Đổi bằng `name=`:

```python
await cameras.query().include(Event, name="su_kien").all()
```

**Có `include` thì kết quả là `list[dict]`, không phải `list[Entity]`.** Entity
là dataclass `slots=True` — không gắn thêm trường vào một object như vậy được.

### Chọn cột trả về

**`select` là CHỌN, không phải THÊM.** Kể tên cột nào thì kết quả **chỉ có**
những cột đó, giống hệt `SELECT` của SQL:

```python
await repo.query().join(Camera).select(ten_camera=Camera.name).all()
# [{"ten_camera": "Cổng chính"}]     <- chỉ đúng một cột, không phải "mọi cột + ten_camera"
```

Muốn giữ đủ cột thì nói rõ bằng `add=` hoặc `rename=`. Bốn tham số, bốn việc:

| Viết | Nghĩa |
|---|---|
| `fields=["id", "name"]` | **chỉ** những cột này |
| `exclude=["raw_payload"]` | mọi cột **trừ** những cột này |
| `rename={"ma": "id"}` | giữ đủ cột, chỉ **đổi tên** trả về |
| `add={"ten_camera": Camera.name}` | giữ đủ cột, **thêm** một cột nữa |

```python
await (events.query().join(Camera)
       .select(add={"ten_camera": Camera.name})
       .all())
# [{"id": "e1", "label": "person", "score": 0.95, ..., "ten_camera": "Cổng chính"}]
```

```sql
SELECT events.id, events.label, events.score, …, cameras.name AS ten_camera
FROM events JOIN cameras ON …
```

`include` / `nest_under` nhận `fields=`, `exclude=`, `rename=` cùng luật (không
có `add=` vì bên đó "đủ cột" đã là mặc định).

**`add=` không nhận hàm gộp**, và đây không phải hạn chế của khung mà của SQL:
`SELECT *, count(x)` là câu lỗi ở PostgreSQL — gộp rồi thì một dòng kết quả
không còn ứng với một bản ghi nào để mà "giữ đủ cột". Lỗi nói luôn hai cách
viết đúng:

```python
.group_by(Event.camera_id).select("camera_id", so_luong=count())   # đếm theo nhóm
.select(so_luong=count())                                          # gộp CẢ BẢNG -> 1 dòng
```

```python
await (cameras.query()
       .select("id", "name")                          # cột của bảng GỐC
       .include(Event, fields=["id", "label"])        # cột của bảng lấy kèm
       .all())
```

`select("id", "name")` và `select(fields=["id", "name"])` là một. Dạng danh sách
có mặt để `select` viết giống `include` khi bạn dùng cả hai trong một câu.

Chỗ nào nhận tên cột thì nhận cả **cột thật**, y như `join` và `where`:

```python
await (cameras.query()
       .select(Camera.id, Camera.name)
       .include(Event, fields=[Event.id, Event.label])
       .all())
```

**Đổi tên trường trả về** — hai cách, chọn theo việc bạn có muốn giữ đủ cột không:

```python
# chỉ 2 cột, đặt tên luôn
await cameras.query().select(fields={"ma": "id", "ten": Camera.name}).all()
# [{"ma": "c1", "ten": "Cổng chính"}]

# ĐỦ cột, chỉ sửa tên một hai cái
await cameras.query().select(rename={"ma": "id"}).all()
# [{"ma": "c1", "name": "Cổng chính", "ip": "10.0.0.1", ...}]

# ở bảng lấy kèm cũng vậy
await cameras.query().include(Event, rename={"nhan": "label"}).all()
```

**Chiều của dict là `{tên bạn muốn: tên cột có thật}`**, giống hệt
`select(ma=Camera.id)`. Viết ngược (`rename={"id": "ma"}`) thì `"ma"` không phải
tên cột nên báo lỗi ngay — không âm thầm cho ra tên sai.

Cột được đổi tên **giữ nguyên vị trí** trong dict kết quả, không bị đẩy xuống
cuối: thứ tự khoá ở đây chính là thứ tự trường trong response JSON.

Nhiều cột quá mà chỉ muốn bỏ vài cái thì đi từ chiều ngược lại:

```python
await cameras.query().select(exclude=["is_active"]).all()           # bảng gốc
await cameras.query().include(Event, exclude=["created_at"]).all()  # bảng lấy kèm
```

Cả ba **bắt tên sai ngay lúc dựng câu lệnh**, kèm danh sách tên đúng — không im
lặng bỏ qua rồi để bạn ngồi tìm cột biến đâu mất. Đưa nhầm cột của bảng khác
(`include(Event, fields=[Camera.name])`) cũng bị chặn.

Cột dùng để ghép (`camera_id`) được tự thêm vào câu lệnh nếu bạn không xin, rồi
**bỏ khỏi kết quả** — bạn không phải nhớ nó.

### Đảo chiều (`nest_under`)

`include` đi từ bảng ngoài vào. Khi **điều kiện nằm bên bảng con** mà bạn vẫn
muốn cha ở ngoài thì đi ngược lại:

```python
rows = await (events.query()
              .where(Event.score >= 0.9)          # lọc theo SỰ KIỆN
              .nest_under(Camera)                 # nhưng trả về CAMERA
              .all())
# [{"id": "c1", "name": "Cổng chính", ..., "events": [{...}, {...}]}]
```

So với `cameras.query().include(Event, where=...)`:

| | Trả về |
|---|---|
| `cameras.query().include(Event, where=…)` | **mọi** camera; camera không có sự kiện khớp thì `events: []` |
| `events.query().where(…).nest_under(Camera)` | **chỉ** camera có sự kiện khớp |

**Ai quyết định cột nào** — mỗi câu một việc, không chồng nhau:

| Câu | Nói về |
|---|---|
| `.select(...)` | cột của **bảng gốc** |
| `.include(Camera, fields=…)` | cột của **Camera** — trả về những cột nào |
| `.nest_under(Camera, …)` | **thứ tự lồng nhau**: bảng nào ngoài, bảng nào trong |
| `.include(Camera, name="…")` | tên trường chứa Camera |

Hai câu cuối đi cùng nhau được, và đó là cách viết gọn nhất:

```python
await (events.query()
       .select(exclude=["reviewed_at"], rename={"dd": "created_at"})  # cột của Event
       .include(Camera, fields=["id", "name"])                        # cột của Camera
       .nest_under(Camera)                                            # Camera ra ngoài
       .all())
# [{"id": "c1", "name": "demo",
#   "events": [{"id": "e1", "label": "…", "camera_id": "c1", "dd": "…"}]}]
```

**Camera chỉ hiện MỘT lần — ở lớp ngoài.** Có `nest_under(Camera)` rồi thì
`include(Camera)` không lồng camera vào từng dòng bên trong nữa; nó chỉ còn làm
đúng một việc là khai cột. Gọi hai câu theo thứ tự nào cũng vậy.

Không dùng `include` thì khai thẳng ở `nest_under` cũng được, nhưng chỉ khi
chuỗi có **một** bảng:

```python
.nest_under(Camera, fields=["id", "name"])
```

### Lồng nhiều mức

Kể tên từ NGOÀI vào TRONG. Ba bảng: `Camera` <- `CameraLog` <- `ItemLog`, repo
là `CameraLog`:

```python
await (logs.query()
       .select("id", "label")
       .include(Camera, fields=["id", "name"])
       .include(ItemLog, fields=["id", "note"])
       .nest_under(Camera, CameraLog, ItemLog)
       .all())
# [{"id": "c1", "name": "demo",
#   "cameralogs": [{"id": "l0", "label": "log 0",
#                   "itemlogs": [{"id": "i0", "note": "…"}]}]}]
```

Không kể bảng gốc thì nó nằm trong cùng: `nest_under(Camera)` chính là
`nest_under(Camera, CameraLog)`.

Repo là bảng **trong cùng** (`ItemLog`) cũng lồng ra được y như vậy — kể cả
`include(Camera)` khi Camera KHÔNG có khoá ngoại trực tiếp với ItemLog, miễn là
chuỗi `nest_under` nối được tới nó:

```python
await (item_logs.query()
       .select("id", "note")
       .include(CameraLog, fields=["id", "label"])
       .include(Camera, fields=["id", "name"])
       .nest_under(Camera, CameraLog, ItemLog)
       .all())
```

Bảng nào không có khoá ngoại trực tiếp với bảng gốc mà cũng **không** nằm trong
chuỗi thì lỗi vẫn ném — nhưng ném lúc `.all()`, không phải lúc `include(...)`.

Tên trường của từng lớp lấy từ `include(X, name=…)`:

```python
.include(CameraLog, name="log_cua_no")
# [{"id": "c1", "log_cua_no": [{…, "itemlogs": [...]}]}]
```

**Chiều khoá ngoại quyết định lớp trong là danh sách hay một object.** Khoá
ngoại nằm bên lớp trong → một dòng ngoài có nhiều dòng trong (`list`); nằm bên
lớp ngoài → ngược lại, lớp trong là **một object**:

```python
await cameras.query().nest_under(Event).all()
# [{"id": "e1", …, "camera": {…}}, …]     <- mỗi sự kiện MỘT camera
```

**Hai bảng cạnh nhau phải có khoá ngoại trực tiếp.** Không có thì báo lỗi và
mách đường đi, chứ khung không tự sắp lại giúp — sắp sai thì dữ liệu sai mà
không ai thấy:

```
`nest_under(..., ItemLog, Camera, ...)`: hai bảng này không có khoá ngoại
TRỰC TIẾP với nhau. Kể tên đủ các bảng trên đường đi
(ItemLog -> CameraLog -> Camera), hoặc đổi thứ tự.
```

**Mỗi mức đúng một câu lệnh nữa.** Ba mức là ba câu, không phải một câu cho mỗi
dòng — có test đếm đúng số câu.

Nhiều bảng thì cột và tên của từng bảng khai bằng `include(X, fields=…,
name=…)`; truyền `fields=` thẳng vào `nest_under(A, B)` sẽ báo lỗi vì không rõ
nó nói về bảng nào.

Khai ở **cả hai** chỗ thì báo lỗi, vì không có luật nào để đoán bên nào thắng:

```
Cột của Camera đang khai ở HAI chỗ: `include(Camera, fields=…)` và
`nest_under(Camera, fields=…)`. Bỏ một trong hai.
```

`include(Camera, where=…, order_by_*=…)` cũng không dùng được khi Camera ra lớp
ngoài: lớp ngoài lấy đúng bản ghi cha của từng nhóm, không có gì để lọc hay sắp.

`include` một bảng **khác** thì vẫn dùng chung bình thường — nó gắn vào từng
dòng bên trong như mọi khi.

**`join(Camera)` không cần** nếu bạn không lọc theo cột nào của Camera:
`nest_under` tự lấy camera bằng một câu lệnh riêng.

Ba điều dễ vấp:

- **`limit` vẫn đếm theo bảng GỐC.** `.limit(20).nest_under(Camera)` là 20 sự
  kiện gom lại thành vài camera, không phải 20 camera.
- **Dòng có khoá ngoại NULL bị bỏ** — nó không thuộc cha nào để gom vào.
- Thứ tự camera theo sự kiện đầu tiên của nó, nên `.order_by_desc(...)` của bảng gốc
  vẫn có tác dụng.

Muốn sắp **theo cột của một lớp** thì cứ nêu thẳng cột của lớp đó — khung tự
xếp ở đúng lớp, không cần `join`:

```python
await (events.query().select("id")
       .nest_under(Camera, fields=["id", "name"])
       .order_by_desc(Camera.name)          # sắp DANH SÁCH camera lớp ngoài
       .all())
```

Cột không thuộc bảng gốc, bảng đã `join`, hay lớp nào trong chuỗi thì báo lỗi
thẳng — cả ba backend cùng một kiểu.

Cũng đúng một câu lệnh nữa, y như `include`.

### Lọc và sắp bảng được lấy kèm

```python
await (cameras.query()
       .include(Event,
                fields=["id", "score"],
                where=F(Event).score >= 0.9,      # chỉ lấy sự kiện điểm cao
                order_by_desc="score")                # mới nhất/cao nhất lên đầu
       .all())
```

**Chưa có: giới hạn số con MỖI cha** ("mỗi camera 5 sự kiện gần nhất"). Cái đó
cần window function, chưa làm. `limit` ở câu ngoài giới hạn số CAMERA, không
phải số sự kiện của mỗi camera.

### `include` tốn thêm mấy câu lệnh?

**Một câu cho mỗi `include`, không phải một câu cho mỗi dòng.** Lấy 100 camera
kèm sự kiện là 2 câu: một câu lấy camera, một câu
`WHERE camera_id IN (100 id)`. Đây là chỗ N+1 hay nằm, nên có test đếm đúng số
câu lệnh để nó không lặng lẽ thành 101.

Danh sách id được chia mẻ 500 một câu — SQLite bản cũ chỉ cho 999 tham số một
câu lệnh.

### Lấy cột của bảng đã join

Mặc định chỉ trả entity gốc. Cần cột bảng kia thì nói rõ — khi đó trả `list[dict]`:

```python
rows = await (repo.query()
              .join(Camera)
              .select("id", "score", camera_name=Camera.name)
              .all())
# [{"id": "e1", "score": 0.95, "camera_name": "Cổng chính"}]
```

`select` và `order_by_asc`/`order_by_desc` nhận cột thật y như `join`:
`select(Event.id)`, `order_by_desc(Event.score)`.

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

| Backend | Query builder |
|---|---|
| `postgres`, `sqlite` | đầy đủ, sinh SQL thật |
| `memory` | đầy đủ, tính bằng Python — để `fam test` chạy được, cỡ O(n×m) nên chỉ hợp với dữ liệu test |
| [`mongodb`](mongodb.md#truy-vấn-lớnbé-like-in-null) | được, **trừ** `join`, `group_by`/`having`, `distinct` |

### Tra cứu

| Dựng | |
|---|---|
| `.join(Entity)` | INNER JOIN, cột nối lấy từ `reference(...)` |
| `.left_join(Entity)` | LEFT JOIN |
| `.right_join(Entity)` · `.outer_join(Entity)` | RIGHT · FULL OUTER (cả hai cần `.select`) |
| `.join(Entity, alias="cha")` | nối bảng với chính nó; lấy cột bằng `F(Entity, "cha")` |
| `.group_by(X.cot)` | gộp nhóm; bắt buộc kèm `.select(...)` |
| `.having(count() > 5)` · `.or_having(...)` | lọc theo kết quả gộp |
| `.join(Entity, on=…, outer=False, alias="")` | `on=` nhận `Entity.cot`, `"cot"`, `("cot","cot_kia")`, hoặc cả một điều kiện |
| `.where(*điều_kiện, **kwargs)` | nhiều lần `where` nối bằng AND |
| `.like(X.cot, "a%")` · `.ilike(...)` | `LIKE` / `ILIKE`; `like` phân biệt hoa thường |
| `.is_null(X.cot)` · `.is_not_null(X.cot)` | `IS NULL` / `IS NOT NULL` |
| `.in_(X.cot, [...])` · `.not_in(...)` | `IN` / `NOT IN` |
| `.between(X.cot, a, b)` | `BETWEEN`, hai đầu tính vào |
| `like(X.cot, "a%")`, `is_null(X.cot)`, … | cùng bảy cái, dạng hàm — dùng trong `or_()`/`or_where` |
| `.or_where(*điều_kiện, **kwargs)` | mở nhánh OR mới |
| `.order_by_asc("name")` · `.order_by_desc("created_at")` | chiều nằm trong TÊN HÀM; nhận `X.cot` và `count()` |
| `.limit(n)` · `.offset(n)` · `.distinct()` | |
| `.select("id", ten_khac=X.cot)` | đổi kiểu trả về sang `list[dict]` |
| `.select("id", ten_khac=X.cot)` | chọn cột bảng gốc; kết quả thành `list[dict]` |
| `.select(fields=…, exclude=…, rename=…, add=…)` | chỉ những cột này · trừ những cột này · đổi tên · giữ đủ và thêm |
| `.include(Entity, name=…, fields=…, exclude=…, rename=…, where=…, order_by_asc=…, order_by_desc=…)` | gắn dữ liệu lồng nhau |
| `async with db.transaction() as tx:` · `await tx.rollback()` | xem [Transaction](#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không) |
| `.nest_under(A, B, C)` | thứ tự lồng nhau, NGOÀI -> TRONG; bảng gốc không kể thì nằm trong cùng |
| `.nest_under(A, name=…, fields=…, exclude=…, rename=…, on=…)` | dạng một bảng: khai luôn cột và tên |
| `.select(so=count(), tb=avg(X.cot))` | hàm gộp, phải đặt tên |

| Cột | |
|---|---|
| `Event.score` | cần `class Event(Entity)` |
| `F(Event).score` | không cần gì |
| `F(Event, "cha").score` | cột của bảng đã đặt `alias="cha"` |
| `"score"` | chỉ ở `on=`, `group_by`, `order_by_*`, `select`, `fields=` — chỗ đã biết bảng |

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

## Transaction — ghi nhiều bảng thì cùng thành công hoặc cùng không

### Trong HTTP handler: đã có sẵn, không phải làm gì

Mỗi request là **một** transaction. Ghi hai bảng rồi ném lỗi thì cả hai bị huỷ:

```python
@post("")
async def tao(self, body: CameraCreate) -> CameraOut:
    camera = await self._cameras.save(Camera(id="", name=body.name))
    await self._logs.save(CameraLog(id="", camera_id=camera.id, message="tạo"))
    # ném lỗi ở đây -> KHÔNG còn camera nào được ghi
    return CameraOut.model_validate(camera)
```

- Handler xong không lỗi → `COMMIT`
- Handler ném exception → `ROLLBACK`

Commit xảy ra **trước khi** response được gửi đi, nên client ghi xong đọc lại
ngay sẽ thấy dữ liệu mới.

### Ngoài request (worker, job, cron, script): PHẢI tự bọc

Ở đó không có request nào để bám vào, nên **mỗi `save()` tự commit ngay**. Ghi
bảng thứ hai hỏng thì bảng thứ nhất đã nằm lại rồi:

```python
@worker(thread=False)
async def dong_bo(self, ctx: WorkerContext) -> None:
    async with self._db.transaction():                 # <-- bắt buộc ở đây
        camera = await self._cameras.save(Camera(id="", name="Cổng"))
        await self._logs.save(CameraLog(id="", camera_id=camera.id, message="tạo"))
```

`self._db` là `Database`, khai như mọi provider khác:

```python
@injectable
class CameraService:
    def __init__(self, db: Database, cameras: Repository[Camera],
                 logs: Repository[CameraLog]) -> None:
        self._db, self._cameras, self._logs = db, cameras, logs
```

**Mọi repository trong khối đều đi chung một connection**, nên gọi
`transaction()` ở đâu cũng bao trùm tất cả — không phải truyền gì qua lại.

### Huỷ một PHẦN mà vẫn chạy tiếp

Khối lồng nhau thành `SAVEPOINT`: khối trong hỏng thì chỉ phần của nó bị huỷ.

```python
for cam in danh_sach:
    try:
        async with self._db.transaction():
            await self._cameras.save(cam)
            await self._logs.save(CameraLog(id="", camera_id=cam.id, message="tạo"))
    except Exception:
        loi.append(cam.id)        # camera này bị huỷ, những cái trước vẫn còn
```

Dùng được cả trong HTTP handler — ở đó nó là SAVEPOINT trên transaction của
request.

### Huỷ mà không ném lỗi

`async with` tự rollback khi có exception. Muốn huỷ vì một lý do bình thường —
dữ liệu không hợp lệ, không có gì để làm — mà không muốn ném lỗi ra ngoài thì
lấy tay cầm:

```python
async with self._db.transaction() as tx:
    await self._cameras.save(camera)
    if not await self._kiem_tra(camera):
        await tx.rollback()          # thoát khối tại đây, KHÔNG ném lỗi
    await self._logs.save(log)       # dòng này không chạy
```

Phần còn lại của khối dừng ngay tại `tx.rollback()`, code sau khối chạy tiếp
như thường. Trong khối lồng nhau thì nó chỉ huỷ khối trong.

### So với `queryRunner` của TypeORM

Cả đoạn dưới đây của NestJS/TypeORM:

```ts
await queryRunner.connect();
await queryRunner.startTransaction();
try {
  await queryRunner.manager.save(users[0]);
  await queryRunner.manager.save(users[1]);
  await queryRunner.commitTransaction();
} catch (err) {
  await queryRunner.rollbackTransaction();
} finally {
  await queryRunner.release();
}
```

viết ở đây là:

```python
async with self._db.transaction():
    await self._users.save(users[0])
    await self._users.save(users[1])
```

| TypeORM | Ở đây |
|---|---|
| `connect()` + `release()` | khối tự mở và tự trả connection, kể cả khi có lỗi |
| `startTransaction()` | vào khối |
| `commitTransaction()` | thoát khối êm |
| `rollbackTransaction()` trong `catch` | có exception → tự rollback rồi ném tiếp |
| `queryRunner.manager.save(...)` | repository bạn đang có sẵn — không cần đổi sang object khác |

Chỗ khác nhau đáng kể nhất là dòng cuối: TypeORM bắt bạn dùng
`queryRunner.manager` thì thao tác mới nằm trong transaction, gọi nhầm
`this.usersRepository` là nó chạy ngoài transaction mà không báo gì. Ở đây
connection đang mở nằm trong `ContextVar`, nên **mọi** repository trong khối tự
đi vào đúng transaction đó.

Còn `finally: release()` là dòng người ta quên — quên thì rò connection cho tới
khi hết pool. `async with` không quên được. Chính TypeORM cũng khuyên dùng
`dataSource.transaction(...)` thay cho `queryRunner` vì lý do đó.

### Kiểm xem nó chạy chưa

Cách nhanh nhất là làm nó hỏng có chủ đích rồi đếm lại:

```python
try:
    async with db.transaction():
        await cameras.save(Camera(id="c1", name="X"))
        raise RuntimeError("thử")
except RuntimeError:
    pass
assert await cameras.query().count() == 0     # còn 1 là transaction chưa ăn
```

### Lưu ý

**Backend `memory` cũng rollback**, cả trong `transaction()` lẫn khi handler ném
lỗi — nó chụp ảnh dữ liệu rồi trả lại. Không có phần này thì test kiểu "hỏng
giữa chừng thì không được ghi gì" sẽ đỏ ở `fam test` trong khi production chạy
đúng. Hai transaction **đồng thời** trên memory thì xếp hàng chạy lần lượt
(SQL thật cho chạy song song) — kết quả giống nhau, chỉ khác tốc độ, và test
thì không đo tốc độ transaction trên memory.

**MongoDB một node thì `transaction()` ném lỗi**, không giả vờ — xem
[mongodb.md](mongodb.md#không-có-transaction-và-làm-gì-thay-thế) để biết dùng gì
thay.

**Kiểu lỗi khác nhau giữa hai backend.** Vi phạm khoá ngoại: `memory` ném
`ConflictError`, `sqlite`/`postgres` ném `IntegrityError` của SQLAlchemy. Ở
tầng HTTP thì cả hai đều thành 409 nên không thấy khác biệt; bắt lỗi trong
code (worker, service) thì đừng bắt riêng `ConflictError`.

**Đừng giữ transaction trong lúc gọi mạng.** Gọi API bên ngoài, chờ MQTT, ngủ
— tất cả nằm ngoài khối. Với SQLite, một transaction giữ khoá ghi quá 5 giây
là các request khác nhận `database is locked`.

| Đang ở | Có sẵn transaction? | Cần làm gì |
|---|---|---|
| HTTP handler | có, cả request | không gì cả |
| worker / job / cron / script | **không** | `async with db.transaction():` |
| trong `transaction()` khác | có | khối lồng thành `SAVEPOINT` |
| `memory` | có (chụp ảnh) | như trên |
| [`mongodb`](mongodb.md#không-có-transaction-và-làm-gì-thay-thế) | **không có** | gộp vào một document |

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
