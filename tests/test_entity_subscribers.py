"""Test `@entity_subscriber` — bản của EntitySubscriberInterface (TypeORM/NestJS).

Chạy trên backend `memory`, vì luật "khi nào handler chạy" nằm hết ở
`Repository`, không phụ thuộc backend.

Mọi class subscriber ở đây khai BÊN TRONG fixture/test, và `isolate_registry`
xoá chúng khỏi sổ provider sau mỗi test. Khai ở cấp module thì chúng nằm lại
mãi, và `Database.startup()` của test khác sẽ quét trúng — với class cố ý sai
thì test khác đỏ vì lỗi của file này.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.core.container import _REGISTRY, entity, injectable
from fastapi_modular.infrastructure.database import (
    Entity,
    EntityEvent,
    Repository,
    entity_subscriber,
    reference,
)
from fastapi_modular.infrastructure.database.repository import Database
from fastapi_modular.infrastructure.database.subscribers import subscribers

SEEN: list[tuple[str, EntityEvent]] = []


@entity()
@dataclass(slots=True)
class SubCamera(Entity):
    id: str
    name: str = ""
    status: str = "online"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class SubZone(Entity):
    id: str
    name: str = ""


@entity()
@dataclass(slots=True)
class SubCameraLog(Entity):
    id: str
    camera_id: str = field(default="", metadata=reference(SubCamera, on_delete="CASCADE"))


@entity()
@dataclass(slots=True)
class SubCameraNote(Entity):
    id: str
    camera_id: str | None = field(
        default=None, metadata=reference(SubCamera, on_delete="SET NULL")
    )


@pytest.fixture(autouse=True)
def isolate_registry():
    known = set(_REGISTRY)
    SEEN.clear()
    yield
    for name in set(_REGISTRY) - known:
        del _REGISTRY[name]
    subscribers.clear()
    SEEN.clear()


def declare_subscribers() -> None:
    """Hai subscriber dùng cho phần lớn test: một nghe SubCamera, một nghe tất cả."""

    @injectable
    @entity_subscriber(SubCamera)
    class SubCameraSubscriber:
        async def after_load(self, event: EntityEvent) -> None:
            SEEN.append(("after_load", event))

        async def before_insert(self, event: EntityEvent) -> None:
            SEEN.append(("before_insert", event))

        async def after_insert(self, event: EntityEvent) -> None:
            SEEN.append(("after_insert", event))

        async def before_update(self, event: EntityEvent) -> None:
            SEEN.append(("before_update", event))

        async def after_update(self, event: EntityEvent) -> None:
            SEEN.append(("after_update", event))

        async def before_remove(self, event: EntityEvent) -> None:
            SEEN.append(("before_remove", event))

        async def after_remove(self, event: EntityEvent) -> None:
            SEEN.append(("after_remove", event))

    @injectable
    @entity_subscriber()                   # không tham số = nghe MỌI entity
    class SubEverythingSubscriber:
        async def after_insert(self, event: EntityEvent) -> None:
            SEEN.append(("all:after_insert", event))

    @injectable
    @entity_subscriber(SubCameraLog, SubCameraNote)
    class SubChildSubscriber:
        async def after_remove(self, event: EntityEvent) -> None:
            SEEN.append(("child:after_remove", event))

        async def after_update(self, event: EntityEvent) -> None:
            SEEN.append(("child:after_update", event))


async def new_database(*entities: type) -> Database:
    database = Database(Settings(APP_DB=DatabaseSettings(driver="memory")))
    await database.startup(*entities)      # startup() là chỗ quét subscriber
    return database


def names() -> list[str]:
    return [name for name, _ in SEEN]


def event_of(name: str) -> EntityEvent:
    return next(event for hook, event in SEEN if hook == name)


@pytest.fixture
async def db():
    declare_subscribers()
    database = await new_database(SubCamera, SubZone, SubCameraLog, SubCameraNote)
    SEEN.clear()
    yield database


@pytest.fixture
def cameras(db: Database) -> Repository[SubCamera]:
    return Repository(SubCamera, db)


async def test_insert_goi_before_roi_after(cameras: Repository[SubCamera]):
    cam = await cameras.save(SubCamera(id="", name="Cổng"))

    assert names()[0] == "before_insert"
    assert sorted(names()[1:]) == ["after_insert", "all:after_insert"]
    inserted = event_of("after_insert")
    assert inserted.entity_name == "SubCamera"
    assert inserted.entity is cam
    assert inserted.id == cam.id
    assert inserted.database_entity is None, "bản ghi mới thì không có bản cũ"


async def test_subscriber_khong_khai_entity_thi_nghe_tat_ca(db: Database):
    await Repository(SubZone, db).save(SubZone(id="", name="Tầng 1"))
    assert names() == ["all:after_insert"], "subscriber của SubCamera không được gọi"
    assert event_of("all:after_insert").entity_name == "SubZone"


async def test_save_ban_ghi_da_co_thi_la_update_va_co_ban_cu(cameras: Repository[SubCamera]):
    cam = await cameras.save(SubCamera(id="", name="Cổng"))
    SEEN.clear()

    cam.status = "offline"
    await cameras.save(cam)

    assert names() == ["before_update", "after_update"]
    updated = event_of("after_update")
    assert updated.database_entity is not None
    assert updated.entity.status == "offline"


async def test_update_theo_id_co_changes_va_ban_cu(cameras: Repository[SubCamera]):
    cam = await cameras.save(SubCamera(id="", name="Cổng"))
    SEEN.clear()

    await cameras.update(cam.id, status="offline")

    assert names() == ["before_update", "after_update"]
    before = event_of("before_update")
    assert before.changes is not None and before.changes["status"] == "offline"
    assert before.database_entity.status == "online"
    assert "status" in event_of("after_update").updated_columns


async def test_update_id_khong_co_thi_khong_co_su_kien_nao(cameras: Repository[SubCamera]):
    assert await cameras.update("khong-co", status="offline") is None
    assert names() == []


async def test_remove_co_ban_ghi_trong_event(cameras: Repository[SubCamera]):
    cam = await cameras.save(SubCamera(id="", name="Cổng"))
    SEEN.clear()

    assert await cameras.delete(cam.id) is True

    assert names() == ["before_remove", "after_remove"]
    removed = event_of("after_remove")
    assert removed.id == cam.id
    assert removed.entity.name == "Cổng", "handler phải xem được bản ghi vừa xoá"


async def test_xoa_id_khong_co_thi_khong_co_su_kien_nao(cameras: Repository[SubCamera]):
    assert await cameras.delete("khong-co") is False
    assert names() == []


async def test_after_load_chay_cho_get_find_va_query(cameras: Repository[SubCamera]):
    await cameras.save(SubCamera(id="", name="Cổng"))
    await cameras.save(SubCamera(id="", name="Sân"))
    SEEN.clear()

    found = await cameras.find()
    assert names().count("after_load") == 2

    SEEN.clear()
    await cameras.get(found[0].id)
    assert names() == ["after_load"]

    SEEN.clear()
    await cameras.query().all()
    assert names() == ["after_load", "after_load"]


async def test_bulk_khong_phat_su_kien(cameras: Repository[SubCamera]):
    """Giống TypeORM: subscriber gắn với save/remove, không phải câu lệnh hàng loạt."""
    await cameras.save(SubCamera(id="", name="Cổng"))
    SEEN.clear()

    await cameras.update_where({"name": "Cổng"}, status="offline")
    await cameras.delete_where(name="Cổng")
    assert names() == []


async def test_moi_su_kien_cua_mot_lenh_mang_cung_operation_id(cameras: Repository[SubCamera]):
    cam = await cameras.save(SubCamera(id="", name="Cổng"))
    first = {event.operation_id for _, event in SEEN}
    assert len(first) == 1 and first != {""}, "một lời gọi = một mã"

    SEEN.clear()
    await cameras.update(cam.id, status="offline")
    second = {event.operation_id for _, event in SEEN}
    assert len(second) == 1
    assert second != first, "lời gọi khác phải mang mã khác"


async def test_xoa_cha_thi_con_bi_cascade_cung_co_su_kien(db: Database):
    cameras = Repository(SubCamera, db)
    logs = Repository(SubCameraLog, db)
    notes = Repository(SubCameraNote, db)

    cam = await cameras.save(SubCamera(id="", name="Cổng"))
    await logs.save(SubCameraLog(id="", camera_id=cam.id))
    await logs.save(SubCameraLog(id="", camera_id=cam.id))
    await notes.save(SubCameraNote(id="", camera_id=cam.id))
    SEEN.clear()

    await cameras.delete(cam.id)

    assert names() == [
        "before_remove", "after_remove",          # chính camera
        "child:after_remove", "child:after_remove",   # 2 log bị CASCADE
        "child:after_update",                     # note bị SET NULL
    ]

    # Tất cả cùng MỘT lệnh xoá -> cùng một operation_id.
    assert len({event.operation_id for _, event in SEEN}) == 1

    removed_children = [e for hook, e in SEEN if hook == "child:after_remove"]
    assert {e.entity_name for e in removed_children} == {"SubCameraLog"}
    assert all(e.cascaded_from == "SubCamera" for e in removed_children)

    note_event = next(e for hook, e in SEEN if hook == "child:after_update")
    assert note_event.cascaded_from == "SubCamera"
    assert note_event.updated_columns == {"camera_id"}
    assert note_event.database_entity.camera_id == cam.id
    assert note_event.entity.camera_id is None, "SET NULL -> bản mới mang None"

    # Và dữ liệu thật đúng như sự kiện vừa báo.
    assert await logs.find() == []
    assert [n.camera_id for n in await notes.find()] == [None]


async def test_xoa_thang_ban_ghi_con_thi_khong_co_cascaded_from(db: Database):
    cam = await Repository(SubCamera, db).save(SubCamera(id="", name="Cổng"))
    logs = Repository(SubCameraLog, db)
    log = await logs.save(SubCameraLog(id="", camera_id=cam.id))
    SEEN.clear()

    await logs.delete(log.id)

    event = next(e for hook, e in SEEN if hook == "child:after_remove")
    assert event.cascaded_from is None, "bị xoá thẳng, không phải bị kéo theo"


async def test_handler_hong_thi_loi_noi_len_va_lam_hong_loi_ghi():
    """Handler chạy trong cùng transaction, nên hỏng là hỏng cả lời ghi."""

    @injectable
    @entity_subscriber(SubZone)
    class SubBrokenSubscriber:
        async def before_insert(self, event: EntityEvent) -> None:
            raise RuntimeError("handler hỏng")

    database = await new_database(SubZone)
    with pytest.raises(RuntimeError, match="handler hỏng"):
        await Repository(SubZone, database).save(SubZone(id="", name="Tầng 2"))


async def test_khong_ai_nghe_thi_khong_doc_them_lan_nao():
    """Lời hứa của thiết kế: không có subscriber thì đường ghi không đổi gì."""
    database = await new_database(SubZone)
    repo = Repository(SubZone, database)
    zone = await repo.save(SubZone(id="", name="Tầng 1"))

    reads = 0
    original = database.backend.get

    async def counted(entity_type, id_):
        nonlocal reads
        reads += 1
        return await original(entity_type, id_)

    database.backend.get = counted          # type: ignore[method-assign]
    try:
        await repo.update(zone.id, name="Tầng 9")
        await repo.delete(zone.id)
    finally:
        database.backend.get = original     # type: ignore[method-assign]
    assert reads == 0, "không ai nghe mà vẫn đọc bản cũ là đang trả giá vô ích"


# ------------------------------------------------- chặn sai sót ngay lúc quét
def test_method_typeorm_co_ma_khung_chua_co_thi_bao_ngay():
    @injectable
    @entity_subscriber(SubZone)
    class SubSoftRemoveSubscriber:
        async def before_soft_remove(self, event: EntityEvent) -> None: ...

    with pytest.raises(RuntimeError, match="xoá mềm"):
        subscribers.discover()


def test_method_phai_la_async_def():
    @injectable
    @entity_subscriber(SubZone)
    class SubSyncSubscriber:
        def after_insert(self, event: EntityEvent) -> None: ...

    with pytest.raises(RuntimeError, match="async def"):
        subscribers.discover()


def test_chu_ky_sai_bi_tu_choi():
    @injectable
    @entity_subscriber(SubZone)
    class SubWrongSignatureSubscriber:
        async def after_insert(self) -> None: ...

    with pytest.raises(RuntimeError, match="self, event"):
        subscribers.discover()


def test_subscriber_rong_bi_tu_choi():
    @injectable
    @entity_subscriber(SubZone)
    class SubEmptySubscriber:
        async def not_a_hook(self, event: EntityEvent) -> None: ...

    with pytest.raises(RuntimeError, match="không có method nào"):
        subscribers.discover()


# ------------------------------------------- request của người gọi trong event
async def test_ngoai_request_thi_event_request_la_none(cameras: Repository[SubCamera]):
    """Ghi từ worker, cron hay script: không có request, và đó KHÔNG phải lỗi.

    Chốt hẳn thành test vì `event.request` mà ném lỗi ở đây thì handler ném theo,
    và lời ghi rollback — tức một subscriber làm chết mọi lệnh ghi ngoài HTTP.
    """
    await cameras.save(SubCamera(id="", name="Cổng"))

    assert event_of("after_insert").request is None
    assert event_of("after_insert").request_id is None


def test_trong_request_http_thi_doc_duoc_header(settings):
    """Ca thật: handler đọc header Authorization của CHÍNH request đang ghi.

    Đi qua `create_app` + TestClient chứ không giả lập contextvar, vì thứ cần
    chốt là cả chuỗi: middleware giữ request -> repository gắn vào event.
    """
    from fastapi.testclient import TestClient

    from fastapi_modular.factory import create_app
    from src.api.users.entities.user_model import User

    seen: list[dict] = []

    @injectable
    @entity_subscriber(User)
    class SubHttpSubscriber:
        async def after_insert(self, event: EntityEvent) -> None:
            request = event.request
            seen.append(
                {
                    "authorization": request.headers.get("authorization"),
                    "path": request.url.path,
                    "method": request.method,
                    "request_id": event.request_id,
                }
            )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/users",
            json={"email": "an@example.com", "full_name": "Nguyễn Văn An"},
            headers={"authorization": "Bearer jwt-cua-keycloak"},
        )

    assert response.status_code == 201, response.text
    assert len(seen) == 1
    assert seen[0]["authorization"] == "Bearer jwt-cua-keycloak"
    assert seen[0]["path"] == "/api/users"
    assert seen[0]["method"] == "POST"
    assert seen[0]["request_id"] == response.headers["x-request-id"], (
        "phải nối được dòng audit với access log của cùng request đó"
    )


# ------------------------------------------------------- bản cũ phải còn nguyên
async def test_doc_len_sua_tai_cho_roi_save_thi_van_con_ban_cu(db: Database):
    """Ca hay viết nhất trong service — và từng là chỗ `memory` nói dối.

    Backend `memory` trước đây trả thẳng object đang nằm trong bảng, nên dòng
    `doc.status = ...` sửa luôn "bản cũ": `database_entity` mang sẵn giá trị
    mới và `updated_columns` rỗng, trong khi sqlite cho đủ hai cột. Chốt hẳn
    thành test vì lệch kiểu này im lặng — `fam test` xanh, production sai.
    """
    repo = Repository(SubCamera, db)
    cam = await repo.save(SubCamera(id="", name="Cổng", status="online"))
    SEEN.clear()

    doc = await repo.get(cam.id)
    doc.status = "offline"
    await repo.save(doc)

    updated = event_of("after_update")
    assert updated.database_entity.status == "online", "bản cũ đã bị sửa theo"
    assert updated.entity.status == "offline"
    assert "status" in updated.updated_columns
