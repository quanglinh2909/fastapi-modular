"""Test `id` kiểu số tự tăng (`id: int`) bên cạnh `id: str` (UUID khung sinh).

Chạy trên **cả bốn backend** — memory, SQLite, MongoDB, Postgres — vì mỗi nơi
cấp số một kiểu: SQL để database phát (`SERIAL` / `INTEGER PRIMARY KEY`), Mongo
dùng bộ đếm `_fam_counters`, memory dùng bộ đếm trong bộ nhớ. Ba cách đó phải
cho CÙNG hành vi, nếu không `fam test` xanh mà production đổ.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.container import entity
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.infrastructure.database import Entity
from fastapi_modular.infrastructure.database.base import mapping_for, reference
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.repository import Repository

HAS_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None
MONGO_DSN = os.getenv("TEST_MONGO_DSN", "")
HAS_MONGO = bool(MONGO_DSN) and importlib.util.find_spec("motor") is not None
POSTGRES_DSN = os.getenv("TEST_POSTGRES_DSN", "")
HAS_POSTGRES = bool(POSTGRES_DSN) and importlib.util.find_spec("asyncpg") is not None

SUFFIX = uuid.uuid4().hex[:6]


@entity(name=f"numzones_{SUFFIX}")
@dataclass(slots=True)
class NumZone(Entity):
    id: int = 0
    name: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity(name=f"numcameras_{SUFFIX}")
@dataclass(slots=True)
class NumCamera(Entity):
    id: int = 0
    name: str = ""
    # Khoá ngoại trỏ tới bảng có id SỐ thì cột con cũng phải là số.
    zone_id: int | None = field(default=None, metadata=reference(NumZone, on_delete="CASCADE"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity(name=f"texttags_{SUFFIX}")
@dataclass(slots=True)
class TextTag(Entity):
    id: str = ""
    name: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


class _Db:
    def __init__(self, backend) -> None:
        self.backend = backend
        self.driver = backend.name


@pytest.fixture(params=[
    "memory",
    pytest.param("sqlite", marks=pytest.mark.skipif(
        not HAS_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")),
    pytest.param("mongodb", marks=pytest.mark.skipif(
        not HAS_MONGO, reason="đặt TEST_MONGO_DSN và cài motor")),
    pytest.param("postgres", marks=pytest.mark.skipif(
        not HAS_POSTGRES, reason="đặt TEST_POSTGRES_DSN và cài asyncpg")),
])
async def repos(request, tmp_path):
    table = uuid.uuid4().hex[:8]
    if request.param == "memory":
        settings = DatabaseSettings(driver="memory")
    elif request.param == "sqlite":
        settings = DatabaseSettings(driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{table}.db")
    elif request.param == "postgres":
        settings = DatabaseSettings(driver="postgres", dsn=POSTGRES_DSN)
    else:
        settings = DatabaseSettings(driver="mongodb", dsn=MONGO_DSN, name=f"fam_id_{table}")

    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(NumZone, NumCamera, TextTag)

    db = _Db(backend)
    yield {
        "driver": request.param,
        "zones": Repository(NumZone, db),
        "cameras": Repository(NumCamera, db),
        "tags": Repository(TextTag, db),
    }

    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    if request.param == "postgres":
        from sqlalchemy import text as sql_text

        async with backend._engine.begin() as conn:
            for name in (f"numcameras_{SUFFIX}", f"numzones_{SUFFIX}", f"texttags_{SUFFIX}"):
                await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
    await backend.shutdown()


# ------------------------------------------------------------------ khai báo
def test_auto_id_read_from_the_declared_type():
    assert mapping_for(NumZone).auto_id is True
    assert mapping_for(TextTag).auto_id is False, "`id: str` vẫn là UUID do khung sinh"


# ------------------------------------------------------------------ cấp số
async def test_new_records_get_increasing_numbers(repos):
    first = await repos["zones"].save(NumZone(name="A"))
    second = await repos["zones"].save(NumZone(name="B"))
    assert isinstance(first.id, int) and isinstance(second.id, int)
    assert first.id >= 1 and second.id > first.id


async def test_id_is_not_reused_after_delete(repos):
    """Xoá bản ghi cuối rồi ghi tiếp: số cũ KHÔNG được quay lại.

    SQLite mặc định cấp `max(rowid) + 1` nên số cũ quay lại thật — khung bật
    `sqlite_autoincrement` đúng vì chuyện này. Không có nó, cùng một đoạn code
    cho hai kết quả khác nhau giữa dev (SQLite) và production (Postgres).
    """
    first = await repos["zones"].save(NumZone(name="A"))
    second = await repos["zones"].save(NumZone(name="B"))
    await repos["zones"].delete(second.id)

    third = await repos["zones"].save(NumZone(name="C"))
    assert third.id not in (first.id, second.id), f"số {second.id} bị cấp lại"


async def test_concurrent_saves_never_share_an_id(repos):
    """20 lượt ghi song song phải ra 20 số khác nhau — kể cả trên Mongo."""
    saved = await asyncio.gather(
        *[repos["zones"].save(NumZone(name=f"z{i}")) for i in range(20)]
    )
    ids = [row.id for row in saved]
    assert len(set(ids)) == 20, sorted(ids)
    assert await repos["zones"].count() == 20


async def test_explicit_number_is_kept(repos):
    saved = await repos["zones"].save(NumZone(id=77, name="tay"))
    assert saved.id == 77
    assert (await repos["zones"].get(77)).name == "tay"


# ---------------------------------------------------------------- dùng số đó
async def test_get_update_delete_by_number(repos):
    saved = await repos["zones"].save(NumZone(name="A"))

    assert (await repos["zones"].get(saved.id)).name == "A"

    changed = await repos["zones"].update(saved.id, name="B")
    assert changed is not None and changed.name == "B" and changed.id == saved.id

    assert await repos["zones"].delete(saved.id) is True
    assert await repos["zones"].get(saved.id) is None


async def test_query_and_find_filter_by_number(repos):
    a = await repos["zones"].save(NumZone(name="A"))
    await repos["zones"].save(NumZone(name="B"))

    assert (await repos["zones"].find_one(id=a.id)).name == "A"
    found = await repos["zones"].query().where(NumZone.id == a.id).all()
    assert [row.id for row in found] == [a.id]


async def test_id_still_cannot_be_changed_by_update(repos):
    saved = await repos["zones"].save(NumZone(name="A"))
    with pytest.raises(BadRequestError, match="id"):
        await repos["zones"].update(saved.id, id=999)


# -------------------------------------------------------------- khoá ngoại
async def test_foreign_key_to_a_number_id(repos):
    zone = await repos["zones"].save(NumZone(name="Tầng 1"))
    camera = await repos["cameras"].save(NumCamera(name="Cổng", zone_id=zone.id))
    assert camera.zone_id == zone.id

    await repos["zones"].delete(zone.id)
    assert await repos["cameras"].get(camera.id) is None, "CASCADE phải kéo camera đi theo"


async def test_foreign_key_to_a_missing_number_is_refused(repos):
    """SQL để database ném (IntegrityError -> 409), memory/Mongo ném ConflictError."""
    with pytest.raises(Exception) as bad:
        await repos["cameras"].save(NumCamera(name="mồ côi", zone_id=424242))
    assert "NumZone" in str(bad.value) or "FOREIGN KEY" in str(bad.value).upper()
    assert await repos["cameras"].count() == 0


async def test_update_where_with_a_number_says_use_update(repos):
    """Truyền id số cho `update_where` phải được chỉ đường, không phải lỗi khó hiểu."""
    saved = await repos["zones"].save(NumZone(name="A"))
    with pytest.raises(BadRequestError, match=r"update\(id"):
        await repos["zones"].update_where(saved.id, name="B")


@pytest.mark.skipif(not HAS_MONGO, reason="đặt TEST_MONGO_DSN và cài motor")
async def test_mongo_refuses_to_overwrite_when_the_counter_is_behind():
    """Bộ đếm lệch (ai đó gán tay id) thì phải nhận lỗi, không được đè dữ liệu."""
    from pymongo.errors import DuplicateKeyError

    from fastapi_modular.infrastructure.database.mongo import _COUNTERS

    settings = DatabaseSettings(
        driver="mongodb", dsn=MONGO_DSN, name=f"fam_id_{uuid.uuid4().hex[:8]}"
    )
    backend = create_backend(settings)
    await backend.startup()
    zones = Repository(NumZone, _Db(backend))
    try:
        first = await zones.save(NumZone(name="A"))          # id 1, bộ đếm = 1
        # Kéo bộ đếm lùi lại: lần ghi sau sẽ xin đúng số 1 đang có người dùng.
        await backend._client[backend._database_name][_COUNTERS].update_one(
            {"_id": mapping_for(NumZone).storage}, {"$set": {"seq": 0}}
        )
        with pytest.raises(DuplicateKeyError):
            await zones.save(NumZone(name="B"))

        assert (await zones.get(first.id)).name == "A", "dữ liệu cũ phải còn nguyên"
        assert await zones.count() == 1
    finally:
        await backend._client.drop_database(backend._database_name)
        await backend.shutdown()


# ---------------------------------------------------------------- id chuỗi
async def test_string_id_is_still_a_uuid(repos):
    saved = await repos["tags"].save(TextTag(name="A"))
    assert re.fullmatch(r"[0-9a-f]{32}", saved.id), saved.id
    assert (await repos["tags"].get(saved.id)).name == "A"
