# Nghe thay đổi của entity (Subscriber)

Chạy code mỗi khi một bản ghi được thêm, sửa, xoá hay đọc lên — không phải nhớ
gọi `emit()` ở từng chỗ trong service.

Đây là bản của **EntitySubscriber** bên TypeORM, khai như một provider đúng kiểu
NestJS. Ai quen `@EventSubscriber()` + `listenTo()` thì đọc thẳng
[Đối chiếu với TypeORM](#đối-chiếu-với-typeorm).

> **Database chia làm sáu trang.** Bạn đang ở **subscribers.md**.
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
| "Thêm một camera thì ghi luôn một dòng nhật ký" | [Làm thế nào](#làm-thế-nào) |
| "Sửa bản ghi thì **so xem đổi những trường nào**" | [`event` có gì](#event-có-gì) |
| "Nghe **mọi entity**, không riêng một bảng" | [Nghe một entity hay tất cả](#nghe-một-entity-hay-tất-cả) |
| "Xoá bản ghi thì dọn file đính kèm" | [Khi nào handler chạy](#khi-nào-handler-chạy) |
| "**Xoá camera kéo theo log của nó** — biết dòng nào bị kéo theo" | [Xoá cha kéo theo con](#xoá-cha-kéo-theo-con) |
| "Nhóm các sự kiện của **cùng một lệnh xoá**" | [Xoá cha kéo theo con](#xoá-cha-kéo-theo-con) — `operation_id` |
| "Handler hỏng thì lời ghi có bị huỷ không" | [Lưu ý](#lưu-ý) |
| "Tôi quen TypeORM, cái gì giống cái gì khác" | [Đối chiếu với TypeORM](#đối-chiếu-với-typeorm) |
| "Viết rồi mà không thấy chạy" | [Hỏng thì tra ở đây](#hỏng-thì-tra-ở-đây) |

---

## Làm thế nào

```python
# src/api/cameras/camera_subscriber.py
from fastapi_modular import get_logger, injectable
from fastapi_modular.infrastructure.database import EntityEvent, entity_subscriber
from src.api.cameras.entities.camera_model import Camera

log = get_logger(__name__)


@injectable
@entity_subscriber(Camera)
class CameraSubscriber:
    async def after_insert(self, event: EntityEvent) -> None:
        log.info("camera.created", camera_id=event.id, name=event.entity.name)

    async def after_update(self, event: EntityEvent) -> None:
        if "status" in event.updated_columns:
            log.info(
                "camera.status_changed",
                camera_id=event.id,
                truoc=event.database_entity.status,
                sau=event.entity.status,
            )

    async def after_remove(self, event: EntityEvent) -> None:
        log.info("camera.removed", camera_id=event.id, name=event.entity.name)
```

Không phải đăng ký ở đâu cả: có `@injectable` và nằm dưới `src/api/` là khung tự
quét, y như controller hay service.

**Cần ghi xuống database trong handler** thì tiêm repository như thường, hoặc mở
từ `event.database`:

```python
    async def after_insert(self, event: EntityEvent) -> None:
        await Repository(CameraLog, event.database).save(
            CameraLog(id="", camera_id=event.id, action="created")
        )
```

`event.database` đi chung transaction với lời ghi vừa xảy ra — tương đương
`event.manager` của TypeORM. Nhờ vậy ghi hỏng thì cả hai cùng bị huỷ.

### Nghe một entity hay tất cả

```python
@entity_subscriber(Camera)           # chỉ Camera
@entity_subscriber(Camera, Zone)     # hai entity
@entity_subscriber()                 # MỌI entity — như bỏ listenTo() bên TypeORM
```

Nghe tất cả hợp với việc chung: ghi nhật ký audit, dọn cache, đếm.

### Bảy method, khai cái nào dùng cái đó

| Method | Chạy khi |
|---|---|
| `after_load` | vừa đọc bản ghi lên: `get`, `find`, `find_one`, `query().all()` |
| `before_insert` / `after_insert` | `save()` một bản ghi **chưa có id** |
| `before_update` / `after_update` | `save()` một bản ghi **đã có id**, và `update(id, ...)` |
| `before_remove` / `after_remove` | `delete(id)` |

Khai method không nằm trong bảng này — ví dụ `before_soft_remove` của TypeORM —
thì **app dừng ngay lúc khởi động** kèm lý do. Cố ý: một handler không bao giờ
chạy mà vẫn im lặng là thứ mất cả buổi để phát hiện.

### `event` có gì

| Trường | Ý nghĩa |
|---|---|
| `entity_name` | tên class entity, ví dụ `"Camera"` |
| `entity` | bản ghi sau thao tác; ở `*_remove` là bản vừa bị xoá |
| `database_entity` | bản đang nằm dưới database **trước** khi sửa/xoá |
| `updated_columns` | tên các trường khác nhau giữa `database_entity` và `entity` |
| `changes` | bộ giá trị truyền cho `update(id, ...)`; `None` với `save()` |
| `id` | khoá chính |
| `database` | để mở `Repository(X, event.database)` — cùng transaction |
| `operation_id` | **một lời gọi `save`/`update`/`delete` = một mã**; xoá cha kéo theo con thì mọi sự kiện mang cùng mã này |
| `cascaded_from` | tên entity **cha** đã kéo theo bản ghi này; `None` = bị tác động thẳng |
| `request_id` | request HTTP đang chạy, nếu có — gom rộng hơn `operation_id` một bậc |

---

## Khi nào handler chạy

| Bạn gọi | Handler chạy |
|---|---|
| `repo.save(obj)` với `id` rỗng | `before_insert` → ghi → `after_insert` |
| `repo.save(obj)` với `id` đã có | `before_update` → ghi → `after_update` |
| `repo.update(id, ...)` | `before_update` → ghi → `after_update`; id không tồn tại thì **không chạy gì** |
| `repo.delete(id)` | `before_remove` → xoá → `after_remove`; id không tồn tại thì **không chạy gì** |
| `repo.get/find/find_one`, `query().all()` | `after_load` cho **từng** bản ghi |
| `repo.update_where(...)`, `repo.delete_where(...)` | **không chạy gì** — xem [Lưu ý](#lưu-ý) |
| xoá cha, con bị `on_delete` đụng tới | `after_remove` (CASCADE) hoặc `after_update` (SET NULL / SET DEFAULT) cho **từng dòng con** |

---

## Xoá cha kéo theo con

Xoá một camera, `on_delete` của khoá ngoại xoá luôn log và gỡ `camera_id` của
ghi chú về `NULL` — cả ba việc đó đều có sự kiện, và **mang cùng một
`operation_id`**:

```python
@injectable
@entity_subscriber(Camera, CameraLog, CameraNote)
class AuditSubscriber:
    async def after_remove(self, event: EntityEvent) -> None:
        log.info("da_xoa", bang=event.entity_name, id=event.id,
                 keo_theo_tu=event.cascaded_from, lenh=event.operation_id)

    async def after_update(self, event: EntityEvent) -> None:
        if event.cascaded_from:                 # camera_id vừa bị gỡ về NULL
            log.info("da_go_lien_ket", bang=event.entity_name, id=event.id,
                     cot=sorted(event.updated_columns), lenh=event.operation_id)
```

Một lần `await cameras.delete(cam_id)` cho ra:

```
after_remove  Camera      id=cam1   cascaded_from=None      operation_id=9f2c…
after_remove  CameraLog   id=log7   cascaded_from="Camera"  operation_id=9f2c…
after_remove  CameraLog   id=log8   cascaded_from="Camera"  operation_id=9f2c…
after_update  CameraNote  id=note3  cascaded_from="Camera"  operation_id=9f2c…
              updated_columns={"camera_id"}   database_entity.camera_id="cam1"
                                              entity.camera_id=None
```

Giống nhau trên `memory`, SQLite, PostgreSQL và MongoDB — dù với SQL thì chính
database làm cascade, còn `memory` và Mongo thì khung làm.

- **Dòng con chỉ có `after_*`, không có `before_*`.** Khung đọc chúng lên trước
  khi xoá cha (SQL không trả lại thứ nó vừa xoá), nhưng chỉ báo khi chắc chắn
  việc đã xảy ra.
- **Chỉ đọc thêm khi có người nghe.** Không có subscriber cho `CameraLog` thì
  xoá camera không sinh thêm câu lệnh nào.
- **`RESTRICT` không có sự kiện** — lúc đó lệnh xoá bị chặn bằng lỗi 409, không
  có gì xảy ra để mà báo.
- **`operation_id` gom một lệnh, `request_id` gom cả request.** Một request xoá
  ba camera cho ba `operation_id` khác nhau nhưng cùng một `request_id`.

---

## Lưu ý

- **Handler ném lỗi thì lời ghi hỏng theo.** Nó chạy trong cùng transaction, nên
  lỗi làm rollback tất cả — giống TypeORM. Việc "hỏng cũng không sao" (gửi mail,
  bắn thông báo) thì đừng làm thẳng trong handler; đẩy sang
  [`@on_event`](background.md#6-báo-cho-nhiều-nơi) hoặc hàng đợi.
- **`update_where` và `delete_where` KHÔNG phát sự kiện.** Một câu lệnh có thể
  khớp hàng trăm nghìn dòng, và đọc hết chúng về chỉ để gọi handler là thứ không
  ai muốn xảy ra ngầm. Cần sự kiện cho từng dòng thì `find()` rồi `update()` theo
  từng id.
- **Đừng gọi lại `save()` của chính entity đó trong handler** — `save` lại phát
  `before_update`, và bạn có một vòng lặp vô tận. Sửa giá trị thì sửa thẳng
  `event.entity` trong `before_insert`/`before_update`.
- **Chỉ trả giá khi có người nghe.** Không có subscriber nào cho entity đó thì
  `save`/`update`/`delete` chạy y như trước. Có người nghe `*_update`/`*_remove`
  thì khung đọc thêm **một** lượt để lấy `database_entity`.
- **Subscriber là singleton.** Không dùng được `Scope.REQUEST` — docs NestJS
  cũng nói đúng câu đó. Cần dữ liệu của request thì lấy từ `event`.

---

## Kiểm xem nó chạy chưa

Lúc khởi động phải thấy một dòng liệt kê đủ subscriber và method của chúng:

```
db.entity_subscribers  subscribers=['CameraSubscriber(Camera): after_insert, after_update']
```

Không thấy dòng này nghĩa là class thiếu `@injectable`, hoặc file không nằm dưới
`src/api/`.

---

## Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân |
|---|---|
| Không có log `db.entity_subscribers` lúc khởi động | class thiếu `@injectable`, hoặc file không nằm dưới `src/api/` |
| `RuntimeError: ... khung chưa có xoá mềm` lúc khởi động | khai `before_soft_remove`/`after_recover`… — khung không có những mốc đó |
| `RuntimeError: ... phải là async def` | method viết bằng `def` thường |
| `RuntimeError: ... chữ ký phải là (self, event: EntityEvent)` | thiếu hoặc thừa tham số |
| `RuntimeError: ... không có method nào trong` | class mang `@entity_subscriber` mà không khai method nào đúng tên |
| Sửa hàng loạt mà handler im | `update_where`/`delete_where` không phát sự kiện — đúng thiết kế |
| Xoá cha mà không thấy sự kiện của con | entity con chưa có subscriber nào nghe `after_remove`/`after_update`, hoặc khoá ngoại khai `RESTRICT` |
| Không có `before_remove` cho dòng con bị cascade | đúng thiết kế — xem [Xoá cha kéo theo con](#xoá-cha-kéo-theo-con) |
| `updated_columns` rỗng sau `save()` trên backend `memory` | `memory` giữ chính object của bạn, nên "bản cũ" đã bị sửa theo — xem [Tra cứu](#tra-cứu) |
| Vòng lặp vô tận, app treo lúc ghi | handler gọi lại `save()` cho chính entity đó |
| Handler ghi database xong mà dữ liệu không thấy đâu | lời ghi chính sau đó ném lỗi và rollback cả hai — đúng thiết kế |

---

## Tra cứu

### Đối chiếu với TypeORM

| TypeORM | Ở đây |
|---|---|
| `@EventSubscriber()` + `providers: [...]` | `@injectable` + `@entity_subscriber(...)`, tự quét |
| `listenTo() { return Post }` | `@entity_subscriber(Post)`; bỏ trống là mọi entity |
| `afterLoad` | `after_load` |
| `beforeInsert` / `afterInsert` | `before_insert` / `after_insert` |
| `beforeUpdate` / `afterUpdate` | `before_update` / `after_update` |
| `beforeRemove` / `afterRemove` | `before_remove` / `after_remove` |
| `event.entity` / `event.databaseEntity` | `event.entity` / `event.database_entity` |
| `event.updatedColumns` | `event.updated_columns` (tên trường, dạng `set`) |
| `event.manager` / `event.queryRunner` | `event.database` — `Repository(X, event.database)` |
| "Event subscribers can not be request-scoped" | y hệt: subscriber là singleton |
| *(TypeORM không có)* | `event.operation_id` — gom mọi sự kiện của cùng một lệnh ghi |
| *(TypeORM không có)* | `event.cascaded_from` + sự kiện cho dòng con bị `on_delete` đụng tới |

### Những chỗ KHÔNG giống, và vì sao

| TypeORM có | Ở đây |
|---|---|
| `beforeSoftRemove` / `afterSoftRemove` / `*Recover` | **không có** — khung chưa có xoá mềm. Khai vào là app báo lỗi lúc khởi động |
| `beforeTransactionStart/Commit/Rollback` | **không có** — MongoDB ở khung này không có transaction, nên sự kiện sẽ có ở backend này mà không có ở backend kia |
| `beforeQuery` / `afterQuery` | **không có** — backend `memory` không có câu lệnh nào để nghe |
| `@BeforeInsert` viết ngay trong entity | **không có** — entity ở đây là dataclass thuần, nghiệp vụ để ở subscriber |
| `@BeforeUpdate` chỉ chạy khi model thật sự đổi | ở đây chạy **mỗi lần** `save()` trên bản ghi đã có; khung không theo dõi dirty. Muốn biết đổi gì thì đọc `updated_columns` |
| subscriber chạy cả với `repository.update()` hàng loạt | `update_where`/`delete_where` **không** phát sự kiện |

### `database_entity` trên backend `memory`

`memory` trả về chính object đang nằm trong bảng, nên đoạn quen thuộc

```python
cam = await repo.get(camera_id)
cam.status = "offline"
await repo.save(cam)
```

đã sửa luôn bản trong bảng trước khi `save()` chạy. Vì vậy ở `memory`,
`database_entity` bằng đúng `entity` và `updated_columns` rỗng. Trên SQLite,
PostgreSQL và MongoDB thì đúng như mong đợi — đo bằng SQLite:
`database_entity.status = "online"`, `entity.status = "offline"`,
`updated_columns = {"status", "updated_at"}`.

Cần `updated_columns` chính xác cả khi chạy test trên `memory` thì dùng
`repo.update(id, ...)`: ở đó giá trị mới đi riêng nên không đụng vào bản cũ.
