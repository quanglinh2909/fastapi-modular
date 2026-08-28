"""Test schema tiến hoá: thêm trường, xoá trường, bù giá trị mặc định.

Chạy trên SQL thật (SQLite/Postgres) vì đây là hành vi DDL. SKIP nếu chưa cài
driver hoặc chưa có server.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.container import entity
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.repository import Repository

pytestmark = pytest.mark.skipif(
    not (os.getenv("TEST_SQLITE") and importlib.util.find_spec("aiosqlite"))
    and not (os.getenv("TEST_POSTGRES_DSN") and importlib.util.find_spec("asyncpg")),
    reason="đặt TEST_SQLITE=1 hoặc TEST_POSTGRES_DSN",
)

TABLE = f"evo_{uuid.uuid4().hex[:8]}"


def _settings(drop: bool = False) -> DatabaseSettings:
    if os.getenv("TEST_POSTGRES_DSN"):
        return DatabaseSettings(
            driver="postgres", dsn=os.environ["TEST_POSTGRES_DSN"],
            schema_mode="sync", drop_columns=drop,
        )
    return DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///./data/{TABLE}.db",
        schema_mode="sync", drop_columns=drop,
    )


class _Db:
    def __init__(self, drop: bool = False) -> None:
        self._backend = create_backend(_settings(drop))

    @property
    def backend(self):
        return self._backend


async def _columns(db) -> list[str]:
    from sqlalchemy import inspect

    async with db.backend._engine.connect() as conn:
        return await conn.run_sync(
            lambda c: [x["name"] for x in inspect(c).get_columns(TABLE)]
        )


@entity(name=TABLE)
@dataclass(slots=True)
class Before:
    id: str
    name: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass(slots=True)
class After:
    id: str
    name: str
    created_at: datetime = field(default_factory=utcnow)
    status: str = "offline"          # trường mới, có default
    note: str | None = None          # trường mới, nhận None


After.__storage_name__ = TABLE       # cùng bảng với Before

# Bảng RIÊNG cho phép kiểm `schema_mode="off"`: nó khẳng định bảng KHÔNG được
# tạo, nên không dùng chung tên với các test ở trên. SQLite mỗi test một file
# nên không lộ, Postgres dùng chung một database thì lộ ngay.
TABLE_OFF = f"{TABLE}_off"


@dataclass(slots=True)
class Untouched:
    id: str
    name: str
    created_at: datetime = field(default_factory=utcnow)


Untouched.__storage_name__ = TABLE_OFF


@pytest.fixture(scope="module", autouse=True)
async def _don_bang_tam():
    """Postgres dùng chung một database cho cả bộ test — bảng tạm phải dọn.

    Không dọn thì lần chạy sau gặp schema cũ, và database của người dùng đầy
    dần những bảng `evo_*` không ai biết của ai.
    """
    yield
    if not os.getenv("TEST_POSTGRES_DSN"):
        return
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["TEST_POSTGRES_DSN"])
    async with engine.begin() as conn:
        for name in (TABLE, TABLE_OFF):
            await conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
    await engine.dispose()


@pytest.mark.asyncio
async def test_them_truong_va_bu_gia_tri_mac_dinh():
    db = _Db()
    await db.backend.startup()
    await db.backend.create_schema(Before)
    assert set(await _columns(db)) == {"id", "name", "created_at"}

    await Repository(Before, db).save(Before(id="", name="Cam A"))
    await db.backend.shutdown()

    # entity thêm 2 trường -> khởi động lại phải tự ALTER TABLE ADD COLUMN
    db = _Db()
    await db.backend.startup()
    await db.backend.create_schema(After)
    assert set(await _columns(db)) == {"id", "name", "created_at", "status", "note"}

    rows = await Repository(After, db).find()
    assert len(rows) == 1, "dữ liệu cũ phải còn nguyên sau khi thêm cột"
    assert rows[0].name == "Cam A"
    assert rows[0].status == "offline", "trường mới phải rơi về default của entity"
    assert rows[0].note is None, "kiểu Optional thì giữ None, không bù default"
    await db.backend.shutdown()


@pytest.mark.asyncio
async def test_xoa_truong_chi_khi_bat_drop_columns():
    db = _Db(drop=False)
    await db.backend.startup()
    await db.backend.create_schema(Before)          # entity không còn status/note
    assert "status" in await _columns(db), "drop_columns=False thì giữ cột thừa"
    await db.backend.shutdown()

    db = _Db(drop=True)
    await db.backend.startup()
    await db.backend.create_schema(Before)
    remaining = await _columns(db)
    assert "status" not in remaining and "note" not in remaining
    assert set(remaining) == {"id", "name", "created_at"}

    rows = await Repository(Before, db).find()
    assert len(rows) == 1 and rows[0].name == "Cam A", "xoá cột không được mất hàng"
    await db.backend.shutdown()


@pytest.mark.asyncio
async def test_schema_mode_off_khong_dung_gi():
    """"off" nghĩa là không đụng DDL — kiểm trong DATABASE, không kiểm bộ nhớ.

    Bản đồ cột `backend._tables` vẫn phải được dựng dù ở mức "off": nó là thứ
    dịch entity ra câu SQL, nên không có nó thì cả `find` lẫn `save` đều hỏng.
    Thứ "off" hứa là không chạy CREATE/ALTER, và đó mới là thứ đáng kiểm.
    """
    from sqlalchemy import text

    rieng = _settings().model_copy(update={"schema_mode": "off"})
    if rieng.driver == "sqlite":
        rieng = rieng.model_copy(
            update={"dsn": f"sqlite+aiosqlite:///./data/{TABLE}_off.db"}
        )
    settings = rieng
    backend = create_backend(settings)
    await backend.startup()
    await backend.create_schema(Untouched)

    async with backend._engine.connect() as conn:
        if backend.name == "postgres":
            sql = "SELECT to_regclass(:t) IS NOT NULL"
        else:
            sql = "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:t"
        co_bang = bool((await conn.execute(text(sql), {"t": TABLE_OFF})).scalar())

    assert co_bang is False, "schema_mode=off mà vẫn tạo bảng"
    await backend.shutdown()
