"""SQLite: PRAGMA mặc định, và an toàn khi nhiều nơi cùng ghi.

Cần `TEST_SQLITE=1` và `make install-sqlite` — giống các test driver khác.

Vì sao đáng có hẳn một file: mặc định gốc của SQLite ghi 68 dòng/giây, và
không ai nhận ra cho tới lúc worker camera bắt đầu ghi sự kiện. Con số đó là
một dòng cấu hình chứ không phải giới hạn của SQLite.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.container import entity
from fastapi_modular.infrastructure.database.factory import create_backend

pytestmark = pytest.mark.skipif(
    not (os.getenv("TEST_SQLITE") and importlib.util.find_spec("aiosqlite")),
    reason="đặt TEST_SQLITE=1 và chạy make install-sqlite",
)


@entity()
@dataclass(slots=True)
class Detection:
    id: str
    camera: str
    label: str
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@pytest.fixture
async def backend(tmp_path: Path):
    async def _mo(**kwargs):
        settings = DatabaseSettings(
            driver="sqlite",
            dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db",
            **kwargs,
        )
        db = create_backend(settings)
        await db.startup()
        await db.create_schema(Detection)
        return db

    mo = []

    async def factory(**kwargs):
        db = await _mo(**kwargs)
        mo.append(db)
        return db

    yield factory
    for db in mo:
        await db.shutdown()


async def _pragmas(db) -> dict[str, object]:
    async with db._engine.connect() as conn:
        return {
            key: (await conn.execute(text(f"PRAGMA {key}"))).scalar()
            for key in ("journal_mode", "synchronous", "busy_timeout")
        }


async def test_mac_dinh_la_wal_normal_busy5s(backend):
    """Ba giá trị này là khác biệt giữa 68 ghi/s và 1.300 ghi/s."""
    db = await backend()
    assert await _pragmas(db) == {
        "journal_mode": "wal",
        "synchronous": 1,          # NORMAL
        "busy_timeout": 5000,
    }


async def test_doi_duoc_ve_mac_dinh_goc(backend):
    """Ổ mạng không chạy được WAL, và có nơi cần bền vững tuyệt đối."""
    db = await backend(sqlite_journal_mode="DELETE", sqlite_synchronous="FULL")
    got = await _pragmas(db)
    assert got["journal_mode"] == "delete"
    assert got["synchronous"] == 2      # FULL


async def test_busy_timeout_theo_giay_trong_cau_hinh(backend):
    db = await backend(sqlite_busy_timeout_seconds=1.5)
    assert (await _pragmas(db))["busy_timeout"] == 1500


async def test_moi_connection_trong_pool_deu_duoc_dat(backend):
    """`synchronous` và `busy_timeout` là thiết lập của TỪNG connection.

    Đặt bằng một câu lệnh lúc khởi động thì chỉ connection đầu tiên có; những
    connection pool mở thêm sau đó lặng lẽ quay về mặc định gốc — và app chậm
    dần đúng lúc tải lên cao.
    """
    db = await backend()

    async def doc_pragma() -> tuple:
        async with db._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await asyncio.sleep(0.05)          # giữ connection để buộc mở thêm
            sy = (await conn.execute(text("PRAGMA synchronous"))).scalar()
            bt = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
            return sy, bt

    ket_qua = await asyncio.gather(*(doc_pragma() for _ in range(5)))
    assert ket_qua == [(1, 5000)] * 5


async def test_nhieu_nguoi_ghi_cung_luc_khong_loi(backend):
    """SQLite chỉ cho MỘT người ghi — nhưng người thứ hai CHỜ, không lỗi ngay.

    `busy_timeout` là thứ biến "database is locked" thành "chậm hơn một chút".
    """
    db = await backend()
    loi: list[str] = []

    async def ghi(w: int, n: int = 25) -> None:
        for i in range(n):
            try:
                await db.save(Detection, Detection(id=f"w{w}-{i}", camera=f"c{w}", label="p"))
            except Exception as exc:
                loi.append(f"{type(exc).__name__}: {exc}")

    await asyncio.gather(*(ghi(w) for w in range(8)))

    assert loi == []
    assert await db.count(Detection, filters={}) == 8 * 25
