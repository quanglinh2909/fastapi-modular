# Khai báo bảng dữ liệu

Entity là một `@dataclass` mang `@entity()` — khung đọc nó ra để tạo bảng, sinh
khoá ngoại, và cho bạn lọc bằng toán tử thường (`Camera.score >= 0.9`).

> **Database chia làm năm trang.** Bạn đang ở **entity.md**.
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
| "Khai một bảng dữ liệu mới" | [Khai báo entity](#khai-báo-entity) |
| "Sự kiện phải thuộc về một camera" | [Khoá ngoại](#khoá-ngoại-nối-hai-bảng-với-nhau) |
| "**Xoá camera thì xoá luôn sự kiện của nó**" | [`CASCADE`](#cascade--xoá-cha-thì-con-đi-theo) |
| "**Xoá khu vực thì camera ở lại, chỉ mất chỗ gắn**" | [`SET NULL`](#set-null--con-ở-lại-mất-chỗ-gắn) |
| "Còn hoá đơn thì KHÔNG cho xoá khách hàng" | [`RESTRICT`](#restrict--chặn-không-cho-xoá) |
| "**Xoá cha mà cháu vẫn còn**" | [Cascade dừng giữa chừng](#cascade-dừng-giữa-chừng-database-chưa-biết-khoá-ngoại) |
| "Cột mã thiết bị chỉ được dài 50 ký tự" | [Độ dài cột chữ](#độ-dài-cột-chữ-varchar50-và-text) |
| "Trường ghi chú có thể rất dài" | [Độ dài cột chữ](#độ-dài-cột-chữ-varchar50-và-text) |
| "Email không được trùng" | [Ràng buộc duy nhất và index](#ràng-buộc-duy-nhất-và-index) |
| "Truy vấn chậm, cần index" | [Ràng buộc duy nhất và index](#ràng-buộc-duy-nhất-và-index) |
| "`created_at` / `updated_at` ai đặt" | [Dấu thời gian](#dấu-thời-gian) |
| "Hỏng rồi, tra ở đâu" | [Hỏng thì tra ở đây](#hỏng-thì-tra-ở-đây) |

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

### Tham số của `@entity`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `name` | không | *(tên class viết thường + `s`)* | tên bảng/collection: `@entity(name="camera_logs")` |
| `unique` | không | `()` | cột phải duy nhất. Một chuỗi = một cột; tuple = duy nhất theo CỤM |
| `indexes` | không | `()` | cột hay dùng để lọc; cũng nhận cụm, và **thứ tự cột trong cụm rất quan trọng** |

Cả `unique` và `indexes` tạo ràng buộc **dưới database**, không phải kiểm trong
service — xem [Duy nhất và index](#duy-nhất-và-index).

### Tham số của `reference`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `target` | **có** | — | class entity của bảng CHA, truyền ở vị trí đầu: `reference(Camera)` |
| `on_delete` | không | `"RESTRICT"` | xoá cha thì con thế nào: `CASCADE` / `SET NULL` / `SET DEFAULT` / `RESTRICT` |
| `column` | không | `"id"` | cột bên bảng cha được trỏ tới; hầu như luôn là khoá chính |

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
đối tượng nặng thêm một byte nào — xem [Tra cứu](repository.md#tra-cứu).

**Tên bảng mặc định là tên class viết thường + `s`**: `Camera` → `cameras`. Đổi
bằng `@entity(name="camera_list")`.

**Kiểu trường quyết định kiểu cột.** Năm kiểu được ánh xạ thật:

| Khai trong Python | Cột trong SQL |
|---|---|
| `str` | `VARCHAR` không giới hạn — đặt độ dài hay đổi sang `TEXT` bằng [`column()`](#độ-dài-cột-chữ-varchar50-và-text) |
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

## Độ dài cột chữ: `VARCHAR(50)` và `TEXT`

`str` mặc định thành `VARCHAR` không giới hạn. Muốn database chặn dữ liệu quá
dài, hoặc muốn một cột `TEXT` cho đoạn mô tả dài, khai bằng `column()` đặt vào
`metadata=` của trường:

```python
# src/api/camera/entities/camera_model.py
from dataclasses import dataclass, field

from fastapi_modular import Entity, column, entity


@entity()
@dataclass(slots=True)
class Camera(Entity):
    id: str
    code: str = field(default="", metadata=column(length=50))    # VARCHAR(50)
    note: str = field(default="", metadata=column(text=True))    # TEXT
    name: str = ""                                               # VARCHAR như cũ
```

Tương đương `@Column({ length: 50 })` và `@Column({ type: "text" })` của TypeORM.

### Tham số của `column`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `length` | một trong hai | `None` | Số ký tự tối đa. Cột thành `VARCHAR(n)`, và khung chặn chuỗi dài hơn ngay lúc ghi |
| `text` | một trong hai | `False` | Cột thành `TEXT` — không giới hạn độ dài |

Cả hai đều là **keyword-only**, và phải khai đúng một trong hai: `column()`
rỗng hay `column(length=50, text=True)` đều bị chặn ngay lúc khai báo.

**Quá dài thì bị chặn ở khung, không đợi database.** Ghi 63 ký tự vào cột khai
`length=50` cho lỗi **400** với câu nói rõ chỗ sai:

```
Camera.code dài 63 ký tự, quá 50 ký tự đã khai bằng `column(length=50)`.
Cắt bớt trước khi ghi, hoặc nâng độ dài trong entity rồi chạy migration đổi cột.
```

Chặn cả trên đường `save()` lẫn `update()` / `update_where()`, và **không có gì
được ghi** khi bị chặn.

**Vì sao khung phải tự chặn: chỉ Postgres báo lỗi.** Đo được — ghi 60 ký tự vào
`VARCHAR(50)`: SQLite nhận bình thường, Postgres ném
`StringDataRightTruncationError`. MongoDB thì không có khái niệm độ dài. Không
chặn ở tầng khung thì `fam test` trên SQLite xanh còn production Postgres đổ,
đúng loại lỗi khó tìm nhất.

**Enum đếm theo `.value`, không phải theo tên.** Cột Enum lưu bằng `.value` nên
`column(length=4)` đo trên chuỗi được lưu.

**Chung trường với khoá ngoại thì gộp hai dict bằng `|`:**

```python
camera_id: str = field(default="", metadata=reference(Camera) | column(length=36))
```

**`length` chỉ đặt được cho cột chữ.** Đặt lên `int` / `float` / `datetime` thì
app **chết ngay lúc khởi động** với câu "Độ dài và TEXT chỉ đặt được cho cột chữ
(`str` hoặc `Enum`)" — sai kiểu thì hỏng ngay, không im lặng bỏ qua.

**Đổi độ dài của cột ĐÃ CÓ phải làm bằng migration.** Khung không tự
`ALTER COLUMN` — mỗi database một cú pháp, và phép đổi có thể khoá bảng rất
lâu. Với `APP_DB__SCHEMA_MODE=sync` bạn chỉ nhận một cảnh báo:

```
db.column_type_mismatch  column='cameras.code: VARCHAR(8) -> VARCHAR(64)'
```

Cột cũ có `VARCHAR(50)` mà entity để `str` trơn thì **không** bị kêu — nếu
không, mọi bảng đang có sẵn đều nhận cảnh báo không ai sửa được.

**MongoDB: `length` vẫn có hiệu lực, `text=True` không có nghĩa gì.** Xem
[mongodb.md](mongodb.md#cái-không-dùng-được-trên-mongo).

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

## Hỏng thì tra ở đây

| Bạn thấy gì | Nguyên nhân |
|---|---|
| 409 `không có Camera nào mang id đó` lúc ghi | khoá ngoại trỏ tới bản ghi cha không tồn tại — [chặn dữ liệu rác](#nó-chặn-dữ-liệu-rác-ngay-lúc-ghi) |
| 409 lúc xoá cha | còn bản ghi con và khoá ngoại khai `RESTRICT` — [`RESTRICT`](#restrict--chặn-không-cho-xoá) |
| Xoá cha thì con đi theo, nhưng **cháu ở lại** | bảng cũ chưa có khoá ngoại thật dưới database — [Cascade dừng giữa chừng](#cascade-dừng-giữa-chừng-database-chưa-biết-khoá-ngoại) |
| `[warning] db.foreign_keys_stale` lúc khởi động | như trên: khoá ngoại trong entity chưa có dưới database |
| `db.indexes_missing` lúc khởi động | `schema_mode="off"` mà index chưa được tạo — ràng buộc duy nhất KHÔNG có hiệu lực |
| `db.index_failed` | dữ liệu cũ đã có bản trùng, không tạo được unique index — dọn rồi khởi động lại |
| 400 `… quá 50 ký tự đã khai bằng column(length=50)` | chuỗi dài hơn độ dài cột — [độ dài cột chữ](#độ-dài-cột-chữ-varchar50-và-text) |
| Khởi động chết: `Độ dài và TEXT chỉ đặt được cho cột chữ` | `column(...)` đặt lên trường `int`/`float`/`datetime` |
| `db.column_type_mismatch  'cameras.code: VARCHAR(8) -> VARCHAR(64)'` | đổi `length` trong entity — khung không tự `ALTER`, phải migration |
| Thêm trường mới, bản ghi cũ đọc ra lỗi | trường mới chưa có giá trị mặc định — [Bốn quy ước phải biết](#bốn-quy-ước-phải-biết) |

---
