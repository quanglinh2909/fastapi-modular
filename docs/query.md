# Truy vấn phức tạp (query builder)

`repo.query()` sinh **SQL thật** — JOIN, so sánh lớn/bé, lọc NULL, gộp nhóm,
dữ liệu lồng nhau. Xem câu lệnh sinh ra bằng `.sql()` bất cứ lúc nào.

> **Database chia làm năm trang.** Bạn đang ở **query.md**.
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
| "Lọc lớn hơn, nhỏ hơn, NULL, nối bảng" | [Làm thế nào](#làm-thế-nào) |
| "**Điều kiện này HOẶC điều kiện kia**" | [`or_where`](#or-or_where-mở-nhánh-mới) |
| "**Mỗi camera có bao nhiêu sự kiện**" | [Gộp nhóm](#gộp-nhóm-đếm-tính-trung-bình) |
| "**Chỉ lấy camera có hơn 5 sự kiện**" | [HAVING](#having--lọc-theo-kết-quả-gộp) |
| "Camera cha của camera này tên gì" | [Nối bảng với chính nó](#nối-bảng-với-chính-nó-self-join) |
| "Giữ cả camera chưa có sự kiện nào" | [Bốn kiểu nối](#bốn-kiểu-nối-bốn-method) |
| "**Trả về camera kèm danh sách sự kiện của nó**" | [Dữ liệu lồng nhau](#dữ-liệu-lồng-nhau-include) |
| "**Lọc theo sự kiện nhưng trả về camera ở ngoài**" | [`nest_under`](#đảo-chiều-nest_under) |
| "**Camera > log > item, lồng ba tầng**" | [Lồng nhiều mức](#lồng-nhiều-mức) |
| "**Bảng nhiều cột quá, tôi muốn bỏ bớt một cột**" | [`select(exclude=…)`](#chọn-cột-trả-về) |
| "**Đổi tên trường trả về**" | [`select(rename=…)`](#chọn-cột-trả-về) |
| "Chỉ lấy dòng có cột này để trống" | [`is_null`](#like-in-is-null-between--bảy-toán-tử-không-có-ký-hiệu) |
| "Tìm theo tên gần đúng" | [`like` / `ilike`](#like-in-is-null-between--bảy-toán-tử-không-có-ký-hiệu) |
| "**Có chống được SQL injection không**" | [Injection](#injection-cái-gì-được-chặn-chặn-ở-đâu) |

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
backend = repo._db.backend                       # lớp SQL bên dưới repository
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

### Tham số của `include`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `entity` | **có** | — | class bảng muốn lấy kèm, truyền ở vị trí đầu: `include(Event)` |
| `name` | không | *(tên class viết thường, thêm `s` nếu là list)* | tên trường trong kết quả: `include(Event, name="su_kien")` |
| `on` | không | *(suy từ khoá ngoại)* | cột dùng để ghép, khi hai bảng có nhiều hơn một khoá ngoại hoặc bảng tự trỏ về chính nó |
| `fields` | không | `()` *(mọi cột)* | chỉ lấy những cột này. Nhận cả `{"tên mới": cột}` |
| `exclude` | không | `()` | mọi cột TRỪ những cột này |
| `rename` | không | `None` | giữ đủ cột, chỉ đổi tên vài cột: `rename={"nhan": "label"}` |
| `where` | không | `None` | lọc bảng được lấy kèm |
| `order_by_asc` / `order_by_desc` | không | `None` | sắp bảng được lấy kèm |

`where=` và `order_by_*=` **không dùng được** khi bảng đó nằm trong chuỗi
[`nest_under`](#đảo-chiều-nest_under) — ở đó nó lấy đúng bản ghi liên quan của
từng nhóm, không có gì để lọc.

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

### Tham số của `nest_under`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `*entities` | **có** | — | các bảng, kể **từ NGOÀI vào TRONG**: `nest_under(Camera, CameraLog, ItemLog)` |
| `name` | không | `""` | đặt tên cho danh sách bản ghi của bảng GỐC. Chỉ dùng được khi truyền MỘT bảng |
| `on` | không | *(suy từ khoá ngoại)* | cột ghép, cần khi bảng tự trỏ về chính nó |
| `fields` / `exclude` / `rename` | không | `()` / `()` / `None` | cột của bảng vừa nêu. Chỉ dùng được khi truyền MỘT bảng — nhiều bảng thì khai bằng `include(X, fields=…)` |

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
| `async with db.transaction() as tx:` · `await tx.rollback()` | xem [Transaction](transaction.md#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không) |
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

