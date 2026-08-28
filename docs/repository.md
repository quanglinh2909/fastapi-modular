# Đọc và ghi dữ liệu (Repository)

`Repository[X]` là bộ CRUD sẵn có cho một entity, giống nhau ở mọi backend —
service của bạn không cần biết bên dưới là SQLite, PostgreSQL hay MongoDB.

> **Database chia làm năm trang.** Bạn đang ở **repository.md**.
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
| "Đọc/ghi dữ liệu trong service" | [Dùng Repository trong code](#dùng-repository-trong-code) |
| "Tra nhanh có những hàm gì" | [Bộ hàm có sẵn](#bộ-hàm-có-sẵn) |
| "**Sửa một dòng mà không phải đọc nó về trước**" | [`update`](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| "**Sửa hàng loạt: mọi camera Tầng 1 thành offline**" | [`update_where`](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| "**Truyền thẳng DTO của PATCH vào để sửa**" | [`update`](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| "Lọc lớn hơn, nhỏ hơn, NULL, nối bảng" | [query.md](query.md) |
| "Ghi 2 bảng, hỏng thì huỷ cả hai" | [transaction.md](transaction.md) |

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
| `update(id, changes, **set)` | **Sửa một bản ghi** (nhận cả DTO), trả về chính nó — [xem dưới](#sửa-dữ-liệu-không-cần-đọc-về-trước) |
| `update_where(dieu_kien, changes, **set)` | Sửa nhiều theo điều kiện, trả số dòng khớp |
| `delete(id)` | Xoá một, trả `True/False` |
| `delete_where(**equals, match=)` | Xoá nhiều, trả số bản ghi |
| `query()` | Builder cho JOIN, lớn/bé, NULL — xem [mục dưới](query.md#truy-vấn-phức-tạp--join-lớnbé-null) |

### Tham số của `find` / `find_one` / `count` / `exists` / `delete_where`

Cả năm nhận cùng một bộ:

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `**equals` | không | *(không lọc)* | điều kiện **so bằng**: `find(zone="T1", status="on")` = AND. Giá trị `None` bị BỎ QUA, không phải "bằng NULL" |
| `match` | không | `None` | hàm Python lọc thêm: `match=lambda o: o.score > 0.9`. Chạy TRONG Python nên phải kéo cả bảng về — xem [Hai quy ước dễ vấp](#hai-quy-ước-dễ-vấp) |
| `order_by` | không | `"created_at"` | tên cột để sắp; chỉ `find` có |
| `limit` | không | `None` *(không giới hạn)* | số dòng tối đa; chỉ `find` có |
| `offset` | không | `0` | bỏ qua bao nhiêu dòng đầu; chỉ `find` có |

Cần `>=`, `LIKE`, `IN`, JOIN thì không dùng `find` nữa mà dùng
[`query()`](query.md#truy-vấn-phức-tạp--join-lớnbé-null) — nó chạy DƯỚI database.

### Tham số của `update` và `update_where`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `id_` *(của `update`)* | **có** | — | id của bản ghi cần sửa. Truyền dict vào đây là lỗi — đó là việc của `update_where` |
| `where` *(của `update_where`)* | **có** | — | dict hoặc DTO, điều kiện **so bằng**. Rỗng thì bị chặn |
| `changes` | không | `None` | giá trị cần ghi: dict hoặc **DTO** (đọc bằng `exclude_unset=True`) |
| `**set_fields` | không | — | cách viết gọn của `changes`; gộp được với nó |
| `match` *(chỉ `update_where`)* | không | `None` | lọc thêm bằng Python; cũng là cách nói rõ "tôi cố ý sửa cả bảng" |

### Sửa dữ liệu không cần đọc về trước

**Sửa một bản ghi và lấy lại chính nó** — cả handler PATCH gói trong một dòng:

```python
# src/api/cameras/camera_service.py
@injectable
class CameraService:
    def __init__(self, repo: Repository[Camera]) -> None:
        self._repo = repo

    async def update_camera(self, camera_id: str, payload: CameraUpdate) -> Camera:
        camera = await self._repo.update(camera_id, payload)
        if camera is None:
            raise NotFoundError(f"Không tìm thấy camera {camera_id}")
        return camera
```

Trả về **chính bản ghi sau khi sửa**, hoặc `None` nếu không có id đó — cùng quy
ước với `get()`. Giá trị truyền bằng DTO, dict, kwargs, hay trộn cả ba:

```python
await cameras.update("cam-01", payload)                    # DTO
await cameras.update("cam-01", {"name": "Cổng chính"})     # dict
await cameras.update("cam-01", status="offline")           # kwargs
await cameras.update("cam-01", payload, status="offline")  # trộn
```

> **Chỉ field client THỰC SỰ gửi mới bị ghi.** DTO đọc bằng `exclude_unset=True`,
> y như [`apply_changes`](#hai-quy-ước-dễ-vấp). Đừng tự `payload.model_dump()`
> rồi truyền vào: dump trần trả về cả field không gửi (mặc định `None` của
> `partial_of`), nên PATCH đổi mỗi `name` sẽ ghi `None` đè lên mọi cột còn lại.
> Gửi `null` tường minh thì vẫn xoá được cột — `null` đã gửi là đã "set".

**Sửa nhiều dòng theo điều kiện** là hàm còn lại, `update_where` — cùng cặp với
`delete` / `delete_where`:

```python
# mọi camera ở Tầng 1 chuyển sang offline
so_dong = await cameras.update_where({"zone": "Tầng 1"}, status="offline")

# nhiều điều kiện = AND
await cameras.update_where({"zone": "T1", "status": "online"}, threshold=0.9)
```

| Hàm | Điều kiện | Trả về |
|---|---|---|
| `update(id, …)` | **một id** (chuỗi) | bản ghi đã sửa, hoặc `None` |
| `update_where(dieu_kien, …)` | **dict/DTO**, so bằng | số dòng khớp |

> **Truyền nhầm giữa hai hàm thì IDE gạch đỏ ngay lúc gõ**, vì kiểu tham số đầu
> khác nhau. `update({"zone": "T1"}, ...)` sẽ nhận
> `Expected type 'str', got 'dict[str, str]' instead` — đổi sang `update_where`.

#### Lưu ý

**Nó chỉ GHI ĐÈ, không đọc giá trị cũ.** Cần tính từ giá trị đang có
(`so_lan = so_lan + 1`) thì đây không phải chỗ — đọc rồi ghi trong
`async with db.transaction():`, hoặc dùng
[`RedisClient.incr`](redis.md#khoá--giá-trị) nếu chỉ là bộ đếm.

**Cần đọc bản ghi cũ trước khi quyết thì vẫn dùng `get` + `save`.** Kiểm trùng
email, so giá trị cũ với mới, ghi log "đổi từ X sang Y" — những việc đó cần bản
ghi trong tay, xem `src/api/users/user_service.py`.

**Không đổi được `id`.** Nó là danh tính bản ghi và là thứ khoá ngoại của bảng
khác đang trỏ tới; muốn đổi thật thì tạo bản ghi mới rồi chuyển các bản ghi con
sang.

**`update_where({}, ...)` bị chặn.** Điều kiện rỗng gần như luôn là biến rỗng do
lỗi lập trình chứ không phải ý định sửa cả bảng. Cố ý thì nói rõ:

```python
await cameras.update_where({}, zone="X", match=lambda _: True)
```

**`update_where` trả số dòng chứ không trả dữ liệu.** Cố ý: một câu lệnh có thể
khớp hàng trăm nghìn dòng, đọc hết về chỉ để trả cho người gọi là thứ không nên
xảy ra ngầm. Cần dữ liệu thì `find(...)` sau đó.

**Điều kiện của `update_where` chỉ so BẰNG.** Cần `>=`, `LIKE`, `IN` thì lọc
bằng [`query()`](query.md#truy-vấn-phức-tạp--join-lớnbé-null) rồi `update` theo từng id,
hoặc truyền `match=` (lọc bằng Python nên phải đọc dòng về trước, chậm hơn).

**Truyền entity vào thì bị từ chối**, kèm lời chỉ đường: đã có sẵn cả bản ghi
thì `save(obj)` mới đúng.

**Ràng buộc vẫn được áp trên cả ba backend.** Sửa cột khoá ngoại sang giá trị
không tồn tại, hay làm trùng cột `unique`, đều bị từ chối bằng 409 — SQL áp
ràng buộc cho câu `UPDATE` chứ không riêng `INSERT`, và `memory`/`mongodb` được
làm cho giống hệt.

`updated_at` tự đóng dấu ở cả hai hàm, y như `save()`.

#### Hỏng thì tra ở đây

| Bạn thấy gì | Nguyên nhân |
|---|---|
| IDE: `Expected type 'str', got 'dict[...]'` | đang gọi `update` với điều kiện — đổi sang `update_where` |
| `update` sửa MỘT bản ghi theo id nên tham số đầu phải là chuỗi | như trên, lúc chạy |
| `update_where` nhận điều kiện dạng dict hoặc DTO | ngược lại: đang truyền id cho `update_where` — dùng `update(id, ...)` |
| `update` trả `None` | không có bản ghi nào mang id đó (không phải lỗi ghi) |
| `update_where` trả `0` | không dòng nào khớp điều kiện |
| Sửa một field mà các field khác **thành `null`** | đã tự `payload.model_dump()` — truyền thẳng `payload` vào |
| `… không có trường 'x'` | gõ sai tên cột; khung chặn thay vì báo "đã sửa" rồi không sửa gì |
| `… không có giá trị nào để ghi` | DTO không có field nào được gửi lên, hoặc quên truyền giá trị |
| `… sẽ sửa MỌI dòng` | `update_where` với điều kiện rỗng — xem mục Lưu ý |
| `… quá 50 ký tự đã khai bằng column(length=50)` | chuỗi dài hơn độ dài cột — [độ dài cột chữ](entity.md#độ-dài-cột-chữ-varchar50-và-text) |
| 409 khi sửa | vi phạm khoá ngoại hoặc cột `unique` |

#### Tra cứu

Chỉ đọc nếu bạn đang cân nhắc có nên đổi từ `get` + `save` sang không.

`update(id)` tốn **một** câu lệnh, vòng `get` + `save` tốn **hai** — đếm bằng
câu lệnh thật gửi xuống driver:

```
update(id)      : 1 câu   UPDATE cameras SET name=?, updated_at=? WHERE … RETURNING *
get + save      : 2 câu   SELECT … / UPDATE …
```

Nhờ `UPDATE ... RETURNING *` (PostgreSQL, SQLite từ 3.35 — bản cũ hơn thì khung
tự lùi về hai câu) và `find_one_and_update` của MongoDB. Bản trả về **đọc từ
database sau khi ghi**, không phải bản đang có trong bộ nhớ: database có thể tự
đổi thêm (giá trị mặc định, trigger), và thứ trả cho client phải là thứ đang
thật sự nằm trong bảng.

### Hai quy ước dễ vấp

**`None` nghĩa là "không lọc"**, không phải "bằng NULL":

```python
await repo.find(reviewed_at=None)   # trả về TẤT CẢ, không phải bản ghi chưa duyệt
await repo.query().where(reviewed_at__isnull=True).all()   # đây mới là lọc NULL
```

**`match=` chạy trong Python, KHÔNG đẩy được xuống database.** Backend lấy **cả
bảng** về rồi mới lọc, và `limit` cũng chỉ cắt sau khi đã lấy về — nên với bảng
lớn thì vừa chậm vừa tốn RAM.

Gần như mọi thứ trước đây phải dùng `match=` thì nay viết được bằng
[query builder](query.md#truy-vấn-phức-tạp--join-lớnbé-null), và nó chạy dưới database:

| Cần gì | Đừng | Hãy |
|---|---|---|
| lớn hơn / nhỏ hơn | `match=lambda o: o.score >= 0.8` | `.query().where(score__gte=0.8)` |
| bằng NULL | `match=lambda o: o.reviewed_at is None` | `.query().where(reviewed_at__isnull=True)` |
| nằm trong danh sách | `match=lambda o: o.label in [...]` | `.query().where(label__in=[...])` |
| nối bảng khác | *(không làm được)* | `.query().join(Camera)` |

`match=` chỉ còn đúng cho điều kiện không có trong SQL — ví dụ gọi một hàm Python
để tính. Khi dữ liệu lớn, cách tốt hơn là chuẩn hoá lúc ghi (lưu sẵn
`email_lower`) rồi lọc bằng cột đó.

---

