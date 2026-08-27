# Hướng dẫn MongoDB

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Cho app chạy trên Mongo" | [Cài và chạy](#cài-và-chạy) |
| "Khai một collection mới" | [Khai báo entity](#khai-báo-entity) |
| "Đọc, ghi, xoá dữ liệu" | [Bộ lệnh dùng được](#bộ-lệnh-dùng-được) |
| "Lọc lớn hơn / nhỏ hơn / gần đúng" | [Truy vấn](#truy-vấn-lớnbé-like-in-null) |
| "**Chỉ lấy vài cột thôi**" | [Chọn cột trả về](#chọn-cột-trả-về) |
| "**Trả về camera kèm danh sách sự kiện**" | [Dữ liệu lồng nhau](#dữ-liệu-lồng-nhau) |
| "**Ghi 2 collection, hỏng thì huỷ cả hai**" | [Không có transaction](#không-có-transaction-và-làm-gì-thay-thế) |
| "Xoá cha thì con thế nào" | [Khoá ngoại](#khoá-ngoại-khung-tự-làm-không-phải-database) |
| "Sao câu lệnh này báo lỗi?" | [Cái KHÔNG dùng được](#cái-không-dùng-được-trên-mongo) |
| "Hỏng rồi, tra ở đâu" | [Hỏng thì tra ở đây](#hỏng-thì-tra-ở-đây) |

> Dùng SQLite/PostgreSQL? Xem [database.md](database.md). Trang này chỉ nói về
> MongoDB, và **có những thứ bên kia làm được mà bên này không** — đọc mục
> [Cái KHÔNG dùng được](#cái-không-dùng-được-trên-mongo) trước khi chọn Mongo.

---

## Cài và chạy

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
| `APP_DB__NAME` | không | `app` | tên database bên trong Mongo. Collection lấy theo tên entity: `users`, `devices` |

Dựng server nhanh bằng Docker:

```bash
docker run -d --name ss-mongo -p 27017:27017 mongo:7
```

### Kiểm xem nó chạy chưa

```bash
fam dev
curl localhost:8000/api/health/ready
# {"status":"ready","driver":"mongodb","database":true}
```

Log khởi động phải thấy hai dòng này:

```
db.connected      backend=mongodb database=app
db.indexes_ready  collections=['devices', 'users']
```

**Không thấy `db.indexes_ready`** nghĩa là chưa entity nào được quét thấy — file
entity phải nằm trong `src/api/<module>/entities/`.

Xem tận nơi:

```bash
docker exec ss-mongo mongosh --quiet app --eval 'db.getCollectionNames()'
```

---

## Khai báo entity

Giống hệt SQL — vẫn là dataclass thuần, không dính ORM:

```python
from dataclasses import dataclass, field
from datetime import datetime

from fastapi_modular import Entity, entity
from fastapi_modular.core.clock import utcnow


@entity(unique=["serial"], indexes=[("owner_id", "status")])
@dataclass(slots=True)
class Camera(Entity):
    id: str                                              # BẮT BUỘC
    name: str
    serial: str
    owner_id: str
    zone: str = ""
    status: str = "offline"
    fps: int = 25
    rtsp: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
```

Bảng ánh xạ kiểu và các quy ước (`id` tự sinh, `created_at`/`updated_at` khung
tự đóng dấu, đổi tên collection bằng `@entity(name=...)`) y như bên SQL — xem
[database.md](database.md#khai-báo-entity).

Ba điều **chỉ đúng với Mongo**:

**Trường `id` được lưu vào `_id` của document.** Không tốn thêm index, và tra
tay thì nhớ gõ `_id`:

```bash
docker exec ss-mongo mongosh --quiet app --eval 'db.cameras.findOne({_id: "cam-01"})'
```

**`unique=` và `indexes=` được tạo THẬT** lúc khởi động, bằng
`createIndex(..., unique=true)`. Đây là ràng buộc ở mức database, không phải
kiểm tra trong service — hai request đồng thời cùng ghi trùng `serial` thì một
cái nhận 409.

Tạo index hỏng (thường vì dữ liệu cũ đã trùng) thì app **vẫn chạy**, chỉ ghi log
lỗi — đừng bỏ qua nó:

```
db.index_failed  collection=cameras index=uq_cameras_serial
                 hint=dọn document trùng rồi khởi động lại
```

**`APP_DB__SCHEMA_MODE` không có tác dụng.** Mongo không có schema cố định nên
không cần migrate gì cả:

- Thêm trường: document cũ thiếu khoá đó, đọc ra dùng giá trị mặc định của entity.
- Xoá trường: document cũ vẫn còn khoá thừa dưới database, đọc ra thì bỏ qua.
  Muốn dọn thật: `db.cameras.updateMany({}, {$unset: {port: ""}})`.

---

## Bộ lệnh dùng được

Trong service, khai `Repository[X]` như mọi backend khác:

```python
from fastapi_modular import injectable
from fastapi_modular.infrastructure.database import Repository


@injectable
class CameraService:
    def __init__(self, repo: Repository[Camera]) -> None:
        self._repo = repo
```

| Gọi | Ra lệnh Mongo | Trả về |
|---|---|---|
| `await repo.get(id)` | `findOne({_id})` | entity hoặc `None` |
| `await repo.find(owner_id="u1", limit=20, offset=40)` | `find(...).skip().limit()` | `list[Camera]` |
| `await repo.find(order_by="name")` | `.sort("name", 1)` | tăng dần |
| `await repo.save(camera)` | `replaceOne({_id}, doc, upsert=true)` | entity (đã có `id`) |
| `await repo.delete(id)` | `deleteOne({_id})` + dọn con | `bool` |
| `await repo.delete_where(owner_id="u1")` | `deleteMany(...)` + dọn con | số đã xoá |
| `await repo.find_one(serial="A1")` | `find(...)` lấy cái đầu | entity hoặc `None` |
| `await repo.count(status="online")` | `countDocuments(...)` | `int` |
| `await repo.exists(serial="A1")` | `countDocuments(...)` | `bool` |

Hai quy ước dễ vấp, giống hệt bên SQL:

**Tham số lọc mang giá trị `None` bị BỎ QUA.** `find(owner_id=None)` trả về
**tất cả**, không phải "những cái có `owner_id` rỗng".

**Phân trang tự ghép từ `count` + `find`** — không có hàm `paginate()` sẵn:

```python
tong = await self._repo.count(owner_id=owner_id)
items = await self._repo.find(owner_id=owner_id, limit=size, offset=(page - 1) * size)
return Page(items=items, total=tong, limit=size, offset=(page - 1) * size)
```

**`id` để trống thì khung tự sinh** (UUID) lúc `save()`:

```python
cam = await self._repo.save(Camera(id="", name="Cổng chính", serial="A1", owner_id="u1"))
print(cam.id)     # "3f2a...", khung vừa sinh
```

---

## Truy vấn: lớn/bé, LIKE, IN, NULL

`find(**equals)` chỉ so bằng. Cần hơn thế thì dùng `repo.query()` — **chạy được
trên Mongo**, và mọi điều kiện đều đẩy xuống database chứ không lọc trong Python:

```python
from fastapi_modular.infrastructure.database import F, and_, or_

ket_qua = await (
    cameras.query()
    .where(Camera.fps >= 25)                  # >= <= > < == !=
    .like(Camera.name, "Cổng%")               # LIKE, phân biệt hoa thường
    .is_null(Camera.rtsp)                     # IS NULL
    .in_(Camera.zone, ["A", "B"])             # IN (...)
    .between(Camera.fps, 24, 30)              # BETWEEN, hai đầu tính vào
    .order_by_desc("created_at")
    .limit(20)
    .all()
)
```

Nhớ `class Camera(Entity)` thì mới viết được `Camera.fps >= 25`; chưa kế thừa
thì dùng `F(Camera).fps >= 25`.

Đủ bộ, chạy y hệt bên SQL:

| Việc | Viết |
|---|---|
| lọc, và nhánh OR | `.where(...)` · `.or_where(...)` |
| lớn/bé/bằng | `Camera.fps >= 25` · `fps__gte=25` |
| LIKE / bỏ qua hoa thường | `.like(X.name, "Cổng%")` · `.ilike(X.name, "cổng%")` |
| IN, NULL, BETWEEN | `.in_()` `.not_in()` `.is_null()` `.is_not_null()` `.between()` |
| lồng AND/OR | `where(or_(and_(a, b), c))` · `~` là NOT |
| sắp xếp, phân trang | `.order_by_asc()` `.order_by_desc()` `.limit()` `.offset()` |
| chọn cột | `.select(fields=…, exclude=…, rename=…, add=…)` |
| dữ liệu lồng nhau | `.include(X, …)` · `.nest_under(X, …)` |
| chạy | `await .all()` `.first()` `.one()` `.count()` `.exists()` |

```python
# "camera tên bắt đầu bằng 'cổng' và đang bật, HOẶC bất kỳ camera nào ở tầng 1"
# — lấy cái mới nhất
mot = await (cameras.query()
             .ilike(Camera.name, "cổng%").where(Camera.status == "online")
             .or_where(Camera.zone == "Tầng 1")
             .order_by_desc("created_at")
             .first())
```

Cách viết điều kiện — `and_`/`or_`/`~`, so cột với cột, bảy toán tử không có ký
hiệu — giống hệt bên SQL, xem
[database.md](database.md#truy-vấn-phức-tạp--join-lớnbé-null).

**NULL cư xử đúng như SQL, không như Mongo.** Đây là chỗ dịch sang Mongo dễ sai
nhất, nên nói rõ: `{n: {"$ne": 1}}` của Mongo **trả về cả document không có
trường `n`** (đo được), còn SQL thì `NULL != 1` là không-đúng nên loại. Khung
chèn thêm `$ne: null` cho `!=` và `not_in`, để cùng một câu lệnh cho cùng kết
quả trên postgres, sqlite, memory và mongo.

Muốn "lấy cả dòng đang để trống" thì nói thẳng bằng `is_null`:

```python
.where(or_(Camera.rtsp != "rtsp://a", is_null(Camera.rtsp)))
```

**`like` dịch sang `$regex`, và ký tự đặc biệt được escape.**
`like(name, "Kho.hàng")` tìm đúng dấu chấm, không phải "ký tự bất kỳ". `%` là
"bao nhiêu ký tự cũng được", `_` là "đúng một ký tự", không có `%` thì là so
khớp **cả chuỗi**.

**Thứ tự giữa các giá trị BẰNG NHAU không xác định.** `order_by_desc("score")`
với hai bản ghi cùng 0.95 thì Mongo và Postgres có thể trả khác thứ tự nhau.
Cần ổn định thì sắp thêm một cột nữa: `.order_by_desc("score").order_by_asc("id")`.

### Chọn cột trả về

```python
await cameras.query().select("id", "name").all()
# [{"id": "c1", "name": "Cổng chính"}, ...]

await cameras.query().select(exclude=["serial"]).all()       # đủ cột, trừ serial
await cameras.query().select(rename={"ma": "id"}).all()      # đủ cột, đổi tên id
await cameras.query().select(add={"ma": Camera.serial}).all()  # đủ cột, thêm một cột
```

Sinh ra projection thật của Mongo (`find(loc, {"name": 1})`), không phải lấy cả
document rồi cắt trong Python. `Enum` và `datetime` trong dict được ép kiểu y
như khi trả về entity — `datetime` luôn có `tzinfo` UTC.

### Dữ liệu lồng nhau

`include` và `nest_under` **chạy được trên Mongo**, vì chúng không cần `$lookup`:
mỗi cái là một câu lệnh `find({_id: {$in: [...]}})` nữa rồi ghép bằng Python.

```python
# mỗi camera kèm danh sách sự kiện
await cameras.query().include(Event).all()
# [{"id": "c1", ..., "events": [{...}, {...}]}]

# đảo chiều: lọc theo sự kiện, trả về camera
await events.query().where(Event.score >= 0.9).nest_under(Camera).all()
```

Đầy đủ tham số (`name=`, `fields=`, `exclude=`, `where=`, `order_by_*=`):
[database.md](database.md#dữ-liệu-lồng-nhau-include).

### Khi nào vẫn phải dùng `match=`

`match=` là một hàm Python, dùng cho điều kiện không có trong database — ví dụ
gọi một hàm để tính:

```python
ket_qua = await cameras.find(zone="A", match=lambda c: len(c.name) > 5)
```

**Nó kéo dữ liệu về rồi mới lọc**, và `limit` cũng chỉ cắt sau khi đã kéo về —
với collection lớn thì vừa chậm vừa tốn RAM. Lọc thô bằng `**equals` cho hẹp
lại trước, hoặc chuẩn hoá lúc ghi rồi lọc bằng cột.

---

## Không có transaction, và làm gì thay thế

MongoDB một node **không có transaction đa-document**. Khung nói thẳng thay vì
giả vờ:

```python
async with self._db.transaction():      # -> CapabilityNotSupportedError
    ...
```

```
MongoDB chỉ có transaction đa-document khi chạy replica set, và template không
bật. Cách khác: gộp dữ liệu cần ghi cùng lúc vào MỘT document (Mongo bảo đảm
nguyên tử ở mức một document), hoặc đổi APP_DB__DRIVER sang postgres/sqlite.
```

Nghĩa là ghi hai collection thì **không có cách nào huỷ cả hai**: ghi cái thứ
hai hỏng, cái thứ nhất đã nằm lại rồi. Ba đường đi:

**1. Gộp vào một document.** Mongo bảo đảm nguyên tử ở mức một document, nên thứ
gì phải "cùng đúng" thì để chung một chỗ:

```python
class Camera(Entity):
    ...
    lan_kiem_tra_cuoi: datetime | None = None    # thay vì một collection riêng
```

**2. Ghi cái quan trọng trước, cái phụ sau,** và chấp nhận cái phụ có thể thiếu:
log, thống kê, bản ghi phụ trợ. Đặt một job dọn định kỳ.

**3. Đổi sang `postgres`** nếu nghiệp vụ thật sự cần "cùng thành công hoặc cùng
không" — xem [database.md](database.md#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không).

Bật replica set thì Mongo có transaction, nhưng template chưa dùng tới nó.

---

## Khoá ngoại: khung tự làm, không phải database

`reference(...)` khai được và **chạy được** trên Mongo:

```python
from fastapi_modular import reference


@entity()
@dataclass(slots=True)
class Event(Entity):
    id: str
    label: str
    score: float
    camera_id: str = field(metadata=reference(Camera, on_delete="CASCADE"))
```

Xoá camera thì mọi `Event` của nó biến mất theo, đúng như bên SQL. Bốn hành vi
`on_delete` (`CASCADE`, `SET NULL`, `SET DEFAULT`, `RESTRICT`) đều có — chi tiết
từng cái ở [database.md](database.md#khoá-ngoại-nối-hai-bảng-với-nhau).

**Nhưng ai áp ràng buộc mới là chỗ khác nhau:**

| | postgres / sqlite | mongodb |
|---|---|---|
| Ai áp | chính database | **khung, bằng nhiều lệnh nối nhau** |
| Nguyên tử | có, trong cùng transaction | **không** |
| Ghi con trỏ tới cha không tồn tại | database chặn | **không chặn** |

Hai hệ quả phải biết trước:

**Chết giữa chừng thì còn dữ liệu nửa vời.** Xoá camera có 100 sự kiện: khung
xoá sự kiện trước rồi mới xoá camera. Tiến trình chết giữa chừng thì sự kiện đã
mất mà camera vẫn còn.

**Ghi rác không bị chặn.** `Event(camera_id="khong-co-that")` ghi được bình
thường trên Mongo, trong khi SQL và backend `memory` đều từ chối bằng lỗi 409.
Tức `fam test` xanh không có nghĩa là dữ liệu sạch trên Mongo — muốn chắc thì
tự kiểm trong service trước khi ghi.

---

## Cái KHÔNG dùng được trên Mongo

Query builder chạy được, **trừ bốn thứ** dưới đây. Cả bốn đều báo lỗi nói rõ
kèm cách thay, không có cái nào chạy tiếp rồi cho kết quả sai.

| Thứ | Trên Mongo | Thay bằng |
|---|---|---|
| `join` / `left_join` / `right_join` / `outer_join` | ném lỗi | `include` / `nest_under` — cả hai chạy được |
| `group_by` / `having` / `count()`, `avg()`… | ném lỗi | `db.x.aggregate([...])` tự viết qua motor |
| `.distinct()` | ném lỗi | không có JOIN thì dòng trùng cũng hiếm khi sinh ra |
| `async with db.transaction()` | ném lỗi | [xem mục trên](#không-có-transaction-và-làm-gì-thay-thế) |
| `.sql()` | không có | `mongosh` để xem truy vấn thật |
| `fam migrate` / `APP_DB__SCHEMA_MODE` | không cần | Mongo không có schema cố định |

Câu lỗi khi gọi `join`:

```
MongoDB không có JOIN. Cần dữ liệu của Camera thì dùng `.include(Camera)` (gắn
vào kết quả) hoặc `.nest_under(Camera)` (đảo chiều) — cả hai chạy được trên
Mongo. Cần LỌC theo cột của Camera thì phải đổi APP_DB__DRIVER sang
postgres/sqlite.
```

**JOIN không giả lập là quyết định có chủ ý.** Mongo có `$lookup`, nhưng nó trả
về **mảng lồng** chứ không phải dòng phẳng như JOIN. Làm cho giống thì đúng ở
demo và sai ở production, nên thà nói không — và ở Mongo, lồng dữ liệu vào một
document mới là cách làm đúng ngay từ đầu.

`group_by` thì chỉ là **chưa làm**, không phải không làm được: `$group` của
Mongo dịch được, nhưng NULL và nhóm rỗng cư xử khác SQL (`$sum` của nhóm rỗng
ra `0`, SQL ra `NULL`) nên cần làm cẩn thận. Cần gấp thì đi thẳng xuống motor.

**Cần dùng `$lookup` hay aggregation thật** thì vẫn đi thẳng xuống motor được:

```python
collection = self._repo._db.backend._collection(Camera)      # tạm thời
async for doc in collection.aggregate([{"$group": {"_id": "$owner_id", "n": {"$sum": 1}}}]):
    ...
```

Cách này bỏ qua mọi thứ khung làm giúp (ép kiểu, `_id` ↔ `id`, dọn khoá ngoại),
nên chỉ dùng cho truy vấn đọc.

---

## Injection: chỗ Mongo nguy hiểm hơn SQL

Mongo không ghép câu lệnh bằng chuỗi nên không có "SQL injection" theo nghĩa
quen thuộc. Nhưng nó có một cửa mà SQL không có: **một giá trị dạng dict được
hiểu là TOÁN TỬ**.

Đo trên MongoDB 7 thật, trước khi khung chặn:

```python
await repo.find(name="an", token={"$ne": ""})    # -> trả về bản ghi của người khác
```

Kẻ tấn công gửi JSON `{"name": "an", "token": {"$ne": ""}}` là qua được cửa
đăng nhập, vì điều kiện `token` không còn là so bằng nữa. Cửa thứ hai còn nặng
hơn: một **khoá** tên `$where` khiến Mongo chạy JavaScript ngay trên server.

Cả hai giờ bị chặn ở tầng dùng chung, và ba backend từ chối giống hệt nhau:

```
Điều kiện 'token': giá trị chứa toán tử của database ($ne). Gần như luôn là dữ
liệu người dùng gửi lên thẳng vào truy vấn — ép kiểu nó về str/int/bool trước.

Camera không có trường '$where'. Có: created_at, fps, id, name, owner_id, …
```

**Cách chắc chắn nhất vẫn là ép kiểu ở cửa vào**: khai DTO bằng pydantic
(`token: str`) thì một dict không bao giờ tới được truy vấn.

`like`/`ilike` dịch sang `$regex` và **escape mọi ký tự đặc biệt của regex**,
nên mẫu người dùng nhập không thành biểu thức. Riêng `%` vẫn là ký tự đại diện
của LIKE — người dùng gõ `%` sẽ quét cả collection.

---

## Lưu ý

**`datetime` đọc ra luôn có `tzinfo` UTC.** Mongo lưu datetime không kèm múi
giờ; khung gắn lại UTC lúc đọc để ba driver cho cùng một dạng, và để một
response không lẫn lộn `"...Z"` với `"..."` không hậu tố.

**`Enum` lưu bằng `.value`** (chuỗi) cho dễ đọc bằng `mongosh` và dễ đổi về sau.
Đọc ra vẫn là Enum.

**Mất kết nối thì motor tự dò lại server.** Template chỉ siết
`serverSelectionTimeoutMS` để request không treo 30 giây khi Mongo chết — request
đang chạy nhận **503**, không phải 500. Xem
[database.md](database.md#mất-kết-nối-database).

**Giữ `APP_DB__CONNECT_TIMEOUT_SECONDS` ≥ 10s nếu chạy replica set** — lúc bầu
primary mới cần chừng đó thời gian.

---

## Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân |
|---|---|
| `Query builder chưa hỗ trợ MongoDB` | gọi `repo.query()`; đổi sang `repo.find(...)` |
| `CapabilityNotSupportedError` khi vào `db.transaction()` | Mongo một node không có transaction đa-document |
| `db.index_failed` lúc khởi động | dữ liệu cũ đã trùng, `unique` không tạo được — dọn document trùng rồi khởi động lại |
| Không thấy `db.indexes_ready` | file entity chưa nằm trong `src/api/<module>/entities/` |
| Ghi được bản ghi con trỏ tới cha không tồn tại | Mongo không có khoá ngoại thật; khung chỉ dọn lúc XOÁ |
| `/health/ready` trả `database: false` | sai DSN, hoặc server chưa lên — xem log `db.unreachable_at_startup` |
| Xoá cha xong con vẫn còn | tiến trình chết giữa chừng lúc dọn; chạy lại lệnh xoá |
| `ServerSelectionTimeoutError` | không tới được server: sai host/cổng, hoặc firewall |

---

## Tra cứu

Chỉ đọc khi cần con số cụ thể.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `APP_DB__DRIVER` | `memory` | đặt `mongodb` |
| `APP_DB__DSN` | `(trống)` | `mongodb://…` hoặc `mongodb+srv://…` |
| `APP_DB__NAME` | `app` | tên database trong Mongo |
| `APP_DB__CONNECT_TIMEOUT_SECONDS` | `10` | vừa là hạn mở kết nối, vừa là hạn chọn server |
| `APP_DB__QUERY_TIMEOUT_SECONDS` | `15` | hạn đọc socket cho một câu lệnh |
| `APP_DB__STARTUP_RETRIES` | `10` | thử ping bao nhiêu lần lúc khởi động |
| `APP_DB__STARTUP_RETRY_DELAY_SECONDS` | `1.0` | cách nhau bao lâu |

Bảng đầy đủ mọi biến `APP_DB__*`: [config.md](config.md).

| Ánh xạ | |
|---|---|
| entity → collection | tên class viết thường + `s`, đổi bằng `@entity(name=…)` |
| `id` → `_id` | không tạo thêm index |
| `Enum` → chuỗi | lưu `.value` |
| `unique=` → `createIndex(unique: true)` | tạo lúc khởi động |
| `indexes=` → `createIndex` | tạo lúc khởi động |

Chạy đúng bộ test của template trên Mongo thật:

```bash
TEST_MONGO_DSN='mongodb://127.0.0.1:27017' fam test
```
