# Ghi nhiều bảng: cùng thành công hoặc cùng không

> **Database chia làm năm trang.** Bạn đang ở **transaction.md**.
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
| "**Ghi 2 bảng, hỏng thì huỷ cả hai**" | [Transaction](#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không) |
| "Trong HTTP handler có cần bọc không" | [Lưu ý](#lưu-ý) |
| "Huỷ giữa chừng mà không ném lỗi" | `await tx.rollback()` — [xem dưới](#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không) |
| "Tôi quen `queryRunner` của TypeORM" | [Transaction](#transaction--ghi-nhiều-bảng-thì-cùng-thành-công-hoặc-cùng-không) |
| "MongoDB thì sao" | [mongodb.md](mongodb.md#không-có-transaction-và-làm-gì-thay-thế) |

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

