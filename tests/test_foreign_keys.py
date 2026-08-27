"""Test khoá ngoại — xoá cha thì con đi theo, hay ở lại, hay chặn không cho xoá.

Mọi phép kiểm chạy trên **cả hai backend**: `memory` (mặc định của `fam test`)
và `sqlite` (khoá ngoại THẬT do database áp). Hai cột phải cho cùng kết quả —
nếu không thì `fam test` xanh mà production hỏng.
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
from fastapi_modular.core.exceptions import BadRequestError, ConflictError
from fastapi_modular.infrastructure.database.base import (
    mapping_for,
    reference,
)
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.repository import Repository

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None
MONGO_DSN = os.getenv("TEST_MONGO_DSN", "")
CO_MONGO = bool(MONGO_DSN) and importlib.util.find_spec("motor") is not None


@entity()
@dataclass(slots=True)
class FkZone:
    id: str
    name: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class FkCamera:
    id: str
    name: str = ""
    # Xoá khu vực -> camera Ở LẠI, chỉ mất chỗ gắn.
    zone_id: str | None = field(default=None, metadata=reference(FkZone, on_delete="SET NULL"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class FkEvent:
    id: str
    # Xoá camera -> sự kiện của nó không còn nghĩa gì, xoá theo.
    camera_id: str = field(default="", metadata=reference(FkCamera, on_delete="CASCADE"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class FkInvoice:
    id: str
    # Hoá đơn KHÔNG được mất theo khách hàng — chặn xoá.
    zone_id: str = field(default="", metadata=reference(FkZone, on_delete="RESTRICT"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class FkTag:
    id: str
    # Xoá camera -> về giá trị mặc định thay vì NULL.
    camera_id: str = field(
        default="chua-gan", metadata=reference(FkCamera, on_delete="SET DEFAULT")
    )
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class FkFrame:
    """Tầng thứ BA: camera -> sự kiện -> khung hình.

    Có để bắt lỗi cascade chỉ đi được một tầng: xoá camera thì khung hình của
    sự kiện cũng phải đi theo, chứ không ở lại thành mồ côi.
    """

    id: str
    event_id: str = field(default="", metadata=reference(FkEvent, on_delete="CASCADE"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


class _Db:
    def __init__(self, backend) -> None:
        self.backend = backend
        self.driver = backend.name


@pytest.fixture(params=[
    "memory",
    pytest.param("sqlite", marks=pytest.mark.skipif(
        not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")),
    pytest.param("mongodb", marks=pytest.mark.skipif(
        not CO_MONGO, reason="đặt TEST_MONGO_DSN và cài motor")),
])
async def kho(request, tmp_path):
    if request.param == "memory":
        settings = DatabaseSettings(driver="memory")
    elif request.param == "sqlite":
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db"
        )
    else:
        # MongoDB không có khoá ngoại — chính khung áp `on_delete`. Vì vậy nó
        # phải nằm trong CÙNG bộ test này, không thì chỗ khác biệt duy nhất lại
        # là chỗ không ai soi.
        settings = DatabaseSettings(
            driver="mongodb", dsn=MONGO_DSN, name=f"fam_fk_{uuid.uuid4().hex[:8]}"
        )
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(FkZone, FkCamera, FkEvent, FkInvoice, FkTag, FkFrame)

    db = _Db(backend)
    yield {
        "zones": Repository(FkZone, db),
        "cameras": Repository(FkCamera, db),
        "events": Repository(FkEvent, db),
        "invoices": Repository(FkInvoice, db),
        "tags": Repository(FkTag, db),
        "frames": Repository(FkFrame, db),
        "backend": backend,
    }
    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    await backend.shutdown()


class _BatLog:
    """Bắt log của một module bằng cách thay thẳng đối tượng `log`.

    Không dùng `caplog`: structlog ở đây in thẳng ra stdout, không đi qua
    `logging`. Cũng không dùng bộ bắt log của structlog: logger được nhớ ngay
    lần dùng đầu, nên khi chạy CẢ BỘ test thì nó đã bị test khác làm cho nhớ và
    phép bắt đó không chặn được nữa — đã thấy đúng ca này, xanh khi chạy một
    file và đỏ khi chạy cả bộ.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def __getattr__(self, level: str):
        def ghi(event: str, **kw) -> None:
            self.records.append((event, kw))
        return ghi

    def of(self, event: str) -> list[dict]:
        return [kw for name, kw in self.records if name == event]


@pytest.fixture
def bat_log(monkeypatch):
    """Thay `log` của backend SQL, trả về chỗ chứa các dòng đã ghi."""
    from fastapi_modular.infrastructure.database import sql as sql_module

    bat = _BatLog()
    monkeypatch.setattr(sql_module, "log", bat)
    return bat


# ------------------------------------------------------------------ khai báo
def test_khai_bao_doc_duoc_tu_entity():
    refs = dict(mapping_for(FkEvent).references)
    assert refs["camera_id"].target is FkCamera
    assert refs["camera_id"].on_delete == "CASCADE"
    assert dict(mapping_for(FkCamera).references)["zone_id"].on_delete == "SET NULL"


def test_on_delete_sai_bi_chan_ngay_luc_khai_bao():
    """Gõ sai lúc khai báo phải chết ngay, không đợi tới lúc có người xoá."""
    with pytest.raises(BadRequestError, match="on_delete"):
        reference(FkZone, on_delete="XOA_HET")      # type: ignore[arg-type]


# ------------------------------------------------------------------ CASCADE
async def test_cascade_xoa_cha_thi_con_di_theo(kho):
    await kho["cameras"].save(FkCamera(id="c1"))
    await kho["cameras"].save(FkCamera(id="c2"))
    await kho["events"].save(FkEvent(id="e1", camera_id="c1"))
    await kho["events"].save(FkEvent(id="e2", camera_id="c1"))
    await kho["events"].save(FkEvent(id="e3", camera_id="c2"))

    assert await kho["events"].count() == 3
    await kho["cameras"].delete("c1")

    assert await kho["events"].count() == 1, "hai sự kiện của c1 phải biến mất"
    assert (await kho["events"].get("e3")) is not None, "sự kiện camera khác không được đụng"
    assert await kho["cameras"].count() == 1, "camera c2 vẫn còn"


async def test_cascade_nhieu_tang(kho):
    """Xoá khu vực -> camera đi theo -> sự kiện của camera đó cũng đi theo.

    Chỉ đúng khi camera khai CASCADE. Ở đây camera khai SET NULL nên chuỗi
    DỪNG lại ở camera — và test này giữ đúng điều đó.
    """
    await kho["zones"].save(FkZone(id="z1"))
    await kho["cameras"].save(FkCamera(id="c1", zone_id="z1"))
    await kho["events"].save(FkEvent(id="e1", camera_id="c1"))

    await kho["zones"].delete("z1")

    assert await kho["cameras"].count() == 1, "SET NULL: camera ở lại"
    assert await kho["events"].count() == 1, "nên sự kiện cũng ở lại"
    assert (await kho["cameras"].get("c1")).zone_id is None


async def test_cascade_qua_delete_where(kho):
    """Xoá nhiều cha một lúc cũng phải áp khoá ngoại, không chỉ `delete(id)`."""
    for i in range(3):
        await kho["cameras"].save(FkCamera(id=f"c{i}", name="bỏ"))
        await kho["events"].save(FkEvent(id=f"e{i}", camera_id=f"c{i}"))

    assert await kho["cameras"].delete_where(name="bỏ") == 3
    assert await kho["events"].count() == 0


async def test_cascade_ba_tang_CASCADE_suot_chuoi(kho):
    """camera -> sự kiện -> khung hình, cả chuỗi khai CASCADE thì đi hết cả chuỗi.

    Đây là ca đã hỏng ngoài thật: tầng con thứ nhất bị xoá, tầng thứ hai ở lại.
    """
    await kho["cameras"].save(FkCamera(id="c1"))
    await kho["events"].save(FkEvent(id="e1", camera_id="c1"))
    await kho["events"].save(FkEvent(id="e2", camera_id="c1"))
    await kho["frames"].save(FkFrame(id="f1", event_id="e1"))
    await kho["frames"].save(FkFrame(id="f2", event_id="e1"))
    await kho["frames"].save(FkFrame(id="f3", event_id="e2"))
    # camera khác, không được đụng tới
    await kho["cameras"].save(FkCamera(id="c9"))
    await kho["events"].save(FkEvent(id="e9", camera_id="c9"))
    await kho["frames"].save(FkFrame(id="f9", event_id="e9"))

    await kho["cameras"].delete("c1")

    assert await kho["events"].count() == 1, "sự kiện của c1 phải đi theo"
    assert await kho["frames"].count() == 1, "khung hình của các sự kiện đó cũng vậy"
    assert (await kho["frames"].get("f9")).event_id == "e9", "nhánh c9 còn nguyên"


async def test_cascade_ba_tang_qua_delete_where(kho):
    """Xoá hàng loạt cũng phải đi hết ba tầng, không chỉ `delete(id)`."""
    for i in range(3):
        await kho["cameras"].save(FkCamera(id=f"c{i}", name="bỏ"))
        await kho["events"].save(FkEvent(id=f"e{i}", camera_id=f"c{i}"))
        await kho["frames"].save(FkFrame(id=f"f{i}", event_id=f"e{i}"))

    assert await kho["cameras"].delete_where(name="bỏ") == 3
    assert await kho["events"].count() == 0
    assert await kho["frames"].count() == 0


# ----------------------------------------------------------------- SET NULL
async def test_set_null_con_o_lai_va_mat_chuc_gan(kho):
    await kho["zones"].save(FkZone(id="z1"))
    await kho["cameras"].save(FkCamera(id="c1", zone_id="z1"))
    await kho["cameras"].save(FkCamera(id="c2", zone_id="z1"))

    await kho["zones"].delete("z1")

    assert await kho["cameras"].count() == 2, "camera KHÔNG bị xoá theo"
    assert [c.zone_id for c in await kho["cameras"].find()] == [None, None]


# -------------------------------------------------------------- SET DEFAULT
async def test_set_default_ve_dung_gia_tri_mac_dinh_cua_truong(kho):
    await kho["cameras"].save(FkCamera(id="c1"))
    await kho["tags"].save(FkTag(id="t1", camera_id="c1"))

    await kho["cameras"].delete("c1")

    assert (await kho["tags"].get("t1")).camera_id == "chua-gan"


# ----------------------------------------------------------------- RESTRICT
async def test_restrict_khong_cho_xoa_cha_khi_con_con(kho):
    """Hoá đơn không được biến mất theo khu vực — chặn ngay, lỗi 409."""
    await kho["zones"].save(FkZone(id="z1"))
    await kho["invoices"].save(FkInvoice(id="i1", zone_id="z1"))

    with pytest.raises(Exception) as loi:
        await kho["zones"].delete("z1")
    assert "FkInvoice" in str(loi.value) or "FOREIGN KEY" in str(loi.value).upper()

    assert await kho["zones"].count() == 1, "cha phải còn nguyên"
    assert await kho["invoices"].count() == 1


async def test_restrict_het_con_thi_xoa_duoc(kho):
    await kho["zones"].save(FkZone(id="z1"))
    await kho["invoices"].save(FkInvoice(id="i1", zone_id="z1"))
    await kho["invoices"].delete("i1")

    assert await kho["zones"].delete("z1") is True


async def test_restrict_o_memory_nem_dung_ConflictError():
    """SQL ném lỗi của driver; memory phải ném lỗi cho ra 409 giống vậy."""
    backend = create_backend(DatabaseSettings(driver="memory"))
    await backend.startup()
    db = _Db(backend)
    zones, invoices = Repository(FkZone, db), Repository(FkInvoice, db)
    await zones.save(FkZone(id="z9"))
    await invoices.save(FkInvoice(id="i9", zone_id="z9"))

    with pytest.raises(ConflictError, match="FkInvoice"):
        await zones.delete("z9")
    await backend.shutdown()


# --------------------------------------------- riêng SQLite: PRAGMA và DDL
@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_sqlite_bat_PRAGMA_foreign_keys(tmp_path):
    """SQLite TẮT khoá ngoại mặc định, và tắt nghĩa là CASCADE chỉ nằm làm cảnh.

    Đo được: bỏ PRAGMA đi thì DDL vẫn ghi `ON DELETE CASCADE`, nhưng xoá cha
    xong con vẫn còn nguyên — không lỗi, không cảnh báo, chỉ là dữ liệu mồ côi.
    Đây là lý do test này tồn tại.
    """
    from sqlalchemy import text

    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/fk.db"))
    await backend.startup()
    await backend.create_schema(FkZone, FkCamera, FkEvent)

    async with backend._engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        ddl = (await conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='fkevents'"))).scalar()
    assert "FOREIGN KEY(camera_id) REFERENCES fkcameras (id) ON DELETE CASCADE" in ddl
    await backend.shutdown()


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_moi_connection_trong_pool_deu_bat_foreign_keys(tmp_path):
    """`foreign_keys` là thiết lập của TỪNG connection.

    Đặt bằng một câu lệnh lúc khởi động thì chỉ connection đầu tiên có; những
    connection pool mở thêm sau đó lặng lẽ tắt khoá ngoại — và cascade hỏng
    đúng lúc tải lên cao.
    """
    import asyncio

    from sqlalchemy import text

    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/pool.db"))
    await backend.startup()
    await backend.create_schema(FkZone)

    async def doc() -> int:
        async with backend._engine.connect() as conn:
            await asyncio.sleep(0.05)          # giữ connection để buộc mở thêm
            return (await conn.execute(text("PRAGMA foreign_keys"))).scalar()

    assert await asyncio.gather(*(doc() for _ in range(5))) == [1] * 5
    await backend.shutdown()


# ------------------------------- bảng cũ: khoá ngoại trong database đã lạc hậu
@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_bang_tao_tu_truoc_thieu_khoa_ngoai_thi_phai_keu_len(tmp_path, bat_log):
    """Thêm `reference(...)` vào entity đã chạy rồi thì database KHÔNG biết.

    `create_all` chỉ tạo bảng còn thiếu, mode "sync" chỉ thêm/bớt CỘT — không
    cái nào đụng ràng buộc. Đo được hậu quả: xoá cha thì con tầng một đi theo,
    tầng hai ở lại thành mồ côi, lặng lẽ. Nên lúc khởi động phải kêu.
    """
    from sqlalchemy import text

    dsn = f"sqlite+aiosqlite:///{tmp_path}/cu.db"
    backend = create_backend(DatabaseSettings(driver="sqlite", dsn=dsn))
    await backend.startup()
    async with backend._engine.begin() as conn:
        # bảng cũ: cột có, khoá ngoại KHÔNG
        await conn.execute(text("CREATE TABLE fkframes (id VARCHAR PRIMARY KEY, "
                                "event_id VARCHAR, created_at DATETIME, updated_at DATETIME)"))

    await backend.create_schema(FkZone, FkCamera, FkEvent, FkFrame)

    keu = bat_log.of("db.foreign_keys_stale")
    assert keu, "phải có cảnh báo, không thì lỗi này lại lặng lẽ"
    assert keu[0]["problems"] == ["fkframes.event_id -> fkevents: database KHÔNG có khoá ngoại này"]
    await backend.shutdown()


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_on_delete_trong_database_khac_voi_khai_bao_thi_phai_keu_len(tmp_path, bat_log):
    """Đổi `on_delete` của entity cũng không tới được database. Cũng phải kêu."""
    from sqlalchemy import text

    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/lech.db"))
    await backend.startup()
    async with backend._engine.begin() as conn:
        await conn.execute(text("CREATE TABLE fkcameras (id VARCHAR PRIMARY KEY, "
                                "name VARCHAR, zone_id VARCHAR, "
                                "created_at DATETIME, updated_at DATETIME)"))
        await conn.execute(text(
            "CREATE TABLE fkevents (id VARCHAR PRIMARY KEY, camera_id VARCHAR "
            "REFERENCES fkcameras(id) ON DELETE SET NULL, "     # khai là CASCADE
            "created_at DATETIME, updated_at DATETIME)"))

    await backend.create_schema(FkCamera, FkEvent)

    keu = bat_log.of("db.foreign_keys_stale")
    assert keu
    assert "fkevents.camera_id: khai ON DELETE CASCADE, database đang SET NULL" \
        in keu[0]["problems"]
    await backend.shutdown()


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_schema_dung_khop_thi_khong_keu_gi_ca(tmp_path, bat_log):
    """Không được báo động giả: bảng do chính khung tạo thì phải im lặng.

    Chỗ dễ báo giả nhất là RESTRICT: khung ghi `ON DELETE RESTRICT` vào DDL,
    còn bảng nào không nói gì thì database đọc ra "NO ACTION".
    """
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/sach.db"))
    await backend.startup()
    await backend.create_schema(FkZone, FkCamera, FkEvent, FkInvoice, FkTag, FkFrame)

    assert bat_log.of("db.foreign_keys_stale") == []
    await backend.shutdown()


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_bang_khong_noi_gi_ve_on_delete_khop_voi_RESTRICT(tmp_path, bat_log):
    """`REFERENCES x(id)` trần trong database = "NO ACTION"; entity mặc định RESTRICT.

    Hai cái này chỉ khác nhau ở ràng buộc DEFERRABLE — thứ khung không dùng —
    nên KHÔNG được kêu. Không có phép gom này thì mọi bảng viết tay đều bị báo
    giả, và cảnh báo bị bỏ qua ngay lần thứ hai người ta nhìn thấy nó.
    """
    from sqlalchemy import text

    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/tran.db"))
    await backend.startup()
    async with backend._engine.begin() as conn:
        await conn.execute(text("CREATE TABLE fkzones (id VARCHAR PRIMARY KEY, "
                                "name VARCHAR, created_at DATETIME, updated_at DATETIME)"))
        await conn.execute(text(
            "CREATE TABLE fkinvoices (id VARCHAR PRIMARY KEY, "
            "zone_id VARCHAR REFERENCES fkzones(id), "        # không nói ON DELETE
            "created_at DATETIME, updated_at DATETIME)"))

    await backend.create_schema(FkZone, FkInvoice)

    assert bat_log.of("db.foreign_keys_stale") == []
    await backend.shutdown()


# ------------------------------------------------- ràng buộc LÚC GHI, không chỉ lúc xoá
async def test_ghi_con_tro_toi_cha_khong_ton_tai_bi_chan(kho):
    """Hai backend phải cùng từ chối. Đây đúng là chỗ đã suýt lệch nhau:

    SQL ném `FOREIGN KEY constraint failed`, còn memory ban đầu cho ghi thoải
    mái — nghĩa là `fam test` xanh mà production đổ.
    """
    with pytest.raises(Exception) as loi:
        await kho["events"].save(FkEvent(id="e-mo-coi", camera_id="khong-ton-tai"))
    assert "FOREIGN KEY" in str(loi.value).upper() or "FkCamera" in str(loi.value)

    assert await kho["events"].count() == 0


async def test_de_NULL_thi_duoc_vi_nghia_la_chua_gan(kho):
    await kho["cameras"].save(FkCamera(id="c1", zone_id=None))
    assert (await kho["cameras"].get("c1")).zone_id is None
