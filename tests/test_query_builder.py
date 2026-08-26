"""Test query builder — JOIN, so sánh, NULL.

Mỗi phép kiểm chạy trên **cả hai backend**: `memory` (mặc định của `fam test`)
và `sqlite` (SQL thật). Hai cột kết quả phải khớp nhau — đó là điều khiến
builder này dùng được: viết một lần, test trên memory, chạy trên postgres.

Bản sqlite cần `TEST_SQLITE=1`; không có thì chỉ chạy phần memory.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.container import entity
from fastapi_modular.core.exceptions import BadRequestError, NotFoundError
from fastapi_modular.infrastructure.database import F, Repository, or_
from fastapi_modular.infrastructure.database.factory import create_backend

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None


@entity()
@dataclass(slots=True)
class QCamera:
    id: str
    name: str
    zone: str
    threshold: float = 0.5
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class QEvent:
    id: str
    camera_id: str
    label: str
    score: float
    reviewed_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


class _Db:
    """Đủ để `Repository` chạy, không cần cả `Database` với retry và circuit."""

    def __init__(self, backend) -> None:
        self.backend = backend
        self.driver = backend.name


@pytest.fixture(params=["memory", pytest.param("sqlite", marks=pytest.mark.skipif(
    not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite"))])
async def kho(request, tmp_path):
    """Trả về (repo_event, repo_camera) trên backend đang thử, đã có sẵn dữ liệu."""
    if request.param == "memory":
        settings = DatabaseSettings(driver="memory")
    else:
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db"
        )
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):        # memory không có schema để dựng
        await backend.create_schema(QCamera, QEvent)

    db = _Db(backend)
    events, cameras = Repository(QEvent, db), Repository(QCamera, db)

    await cameras.save(QCamera(id="c1", name="Cổng chính", zone="Tầng 1", threshold=0.7))
    await cameras.save(QCamera(id="c2", name="Kho hàng", zone="Tầng 2", threshold=0.9))
    await cameras.save(QCamera(id="c3", name="Bãi xe", zone="Tầng 1", threshold=0.5))

    goc = utcnow()
    mau = [
        ("e0", "c1", "person", 0.95, None),
        ("e1", "c1", "person", 0.60, None),
        ("e2", "c1", "fire", 0.30, None),
        ("e3", "c2", "person", 0.95, None),
        ("e4", "c2", "car", 0.85, None),
        ("e5", "c1", "person", 0.99, goc),          # đã duyệt
    ]
    for i, (id_, cam, label, score, reviewed) in enumerate(mau):
        await events.save(
            QEvent(id=id_, camera_id=cam, label=label, score=score, reviewed_at=reviewed,
                   created_at=goc + timedelta(seconds=i))
        )
    yield events, cameras
    await backend.shutdown()


def ids(rows) -> list[str]:
    return sorted(r.id for r in rows)


# ------------------------------------------------------------ toán tử so sánh
async def test_lon_be_bang_khac(kho):
    events, _ = kho
    assert ids(await events.query().where(score__gte=0.9).all()) == ["e0", "e3", "e5"]
    assert ids(await events.query().where(score__gt=0.95).all()) == ["e5"]
    assert ids(await events.query().where(score__lt=0.5).all()) == ["e2"]
    assert ids(await events.query().where(score__lte=0.6).all()) == ["e1", "e2"]
    assert ids(await events.query().where(label__ne="person").all()) == ["e2", "e4"]
    assert ids(await events.query().where(label="fire").all()) == ["e2"]


async def test_in_between_like(kho):
    events, _ = kho
    assert ids(await events.query().where(label__in=["fire", "car"]).all()) == ["e2", "e4"]
    assert ids(await events.query().where(label__nin=["fire", "car"]).all()) == ["e0", "e1", "e3", "e5"]
    assert ids(await events.query().where(score__between=[0.5, 0.9]).all()) == ["e1", "e4"]
    assert ids(await events.query().where(label__like="pers%").all()) == ["e0", "e1", "e3", "e5"]
    assert ids(await events.query().where(label__startswith="per").all()) == ["e0", "e1", "e3", "e5"]
    assert ids(await events.query().where(label__contains="ers").all()) == ["e0", "e1", "e3", "e5"]


async def test_where_null_va_not_null(kho):
    """Chưa duyệt = `reviewed_at IS NULL`. Đây là ca `find()` không viết nổi."""
    events, _ = kho
    assert ids(await events.query().where(reviewed_at__isnull=True).all()) == [
        "e0", "e1", "e2", "e3", "e4"
    ]
    assert ids(await events.query().where(reviewed_at__isnull=False).all()) == ["e5"]

    E = F(QEvent)
    assert ids(await events.query().where(E.reviewed_at.is_null()).all()) == [
        "e0", "e1", "e2", "e3", "e4"
    ]
    assert ids(await events.query().where(E.reviewed_at == None).all()) == [  # noqa: E711
        "e0", "e1", "e2", "e3", "e4"
    ]


async def test_so_sanh_voi_null_luon_sai_giong_SQL(kho):
    """Trong SQL, `NULL > x` không đúng mà cũng không sai — dòng đó bị loại.

    Backend memory phải giữ y hệt, nếu không cùng một câu lệnh sẽ cho hai kết
    quả khác nhau giữa `fam test` và production. Đây là chỗ dễ lệch nhất.
    """
    events, _ = kho
    assert await events.query().where(reviewed_at__gt=utcnow()).count() == 0
    assert ids(await events.query().where(reviewed_at__ne="gì đó").all()) == ["e5"]


# ------------------------------------------------------------------- join
async def test_join_de_loc_van_tra_ve_entity_goc(kho):
    events, _ = kho
    rows = await events.query().join(QCamera, on="camera_id").where(qcamera__zone="Tầng 2").all()

    assert ids(rows) == ["e3", "e4"]
    assert all(isinstance(r, QEvent) for r in rows), "join để LỌC, không đổi kiểu trả về"


async def test_join_loc_theo_cot_bang_kia(kho):
    events, _ = kho
    rows = await (
        events.query()
        .join(QCamera, on="camera_id")
        .where(qcamera__name__like="Cổng%", score__gte=0.9)
        .all()
    )
    assert ids(rows) == ["e0", "e5"]


async def test_so_COT_voi_COT(kho):
    """`events.score > cameras.threshold` — kwargs không viết được ca này."""
    events, _ = kho
    E, C = F(QEvent), F(QCamera)
    rows = await events.query().join(QCamera, on=E.camera_id == C.id).where(E.score > C.threshold).all()

    # c1 ngưỡng 0.7 -> e0(.95) e5(.99); c2 ngưỡng 0.9 -> e3(.95)
    assert ids(rows) == ["e0", "e3", "e5"]


async def test_left_join_giu_dong_khong_khop(kho):
    """`outer=True` giữ camera chưa có sự kiện nào — cách tìm "cái nào trống"."""
    _, cameras = kho
    E = F(QEvent)
    rows = await (
        cameras.query()
        .join(QEvent, on=F(QCamera).id == E.camera_id, outer=True)
        .where(E.id.is_null())
        .all()
    )
    assert ids(rows) == ["c3"], "c3 chưa có sự kiện nào"


async def test_join_mot_nhieu_thi_distinct(kho):
    """c1 có 4 sự kiện -> không distinct thì c1 hiện 4 lần."""
    _, cameras = kho
    q = cameras.query().join(QEvent, on=F(QCamera).id == F(QEvent).camera_id)

    assert len(await q.all()) == 6, "mỗi sự kiện một dòng"
    assert ids(await q.distinct().all()) == ["c1", "c2"]


# --------------------------------------------------------------- OR / NOT
async def test_or_va_not(kho):
    events, _ = kho
    E = F(QEvent)
    assert ids(await events.query().where(or_(E.label == "fire", E.score >= 0.95)).all()) == [
        "e0", "e2", "e3", "e5"
    ]
    assert ids(await events.query().where(~(E.label == "person")).all()) == ["e2", "e4"]
    assert ids(await events.query().where((E.score >= 0.9) & (E.label == "car")).all()) == []


# ------------------------------------------------- sắp xếp, phân trang, đếm
async def test_order_limit_offset(kho):
    events, _ = kho
    theo_diem = [r.id for r in await events.query().order_by("-score").limit(3).all()]
    assert theo_diem == ["e5", "e0", "e3"] or theo_diem == ["e5", "e3", "e0"]

    trang2 = [r.id for r in await events.query().order_by("created_at").offset(2).limit(2).all()]
    assert trang2 == ["e2", "e3"]


async def test_count_first_exists_one(kho):
    events, _ = kho
    assert await events.query().where(score__gte=0.9).count() == 3
    assert await events.query().where(label="khong-co").exists() is False
    assert await events.query().where(label="fire").exists() is True
    assert (await events.query().order_by("-score").first()).id == "e5"
    assert await events.query().where(label="khong-co").first() is None

    with pytest.raises(NotFoundError):
        await events.query().where(label="khong-co").one()


async def test_count_khong_keo_dong_nao_ve(kho):
    """`count()` phải là `SELECT count(*)`, không phải `len(all())`."""
    events, _ = kho
    assert await events.query().join(QCamera, on="camera_id").where(qcamera__zone="Tầng 1").count() == 4


# ------------------------------------------------------------------ select
async def test_select_lay_duoc_cot_bang_da_join(kho):
    events, _ = kho
    rows = await (
        events.query()
        .join(QCamera, on="camera_id")
        .select("id", "score", camera_name=F(QCamera).name)
        .where(score__gte=0.95)
        .all()
    )
    assert all(isinstance(r, dict) for r in rows)
    assert {r["camera_name"] for r in rows} == {"Cổng chính", "Kho hàng"}
    assert sorted(rows[0]) == ["camera_name", "id", "score"]


# ------------------------------------------------------- báo lỗi cho tử tế
async def test_go_sai_ten_cot_bao_ngay_va_liet_ke_ten_dung(kho):
    events, _ = kho
    with pytest.raises(BadRequestError, match="không có trường 'scoree'"):
        events.query().where(scoree__gte=0.8)

    with pytest.raises(BadRequestError) as loi:
        _ = F(QEvent).khong_co_dau
    assert "camera_id" in str(loi.value), "phải liệt kê tên hợp lệ để sửa được ngay"


async def test_chua_join_ma_loc_theo_bang_kia_thi_bao_ro(kho):
    events, _ = kho
    with pytest.raises(BadRequestError, match="không phải bảng đã `join`"):
        events.query().where(khong_co_bang__zone="Tầng 1")


async def test_join_trung_ten_bi_chan(kho):
    events, _ = kho
    with pytest.raises(BadRequestError, match="alias="):
        events.query().join(QCamera, on="camera_id").join(QCamera, on="camera_id")


async def test_toan_tu_khong_co_thi_liet_ke_toan_tu_dung(kho):
    events, _ = kho
    with pytest.raises(BadRequestError, match="'lonhon' không phải toán tử"):
        events.query().where(score__lonhon=0.8)


# -------------------------------------------------------- riêng SQL: .sql()
@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_sinh_ra_dung_la_cau_SQL(tmp_path):
    """Cả điểm của builder: điều kiện chạy DƯỚI database, không lọc bằng Python."""
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/sql.db"))
    await backend.startup()
    await backend.create_schema(QCamera, QEvent)
    events = Repository(QEvent, _Db(backend))

    sql = (
        events.query()
        .join(QCamera, on="camera_id")
        .where(score__gte=0.8, reviewed_at__isnull=True)
        .where(qcamera__name__like="Cổng%")
        .order_by("-created_at")
        .limit(20)
        .sql()
    )
    gon = " ".join(sql.split())

    assert "JOIN qcameras ON qevents.camera_id = qcameras.id" in gon
    assert "qevents.score >= 0.8" in gon
    assert "qevents.reviewed_at IS NULL" in gon
    assert "qcameras.name LIKE 'Cổng%'" in gon
    assert "ORDER BY qevents.created_at DESC" in gon
    assert "LIMIT 20" in gon
    await backend.shutdown()


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_limit_ap_o_DATABASE_chu_khong_phai_trong_python(tmp_path):
    """Khác `find(match=...)` vốn kéo cả bảng về rồi mới cắt."""
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/lim.db"))
    await backend.startup()
    await backend.create_schema(QCamera, QEvent)
    events = Repository(QEvent, _Db(backend))
    for i in range(50):
        await events.save(QEvent(id=f"x{i}", camera_id="c1", label="person", score=i / 100))

    sql = events.query().where(score__gte=0.1).limit(5).sql()
    assert "LIMIT 5" in " ".join(sql.split()), "LIMIT phải nằm trong câu SQL"
    assert len(await events.query().where(score__gte=0.1).limit(5).all()) == 5
    await backend.shutdown()
