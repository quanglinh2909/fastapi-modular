"""Chạy cùng một kịch bản CRUD trên mọi driver có sẵn.

Driver nào chưa cài thư viện, hoặc chưa có server, sẽ được SKIP chứ không FAIL —
nhờ vậy bộ test chạy được trên máy chỉ cài một driver.

Bật driver thật bằng biến môi trường:
    TEST_SQLITE=1
    TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app
    TEST_MONGO_DSN=mongodb://127.0.0.1:27017
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.compat import UTC
from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.core.container import entity
from fastapi_modular.infrastructure.database import Entity
from fastapi_modular.infrastructure.database.repository import Database, Repository

# Bảng RIÊNG cho lần chạy này, không dùng `users` của app mẫu.
#
# Vì sao quan trọng: fixture bên dưới gọi `delete_where()` KHÔNG điều kiện, tức
# xoá sạch bảng. Nếu `TEST_POSTGRES_DSN` trỏ vào một database đang có bảng
# `users` của dự án khác — chuyện rất dễ xảy ra, `app` là tên mặc định — thì bộ
# test này xoá dữ liệu của người ta. Tên bảng có hậu tố ngẫu nhiên thì không
# đụng vào đâu cả.
TABLE = f"drvusers_{uuid.uuid4().hex[:8]}"


@entity(name=TABLE, unique=["email"])
@dataclass(slots=True)
class User(Entity):
    id: str
    email: str
    full_name: str
    is_active: bool = True
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _cases() -> list:
    cases = [pytest.param({"driver": "memory"}, id="memory")]

    cases.append(
        pytest.param(
            {"driver": "sqlite", "dsn": f"sqlite+aiosqlite:///./data/test_{uuid.uuid4().hex}.db"},
            id="sqlite",
            marks=pytest.mark.skipif(
                not (os.getenv("TEST_SQLITE") and _installed("aiosqlite")),
                reason="đặt TEST_SQLITE=1 và chạy make install-sqlite",
            ),
        )
    )
    cases.append(
        pytest.param(
            {"driver": "postgres", "dsn": os.getenv("TEST_POSTGRES_DSN", "")},
            id="postgres",
            marks=pytest.mark.skipif(
                not (os.getenv("TEST_POSTGRES_DSN") and _installed("asyncpg")),
                reason="đặt TEST_POSTGRES_DSN và chạy make install-postgres",
            ),
        )
    )
    cases.append(
        pytest.param(
            {
                "driver": "mongodb",
                "dsn": os.getenv("TEST_MONGO_DSN", ""),
                "name": f"test_{uuid.uuid4().hex[:8]}",
            },
            id="mongodb",
            marks=pytest.mark.skipif(
                not (os.getenv("TEST_MONGO_DSN") and _installed("motor")),
                reason="đặt TEST_MONGO_DSN và chạy make install-mongo",
            ),
        )
    )
    return cases


@pytest.fixture(params=_cases())
async def repo(request) -> Repository:
    database = Database(Settings(APP_DB=DatabaseSettings(**request.param)))
    await database.startup(User)
    repository = Repository(User, database)
    await repository.delete_where()          # dọn dữ liệu cũ nếu có
    yield repository
    await repository.delete_where()
    if request.param["driver"] == "postgres":
        # Postgres dùng chung một database cho cả bộ test, nên bảng tạm phải
        # được dọn — để lại là lần sau chạy đụng schema cũ.
        from sqlalchemy import text as sql_text

        async with database.backend._engine.begin() as conn:
            await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{TABLE}"'))
    await database.shutdown()


@pytest.mark.asyncio
async def test_crud_giong_nhau_tren_moi_driver(repo):
    a = await repo.save(User(id="", email="a@x.co", full_name="A"))
    b = await repo.save(User(id="", email="b@x.co", full_name="B"))
    assert a.id and b.id and a.id != b.id

    assert await repo.count() == 2
    assert (await repo.get(a.id)).full_name == "A"
    assert (await repo.find_one(email="b@x.co")).id == b.id
    assert await repo.exists(email="a@x.co")
    assert not await repo.exists(email="zzz@x.co")

    # phân trang + sắp xếp
    page = await repo.find(limit=1, offset=0, order_by="created_at")
    assert len(page) == 1

    # lọc bằng nhau, và None = không lọc
    assert await repo.count(full_name="A") == 1
    assert await repo.count(full_name=None) == 2

    # predicate Python
    assert len(await repo.find(match=lambda u: u.email.startswith("b"))) == 1

    # cập nhật là upsert trên cùng id
    a.full_name = "A2"
    await repo.save(a)
    assert (await repo.get(a.id)).full_name == "A2"
    assert await repo.count() == 2

    assert await repo.delete(a.id) is True
    assert await repo.delete(a.id) is False
    assert await repo.delete_where(email="b@x.co") == 1
    assert await repo.count() == 0


@pytest.mark.asyncio
async def test_enum_va_datetime_giu_nguyen_kieu(repo):
    from datetime import datetime

    saved = await repo.save(User(id="", email="t@x.co", full_name="T"))
    loaded = await repo.get(saved.id)
    assert isinstance(loaded.created_at, datetime)
    assert loaded.is_active is True


@pytest.mark.asyncio
async def test_datetime_luon_co_mui_gio_utc(repo):
    """SQLite và Mongo trả datetime không kèm tzinfo; phải chuẩn hoá lúc đọc,
    nếu không một response sẽ lẫn lộn 'có Z' với 'không Z'."""

    saved = await repo.save(User(id="", email="tz@x.co", full_name="TZ"))
    loaded = await repo.get(saved.id)

    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.utcoffset() == UTC.utcoffset(None)
    assert loaded.updated_at.tzinfo is not None


@pytest.mark.asyncio
async def test_ban_ghi_moi_co_created_bang_updated(repo):
    saved = await repo.save(User(id="", email="new@x.co", full_name="N"))
    loaded = await repo.get(saved.id)
    assert loaded.created_at == loaded.updated_at, "chưa sửa thì hai mốc phải bằng nhau"

    # Mongo lưu datetime tới MILI giây, nên hai lần save sát nhau rơi vào cùng
    # một mốc và `>` không đúng — đo được 147/200 vòng bị trùng khi ghi liên
    # tiếp. Chờ 2ms để phép so nói về cái nó định nói: đã sửa thì mốc phải mới.
    await asyncio.sleep(0.002)
    loaded.full_name = "N2"
    await repo.save(loaded)
    again = await repo.get(saved.id)
    assert again.updated_at > again.created_at
