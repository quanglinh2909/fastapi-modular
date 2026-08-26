"""Query builder trên MongoDB — đối chiếu từng ca với backend `memory`.

Cần một MongoDB thật:

    TEST_MONGO_DSN='mongodb://root:root@127.0.0.1:27017/?authSource=admin' fam test

Mỗi phép kiểm chạy cùng một câu lệnh trên `memory` và trên `mongodb` rồi so kết
quả. Đây là cách duy nhất bắt được những chỗ Mongo hiểu khác SQL — mà có thật:
`{n: {"$ne": 1}}` của Mongo TRẢ VỀ cả document không có trường `n`, còn SQL thì
`NULL != 1` là không-đúng nên loại.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pytest

from fastapi_modular import Entity, entity, reference
from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.compat import StrEnum
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.infrastructure.database import (
    F,
    Repository,
    and_,
    count,
    in_,
    or_,
)
from fastapi_modular.infrastructure.database.factory import create_backend

DSN = os.getenv("TEST_MONGO_DSN", "")
CO_MONGO = bool(DSN) and importlib.util.find_spec("motor") is not None

pytestmark = pytest.mark.skipif(
    not CO_MONGO, reason="đặt TEST_MONGO_DSN và cài motor (fam install mongodb)"
)


class MTrangThai(StrEnum):
    ON = "online"
    OFF = "offline"


class MLoai(Enum):
    """Enum THƯỜNG, không phải StrEnum — pymongo không tự mã hoá được kiểu này."""

    THUONG = "thuong"
    DAC_BIET = "dac_biet"


@entity(name="mq_cameras")
@dataclass(slots=True)
class MCamera(Entity):
    id: str
    name: str
    zone: str
    status: MTrangThai = MTrangThai.OFF
    kind: MLoai = MLoai.THUONG
    threshold: float = 0.5
    rtsp: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@entity(name="mq_events")
@dataclass(slots=True)
class MEvent(Entity):
    id: str
    label: str
    score: float
    camera_id: str = field(metadata=reference(MCamera))


class _Db:
    def __init__(self, backend) -> None:
        self.backend, self.driver = backend, backend.name


CAMERAS = [
    ("c1", "Cổng chính", "Tầng 1", MTrangThai.ON, MLoai.DAC_BIET, 0.7, "rtsp://a"),
    ("c2", "Kho hàng", "Tầng 2", MTrangThai.OFF, MLoai.THUONG, 0.9, None),
    ("c3", "Bãi xe", "Tầng 1", MTrangThai.OFF, MLoai.THUONG, 0.5, None),
]
EVENTS = [
    ("e0", "person", 0.95, "c1"), ("e1", "person", 0.60, "c1"),
    ("e2", "fire", 0.30, "c1"), ("e3", "person", 0.99, "c2"),
    ("e4", "car", 0.85, "c2"),
]


async def _mo(driver: str):
    if driver == "memory":
        settings = DatabaseSettings(driver="memory")
    else:
        settings = DatabaseSettings(driver="mongodb", dsn=DSN, name=f"fam_test_{uuid.uuid4().hex[:8]}")
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(MCamera, MEvent)

    db = _Db(backend)
    cameras, events = Repository(MCamera, db), Repository(MEvent, db)
    for id_, name, zone, status, kind, threshold, rtsp in CAMERAS:
        await cameras.save(MCamera(id=id_, name=name, zone=zone, status=status,
                                   kind=kind, threshold=threshold, rtsp=rtsp))
    for id_, label, score, camera_id in EVENTS:
        await events.save(MEvent(id=id_, label=label, score=score, camera_id=camera_id))
    return backend, cameras, events


@pytest.fixture(params=["memory", "mongodb"])
async def kho(request):
    """(repo_event, repo_camera) trên backend đang thử; dọn sạch sau khi xong."""
    backend, cameras, events = await _mo(request.param)
    yield events, cameras
    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    await backend.shutdown()


def ids(rows) -> list[str]:
    return sorted(r.id if hasattr(r, "id") else r["id"] for r in rows)


# ------------------------------------------------------------- so sánh
async def test_lon_be_bang_khac(kho):
    events, _ = kho
    E = F(MEvent)
    assert ids(await events.query().where(E.score >= 0.9).all()) == ["e0", "e3"]
    assert ids(await events.query().where(E.score < 0.5).all()) == ["e2"]
    assert ids(await events.query().where(E.label == "fire").all()) == ["e2"]


async def test_khac_bo_qua_dong_NULL_giong_SQL(kho):
    """Đây là chỗ Mongo hiểu khác SQL nhất: `$ne` của nó GIỮ document thiếu trường."""
    _, cameras = kho
    C = F(MCamera)
    assert ids(await cameras.query().where(C.rtsp != "rtsp://a").all()) == [], (
        "c2, c3 có rtsp NULL — SQL loại chúng khỏi `!=`"
    )
    assert ids(await cameras.query().not_in(MCamera.rtsp, ["rtsp://a"]).all()) == []


async def test_is_null_va_is_not_null(kho):
    _, cameras = kho
    assert ids(await cameras.query().is_null(MCamera.rtsp).all()) == ["c2", "c3"]
    assert ids(await cameras.query().is_not_null(MCamera.rtsp).all()) == ["c1"]


async def test_in_between(kho):
    events, _ = kho
    assert ids(await events.query().in_(MEvent.label, ["fire", "car"]).all()) == ["e2", "e4"]
    assert ids(await events.query().between(MEvent.score, 0.60, 0.95).all()) == \
        ["e0", "e1", "e4"]


async def test_like_ilike(kho):
    _, cameras = kho
    assert ids(await cameras.query().like(MCamera.name, "Kho%").all()) == ["c2"]
    assert ids(await cameras.query().like(MCamera.name, "kho%").all()) == [], \
        "like phân biệt hoa thường ở cả ba backend"
    assert ids(await cameras.query().ilike(MCamera.name, "kHo%").all()) == ["c2"]
    assert ids(await cameras.query().like(MCamera.name, "Kho_hàng").all()) == ["c2"]


async def test_like_khop_CA_CHUOI_chu_khong_phai_mot_doan(kho):
    """Không có `%` thì `LIKE` là so khớp cả chuỗi, không phải "có chứa"."""
    _, cameras = kho
    assert ids(await cameras.query().like(MCamera.name, "hàng").all()) == []
    assert ids(await cameras.query().like(MCamera.name, "%hàng").all()) == ["c2"]
    assert ids(await cameras.query().like(MCamera.name, "Kho hàng").all()) == ["c2"]


async def test_loc_theo_Enum_thuong(kho):
    """Enum lưu bằng `.value`, nên lọc cũng phải so bằng `.value`.

    Với `Enum` thường (không phải `StrEnum`) mà quên đổi thì pymongo không mã
    hoá nổi giá trị và ném lỗi ngay.
    """
    _, cameras = kho
    assert ids(await cameras.query().where(kind=MLoai.DAC_BIET).all()) == ["c1"]
    assert ids(await cameras.query().in_(MCamera.kind, [MLoai.THUONG]).all()) == ["c2", "c3"]


async def test_like_khong_de_ky_tu_regex_lot_qua(kho):
    """`.` trong mẫu phải là dấu chấm thật, không phải ký tự đại diện của regex."""
    _, cameras = kho
    assert ids(await cameras.query().like(MCamera.name, "Kho.hàng").all()) == []


async def test_or_where_va_and_long_nhau(kho):
    events, _ = kho
    E = F(MEvent)
    assert ids(await events.query().where(E.label == "fire").or_where(E.score >= 0.95).all()) \
        == ["e0", "e2", "e3"]
    assert ids(await events.query().where(or_(
        and_(E.label == "person", E.score >= 0.9),
        and_(E.label == "fire", E.score >= 0.3))).all()) == ["e0", "e2", "e3"]
    assert ids(await events.query().where(~in_(MEvent.label, ["person"])).all()) == \
        ["e2", "e4"]


async def test_sap_xep_phan_trang(kho):
    events, _ = kho
    assert [r.id for r in await events.query().order_by_desc("score").limit(2).all()] == \
        ["e3", "e0"]
    assert [r.id for r in await events.query().order_by_asc("id").offset(3).all()] == \
        ["e3", "e4"]
    assert (await events.query().order_by_desc("score").first()).id == "e3"


async def test_fields_va_exclude(kho):
    _, cameras = kho
    rows = await cameras.query().fields("id", "name").order_by_asc("id").all()
    assert rows == [{"id": "c1", "name": "Cổng chính"},
                    {"id": "c2", "name": "Kho hàng"},
                    {"id": "c3", "name": "Bãi xe"}]

    row = (await cameras.query().where(id="c2").exclude(
        "created_at", "threshold", "rtsp", "zone", "kind").all())[0]
    assert row == {"id": "c2", "name": "Kho hàng", "status": MTrangThai.OFF}


async def test_ep_kieu_giong_het_luc_tra_ve_entity(kho):
    """Enum và datetime trong dict phải giống hệt trong entity."""
    _, cameras = kho
    row = (await cameras.query().where(id="c1").fields("status", "created_at").all())[0]
    entity_ = (await cameras.query().where(id="c1").all())[0]
    assert row["status"] is MTrangThai.ON is entity_.status
    assert row["created_at"].tzinfo is not None


async def test_count_exists(kho):
    events, _ = kho
    assert await events.query().where(F(MEvent).score >= 0.9).count() == 2
    assert await events.query().where(F(MEvent).label == "fire").exists() is True
    assert await events.query().where(F(MEvent).label == "khong-co").exists() is False


async def test_include_va_nest_under_chay_duoc_tren_mongo(kho):
    """Hai cái này không cần `$lookup`: chúng là câu lệnh riêng + ghép trong Python."""
    events, cameras = kho
    rows = await (cameras.query().fields("id").include(MEvent, fields=["id"])
                  .order_by_asc("id").all())
    assert [e["id"] for e in rows[0]["mevents"]] == ["e0", "e1", "e2"]
    assert rows[2] == {"id": "c3", "mevents": []}

    nested = await (events.query().where(F(MEvent).score >= 0.95).fields("id")
                    .nest_under(MCamera, fields=["id"]).all())
    assert sorted(r["id"] for r in nested) == ["c1", "c2"]


# ------------------------------------------------- cái chưa làm được, báo cho tử tế
async def test_join_bao_ro_va_chi_sang_include(kho):
    events, _ = kho
    if events._db.driver != "mongodb":
        pytest.skip("chỉ kiểm trên mongodb")
    with pytest.raises(BadRequestError) as loi:
        await events.query().join(MCamera).all()
    assert "include" in str(loi.value) and "nest_under" in str(loi.value)


async def test_group_by_bao_ro(kho):
    events, _ = kho
    if events._db.driver != "mongodb":
        pytest.skip("chỉ kiểm trên mongodb")
    with pytest.raises(BadRequestError) as loi:
        await (events.query().group_by(MEvent.camera_id)
               .select("camera_id", so=count()).all())
    assert "group_by" in str(loi.value)
