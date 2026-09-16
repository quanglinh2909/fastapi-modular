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
    database = await new_database(SubCamera, SubZone)
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
