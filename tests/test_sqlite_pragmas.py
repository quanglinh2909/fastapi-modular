"""SQLite: PRAGMA mặc định, an toàn khi nhiều nơi cùng ghi, và lúc chết đột ngột.

Cần `TEST_SQLITE=1` và `make install-sqlite` — giống các test driver khác.

Vì sao đáng có hẳn một file: mặc định gốc của SQLite ghi 68 dòng/giây, và
không ai nhận ra cho tới lúc worker camera bắt đầu ghi sự kiện. Con số đó là
một dòng cấu hình chứ không phải giới hạn của SQLite.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sqlite3
import subprocess
import sys
import time
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


async def test_like_phan_biet_hoa_thuong_nhu_postgres(backend):
    """`LIKE` của SQLite mặc định BỎ QUA hoa thường, Postgres và memory thì không.

    Không bật pragma này thì `where(name__like="kho%")` ra kết quả ở sqlite mà
    không ra gì ở postgres — lệch âm thầm, chỉ lộ khi đã lên production.
    """
    db = await backend()
    async with db._engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (name TEXT)"))
        await conn.execute(text("INSERT INTO t VALUES ('Kho hàng')"))
        thuong = (await conn.execute(text("SELECT name FROM t WHERE name LIKE 'kho%'"))).all()
        hoa = (await conn.execute(text("SELECT name FROM t WHERE name LIKE 'Kho%'"))).all()
        bo_qua = (await conn.execute(
            text("SELECT name FROM t WHERE lower(name) LIKE lower('kHo%')"))).all()

    assert thuong == [], "LIKE phải phân biệt hoa thường"
    assert len(hoa) == 1
    assert len(bo_qua) == 1, "ilike vẫn phải bỏ qua hoa thường"


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


# ------------------------------------------------- chết đột ngột thì file có hỏng
_KICH_BAN_GHI = """
import asyncio, sys
sys.path.insert(0, {repo!r})
from dataclasses import dataclass, field
from datetime import datetime
from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings, LogSettings
from fastapi_modular.core.container import entity
from fastapi_modular.core.logging import configure_logging
configure_logging(LogSettings(level="ERROR"))
from fastapi_modular.infrastructure.database.factory import create_backend

@entity()
@dataclass(slots=True)
class Row:
    id: str
    value: str
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

async def main():
    db = create_backend(DatabaseSettings(driver="sqlite", dsn={dsn!r}))
    await db.startup()
    await db.create_schema(Row)
    i = 0
    while True:
        await db.save(Row, Row(id="r%07d" % i, value="x"))
        i += 1
        if i % 50 == 0:
            print(i, flush=True)

asyncio.run(main())
"""


def _ghi_roi_kill9(path: Path) -> int:
    """Chạy một tiến trình ghi thật, giết bằng SIGKILL. Trả về số dòng CHẮC CHẮN đã commit."""
    repo = str(Path(__file__).resolve().parent.parent)
    script = _KICH_BAN_GHI.format(repo=repo, dsn=f"sqlite+aiosqlite:///{path}")
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        chac_chan = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()          # type: ignore[union-attr]
            if not line:
                break
            if line.strip().isdigit():
                chac_chan = int(line.strip())
                if chac_chan >= 100:
                    break
        assert chac_chan >= 100, "tiến trình ghi không khởi động được"
        time.sleep(0.05)                            # giết vào GIỮA một lần ghi
        proc.kill()
    finally:
        proc.wait(timeout=10)
    return chac_chan


def _soi(path: Path) -> tuple[str, int]:
    """(kết quả integrity_check, số dòng còn lại).

    Bảng có thể KHÔNG còn: khi cắt cụt cả WAL thì cái `CREATE TABLE` — vốn
    cũng nằm trong WAL — biến mất theo. Đó là MẤT dữ liệu, không phải HỎNG
    file, và hai thứ đó phải phân biệt được thì phép kiểm mới có nghĩa.
    """
    con = sqlite3.connect(path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            con_lai = con.execute("SELECT count(*) FROM rows").fetchone()[0]
        except sqlite3.OperationalError:
            con_lai = 0
        return integrity, con_lai
    finally:
        con.close()


def test_kill9_giua_luc_ghi_khong_lam_hong_file(tmp_path: Path):
    """`kill -9` KHÔNG phải ca nguy hiểm, và đây là chỗ nói rõ vì sao.

    Giết tiến trình không đụng tới page cache của nhân: dữ liệu đã ghi vẫn
    được nhân đẩy xuống đĩa như thường. Nên mất cả tiến trình cũng không mất
    một giao dịch nào đã commit. Ca nguy hiểm là MẤT ĐIỆN — xem test dưới.
    """
    path = tmp_path / "k.db"
    chac_chan = _ghi_roi_kill9(path)
    integrity, con_lai = _soi(path)

    assert integrity == "ok", f"file hỏng sau kill -9: {integrity}"
    assert con_lai >= chac_chan, "mất dữ liệu đã commit dù nhân vẫn còn sống"


def test_mat_dien_khong_lam_hong_file(tmp_path: Path):
    """Mất điện = mất page cache + lần ghi đang dở bị ĐỨT.

    Mô phỏng bằng cách cắt cụt WAL ở nhiều chỗ khác nhau: phần đuôi coi như
    chưa kịp xuống đĩa. SQLite có checksum cho từng khung WAL nên khung dở
    dang bị bỏ qua, file KHÔNG hỏng. Mất mấy giao dịch cuối thì có — đó đúng
    là cái giá của `synchronous=NORMAL`, và nó được đổi lấy tốc độ gấp 20 lần.
    """
    path = tmp_path / "p.db"
    _ghi_roi_kill9(path)
    wal = Path(f"{path}-wal")
    assert wal.exists() and wal.stat().st_size > 0, "phải còn WAL thì mới mô phỏng được"

    goc = wal.read_bytes()
    for phan in (0.0, 0.13, 0.5, 0.87, 0.99):
        ban_sao = tmp_path / f"cut_{phan}.db"
        ban_sao.write_bytes(path.read_bytes())
        Path(f"{ban_sao}-wal").write_bytes(goc[: int(len(goc) * phan)])

        integrity, _ = _soi(ban_sao)
        assert integrity == "ok", f"cắt WAL còn {phan:.0%} thì file hỏng: {integrity}"


def test_ghi_dut_giua_chung_vao_duoi_wal_van_mo_duoc(tmp_path: Path):
    """Không chỉ CỤT mà còn RÁC: lần ghi cuối đứt nửa chừng, byte lem nhem."""
    path = tmp_path / "t.db"
    _ghi_roi_kill9(path)
    wal = Path(f"{path}-wal")
    goc = wal.read_bytes()
    assert len(goc) > 8192

    for lech in (512, 4096, len(goc) - 2048):
        ban_sao = tmp_path / f"rac_{lech}.db"
        ban_sao.write_bytes(path.read_bytes())
        hong = bytearray(goc)
        hong[lech : lech + 512] = bytes(512)          # 512 byte không bao giờ tới đĩa
        Path(f"{ban_sao}-wal").write_bytes(bytes(hong))

        integrity, _ = _soi(ban_sao)
        assert integrity == "ok", f"đè rác ở byte {lech} thì file hỏng: {integrity}"
