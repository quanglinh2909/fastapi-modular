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
from fastapi_modular.core.compat import StrEnum
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.container import entity
from fastapi_modular.core.exceptions import BadRequestError, NotFoundError
from fastapi_modular.infrastructure.database import (
    Entity,
    F,
    Repository,
    and_,
    avg,
    count,
    is_not_null,
    is_null,
    max_,
    min_,
    or_,
    reference,
    sum_,
)
from fastapi_modular.infrastructure.database.factory import create_backend

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None


class QTrangThai(StrEnum):
    ON = "online"
    OFF = "offline"


@entity()
@dataclass(slots=True)
class QCamera(Entity):
    id: str
    name: str
    zone: str
    status: QTrangThai = QTrangThai.OFF
    threshold: float = 0.5
    parent_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class QEvent:
    id: str
    camera_id: str = field(metadata=reference(QCamera))
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

    await cameras.save(QCamera(id="c1", name="Cổng chính", zone="Tầng 1", threshold=0.7,
                               status=QTrangThai.ON))
    await cameras.save(QCamera(id="c2", name="Kho hàng", zone="Tầng 2", threshold=0.9,
                               parent_id="c1"))
    await cameras.save(QCamera(id="c3", name="Bãi xe", zone="Tầng 1", threshold=0.5,
                               parent_id="c1"))

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


async def test_ba_cach_loc_NULL_cho_cung_ket_qua(kho):
    """`is_null(...)` dùng được cả khi entity chưa kế thừa `Entity` — QEvent thì chưa."""
    events, _ = kho
    chua_duyet = ["e0", "e1", "e2", "e3", "e4"]

    assert ids(await events.query().where(is_null(QEvent.reviewed_at)).all()) == chua_duyet
    assert ids(await events.query().where(F(QEvent).reviewed_at.is_null()).all()) == chua_duyet
    assert ids(await events.query().where(reviewed_at__isnull=True).all()) == chua_duyet

    assert ids(await events.query().where(is_not_null(QEvent.reviewed_at)).all()) == ["e5"]


async def test_is_null_tren_entity_ke_thua_Entity(kho):
    _, cameras = kho
    assert ids(await cameras.query().where(is_null(QCamera.parent_id)).all()) == ["c1"]
    assert ids(await cameras.query().where(QCamera.parent_id.is_null()).all()) == ["c1"]


async def test_order_by_chieu_nam_trong_ten_ham(kho):
    events, _ = kho
    assert [r.id for r in await events.query().order_by_desc("score").limit(2).all()] == \
        ["e5", "e0"]
    assert [r.id for r in await events.query().order_by_asc("score").limit(2).all()] == \
        ["e2", "e1"]


async def test_order_by_nhieu_cot_thu_tu_goi_la_thu_tu_uu_tien(kho):
    """`label` tăng dần, trong mỗi label thì `score` giảm dần."""
    events, _ = kho
    rows = await events.query().order_by_asc("label").order_by_desc("score").all()
    assert [r.id for r in rows] == ["e4", "e2", "e5", "e0", "e3", "e1"]


async def test_so_sanh_voi_null_luon_sai_giong_SQL(kho):
    """Trong SQL, `NULL > x` không đúng mà cũng không sai — dòng đó bị loại.

    Backend memory phải giữ y hệt, nếu không cùng một câu lệnh sẽ cho hai kết
    quả khác nhau giữa `fam test` và production. Đây là chỗ dễ lệch nhất.
    """
    events, _ = kho
    assert await events.query().where(reviewed_at__gt=utcnow()).count() == 0
    assert ids(await events.query().where(reviewed_at__ne="gì đó").all()) == ["e5"]


# ------------------------------------------------------------------- join
async def test_join_khong_can_noi_cot_nao(kho):
    """`.join(QCamera)` trần — cột nối đọc từ `reference(QCamera)` đã khai."""
    events, _ = kho
    rows = await events.query().join(QCamera).where(qcamera__zone="Tầng 2").all()
    assert ids(rows) == ["e3", "e4"]


async def test_join_bang_cot_that_thay_vi_chuoi(kho):
    """`on=QEvent.camera_id` — cột thật, gõ sai là gãy lúc import."""
    events, _ = kho
    rows = await events.query().join(QCamera, on=QEvent.camera_id).where(qcamera__zone="Tầng 2").all()
    assert ids(rows) == ["e3", "e4"]


async def test_join_bang_cot_cua_bang_kia_la_chieu_mot_nhieu(kho):
    """Cùng một cột, đảo bảng gốc: `cameras.join(QEvent, on=QEvent.camera_id)`."""
    _, cameras = kho
    rows = await cameras.query().join(QEvent, on=QEvent.camera_id).where(qevent__label="car").all()
    assert ids(rows) == ["c2"]


async def test_join_tu_suy_ca_chieu_nguoc(kho):
    """`cameras.join(QEvent)` cũng suy được, dù khoá ngoại nằm bên QEvent."""
    _, cameras = kho
    rows = await cameras.query().join(QEvent).where(qevent__label="car").all()
    assert ids(rows) == ["c2"]


async def test_bon_cach_viet_on_cho_cung_mot_cau_SQL(kho):
    """Ba cách viết mới và cách cũ phải sinh ra cùng một điều kiện nối."""
    events, _ = kho
    cach = [
        events.query().join(QCamera),
        events.query().join(QCamera, on=QEvent.camera_id),
        events.query().join(QCamera, on="camera_id"),
        events.query().join(QCamera, on=("camera_id", "id")),
        events.query().join(QCamera, on=F(QEvent).camera_id == F(QCamera).id),
    ]
    dieu_kien = {repr(q._spec.joins[0].on) for q in cach}
    assert len(dieu_kien) == 1, dieu_kien


async def test_order_by_va_select_nhan_cot_that(kho):
    events, _ = kho
    rows = await (
        events.query()
        .join(QCamera)
        .select(QEvent.id, ten=QCamera.name)
        .order_by_asc(QEvent.score)
        .limit(1)
        .all()
    )
    assert rows == [{"id": "e2", "ten": "Cổng chính"}]


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
    """`left_join` giữ camera chưa có sự kiện nào — cách tìm "cái nào trống"."""
    _, cameras = kho
    E = F(QEvent)
    rows = await (
        cameras.query()
        .left_join(QEvent, on=F(QCamera).id == E.camera_id)
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


# ------------------------------------------- toán tử thường (kế thừa Entity)
async def test_toan_tu_thuong_thay_cho_duoi_gach(kho):
    """`QCamera.threshold >= 0.7` — QCamera có kế thừa `Entity`."""
    _, cameras = kho
    assert ids(await cameras.query().where(QCamera.threshold >= 0.7).all()) == ["c1", "c2"]
    assert ids(await cameras.query().where(QCamera.zone == "Tầng 1").all()) == ["c1", "c3"]
    assert ids(await cameras.query().where(QCamera.name.like("Cổng%")).all()) == ["c1"]
    assert ids(await cameras.query().where(
        or_(QCamera.threshold > 0.8, QCamera.zone == "Tầng 1")).all()) == ["c1", "c2", "c3"]


async def test_toan_tu_thuong_dung_duoc_ca_o_join_va_order_by(kho):
    events, _ = kho
    rows = await (
        events.query()
        .join(QCamera)
        .where(QCamera.zone == "Tầng 2", F(QEvent).score >= 0.9)   # QEvent không kế thừa Entity
        .order_by_asc(QCamera.name)
        .all()
    )
    assert ids(rows) == ["e3"]


async def test_ke_thua_entity_khong_lam_doi_tuong_nang_them(kho):
    """Cái giá của `Entity` phải bằng 0 với đối tượng — chỉ đọc TỪ LỚP mới khác."""
    import sys

    @dataclass(slots=True)
    class Doi:                                   # y hệt QCamera nhưng không kế thừa
        id: str
        name: str
        zone: str
        status: QTrangThai = QTrangThai.OFF
        threshold: float = 0.5
        parent_id: str | None = None
        created_at: datetime = field(default_factory=utcnow)
        updated_at: datetime = field(default_factory=utcnow)

    a = QCamera(id="x", name="n", zone="z")
    b = Doi(id="x", name="n", zone="z")
    assert not hasattr(a, "__dict__"), "kế thừa Entity mà mọc lại __dict__ là mất hết slots"
    assert sys.getsizeof(a) == sys.getsizeof(b)
    a.threshold = 0.9                            # ghi vẫn đi đường slot bình thường
    assert a.threshold == 0.9


async def test_lop_con_khai_lai_truong_van_giu_gia_tri_mac_dinh():
    """Bẫy của metaclass: `@dataclass` đọc mặc định bằng `getattr(cls, ten)`."""
    @dataclass(slots=True)
    class QCamCon(QCamera):
        threshold: float = 0.99

    assert QCamCon(id="x", name="n", zone="z").threshold == 0.99
    assert isinstance(QCamCon.threshold >= 0.5, object)


async def test_chua_ke_thua_Entity_ma_dung_bang_bang_thi_bao_ro(kho):
    """`QEvent.label == "x"` cho ra `False` chứ không lỗi — phải chặn, không nuốt."""
    events, _ = kho
    with pytest.raises(BadRequestError) as loi:
        events.query().where(QEvent.label == "person")
    assert "Entity" in str(loi.value) and "F(Event)" in str(loi.value)

    with pytest.raises(BadRequestError):
        or_(QEvent.label == "person", QEvent.label == "car")


def test_chu_ky_where_de_Any_de_IDE_khong_gach_do_cau_dung():
    """Khai `Condition` ở đây là IDE báo lỗi trên câu HOÀN TOÀN ĐÚNG.

    Type checker đọc annotation `score: float` nên với nó `Camera.score > 1`
    là `bool` — metaclass là thứ chỉ có lúc chạy, nó không thấy. Sai kiểu thật
    đã có `as_condition` bắt lúc chạy.
    """
    import inspect

    from fastapi_modular.infrastructure.database import Query

    for ten in ("where",):
        chu_ky = inspect.signature(getattr(Query, ten))
        assert chu_ky.parameters["conditions"].annotation == "Any", ten


# ------------------------------------------------- nối bảng với chính nó
async def test_self_join_bang_alias(kho):
    """Camera có `parent_id` trỏ về camera khác — cùng một bảng, hai vai."""
    _, cameras = kho
    Cha = F(QCamera, "cha")
    rows = await (
        cameras.query()
        .join(QCamera, on=QCamera.parent_id, alias="cha")
        .select("id", cha=Cha.name)
        .order_by_asc("id")
        .all()
    )
    assert rows == [{"id": "c2", "cha": "Cổng chính"}, {"id": "c3", "cha": "Cổng chính"}]


async def test_self_join_loc_theo_bang_kia(kho):
    """Điều kiện trên bảng đã đặt alias, cả hai kiểu viết."""
    _, cameras = kho
    Cha = F(QCamera, "cha")
    q = cameras.query().join(QCamera, on=QCamera.parent_id, alias="cha")
    assert ids(await q.where(Cha.zone == "Tầng 1").all()) == ["c2", "c3"]

    q2 = cameras.query().join(QCamera, on=QCamera.parent_id, alias="cha")
    assert ids(await q2.where(cha__name__like="Cổng%").all()) == ["c2", "c3"]


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_self_join_sinh_ra_alias_that_trong_SQL(tmp_path):
    """Không có alias thật thì câu lệnh là `FROM qcameras JOIN qcameras` — vô nghĩa."""
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/self.db"))
    await backend.startup()
    await backend.create_schema(QCamera)
    cameras = Repository(QCamera, _Db(backend))

    sql = " ".join(
        cameras.query().join(QCamera, on=QCamera.parent_id, alias="cha")
        .select("id", cha=F(QCamera, "cha").name).sql().split()
    )
    assert "JOIN qcameras AS cha ON qcameras.parent_id = cha.id" in sql
    assert "cha.name" in sql
    await backend.shutdown()


async def test_select_ep_kieu_y_het_luc_tra_ve_entity(kho):
    """Enum và datetime trong dict phải giống hệt trong entity — hai backend cũng vậy.

    Trước đây sqlite trả thẳng giá trị thô: `'online'` thay vì `QTrangThai.ON`,
    và datetime KHÔNG mang múi giờ. Test chạy trên memory thì xanh, production
    trên sqlite/postgres lại ra kiểu khác.
    """
    _, cameras = kho
    row = (await cameras.query().where(id="c1").fields("status", "created_at").all())[0]
    entity_ = (await cameras.query().where(id="c1").all())[0]

    assert row["status"] is QTrangThai.ON is entity_.status
    assert row["created_at"].tzinfo is not None, "datetime phải mang múi giờ như entity"
    assert row["created_at"] == entity_.created_at


# ------------------------------------------------- dữ liệu lồng nhau (include)
async def test_include_gan_list_con_vao_cha(kho):
    """Camera kèm danh sách sự kiện, tên trường mặc định là `qevents`."""
    _, cameras = kho
    rows = await cameras.query().fields("id").include(QEvent, fields=["id"]).order_by_asc("id").all()

    assert rows[0]["id"] == "c1"
    assert [e["id"] for e in rows[0]["qevents"]] == ["e0", "e1", "e2", "e5"]
    assert rows[2] == {"id": "c3", "qevents": []}, "không có con thì là list rỗng"


async def test_include_gan_object_cha_vao_con(kho):
    """Chiều ngược lại: khoá ngoại nằm bên QEvent nên mỗi sự kiện có MỘT camera."""
    events, _ = kho
    rows = await (events.query().where(id="e3").fields("id")
                  .include(QCamera, fields=["id", "name"]).all())
    assert rows == [{"id": "e3", "qcamera": {"id": "c2", "name": "Kho hàng"}}]


async def test_include_doi_ten_truong(kho):
    _, cameras = kho
    rows = await (cameras.query().where(id="c2").fields("id")
                  .include(QEvent, name="su_kien", fields=["id"]).all())
    assert rows == [{"id": "c2", "su_kien": [{"id": "e3"}, {"id": "e4"}]}]


async def test_include_chon_va_loai_tru_cot_cua_bang_con(kho):
    _, cameras = kho
    rows = await (cameras.query().where(id="c2").fields("id")
                  .include(QEvent, exclude=["created_at", "updated_at", "camera_id",
                                            "reviewed_at", "score"]).all())
    assert rows[0]["qevents"][0].keys() == {"id", "label"}


async def test_include_loc_va_sap_bang_con(kho):
    _, cameras = kho
    rows = await (cameras.query().where(id="c1").fields("id")
                  .include(QEvent, fields=["id", "score"],
                           where=F(QEvent).score >= 0.9, order_by_desc="score").all())
    assert [e["id"] for e in rows[0]["qevents"]] == ["e5", "e0"]


async def test_include_khong_lam_lo_cot_ghep_khong_ai_xin(kho):
    """`camera_id` phải có mặt để ghép, nhưng người dùng không xin thì đừng trả."""
    events, _ = kho
    rows = await events.query().where(id="e0").fields("id").include(QCamera).all()
    assert set(rows[0]) == {"id", "qcamera"}


async def test_include_chi_them_MOT_cau_lenh_chu_khong_phai_moi_dong_mot_cau(kho):
    """Đây là cả điểm của include: 3 camera -> 2 câu lệnh, không phải 4."""
    _, cameras = kho
    backend = cameras._db.backend
    that = backend.run_query
    dem = []

    async def dem_lai(spec):
        dem.append(spec.entity.__name__)
        return await that(spec)

    backend.run_query = dem_lai
    try:
        await cameras.query().include(QEvent).all()
    finally:
        backend.run_query = that
    assert dem == ["QCamera", "QEvent"]


async def test_include_chia_me_khi_qua_nhieu_id(kho, monkeypatch):
    """SQLite bản cũ chỉ cho 999 tham số một câu, nên `IN (...)` phải chia mẻ.

    Không dựng nổi 999 camera trong một test, nên hạ ngưỡng xuống 2 rồi đếm số
    câu lệnh: 3 camera chia mẻ 2 phải thành 2 câu con, và không được mất dòng
    nào khi ghép lại.
    """
    from fastapi_modular.infrastructure.database import query as mod

    monkeypatch.setattr(mod, "IN_CHUNK", 2)
    _, cameras = kho
    backend = cameras._db.backend
    that = backend.run_query
    dem = []

    async def dem_lai(spec):
        dem.append(spec.entity.__name__)
        return await that(spec)

    backend.run_query = dem_lai
    try:
        rows = await (cameras.query().fields("id")
                      .include(QEvent, fields=["id"]).order_by_asc("id").all())
    finally:
        backend.run_query = that

    assert dem == ["QCamera", "QEvent", "QEvent"], "3 id, mẻ 2 -> 2 câu con"
    assert sum(len(r["qevents"]) for r in rows) == 6, "chia mẻ mà mất dòng là hỏng"


async def test_include_khong_co_khoa_ngoai_thi_bao_ro(kho):
    events, _ = kho

    @entity()
    @dataclass(slots=True)
    class QRoi:
        id: str

    with pytest.raises(BadRequestError) as loi:
        events.query().include(QRoi)
    assert "reference" in str(loi.value)


async def test_fields_exclude_include_nest_deu_nhan_cot_that(kho):
    """`Camera.name` dùng được ở mọi chỗ chọn cột, không riêng chuỗi."""
    events, cameras = kho
    rows = await (cameras.query()
                  .fields(QCamera.id, QCamera.name)
                  .include(QEvent, fields=[QEvent.id])
                  .where(id="c2").all())
    assert rows == [{"id": "c2", "name": "Kho hàng", "qevents": [{"id": "e3"}, {"id": "e4"}]}]

    bo_bot = await (cameras.query()
                    .exclude(QCamera.parent_id, "status", "threshold",
                             "created_at", "updated_at")
                    .where(id="c2").all())
    assert bo_bot == [{"id": "c2", "name": "Kho hàng", "zone": "Tầng 2"}]

    nested = await (events.query().where(id="e4").fields(QEvent.id)
                    .nest_under(QCamera, fields=[QCamera.name]).all())
    assert nested == [{"name": "Kho hàng", "qevents": [{"id": "e4"}]}]


async def test_chon_cot_cua_bang_khac_thi_bao_ngay(kho):
    """`include(Event, fields=[Camera.name])` gần như luôn là gõ nhầm."""
    _, cameras = kho
    with pytest.raises(BadRequestError) as loi:
        cameras.query().include(QEvent, fields=[QCamera.name])
    assert "không phải" in str(loi.value)


async def test_fields_va_exclude_bat_ten_sai_ngay(kho):
    events, _ = kho
    with pytest.raises(BadRequestError) as loi:
        events.query().fields("khong_co_truong_nay")
    assert "Có:" in str(loi.value)

    with pytest.raises(BadRequestError):
        events.query().exclude("khong_co_truong_nay")


async def test_exclude_bo_dung_mot_cot(kho):
    events, _ = kho
    row = (await events.query().where(id="e0").exclude("score").all())[0]
    assert "score" not in row and "label" in row and "camera_id" in row


# ----------------------------------------- đảo chiều lồng nhau (nest_under)
async def test_nest_under_dua_cha_ra_ngoai(kho):
    """Lọc theo cột của sự kiện, nhưng nhận về camera kèm đúng các sự kiện đó."""
    events, _ = kho
    rows = await (
        events.query()
        .where(F(QEvent).score >= 0.95)
        .fields("id", "score")
        .nest_under(QCamera, fields=["id", "name"])
        .all()
    )
    assert rows == [
        {"id": "c1", "name": "Cổng chính",
         "qevents": [{"id": "e0", "score": 0.95}, {"id": "e5", "score": 0.99}]},
        {"id": "c2", "name": "Kho hàng", "qevents": [{"id": "e3", "score": 0.95}]},
    ]


async def test_nest_under_khac_include_o_cho_dieu_kien_nam_ben_nao(kho):
    """`include` = mọi camera; `nest_under` = chỉ camera CÓ sự kiện khớp."""
    events, cameras = kho
    qua_include = await (cameras.query().fields("id")
                         .include(QEvent, fields=["id"], where=F(QEvent).score >= 0.99).all())
    qua_nest = await (events.query().where(F(QEvent).score >= 0.99).fields("id")
                      .nest_under(QCamera, fields=["id"]).all())

    assert [r["id"] for r in qua_include] == ["c1", "c2", "c3"]
    assert qua_include[1]["qevents"] == [], "include giữ cả camera không có sự kiện nào khớp"
    assert [r["id"] for r in qua_nest] == ["c1"], "nest_under chỉ giữ camera có sự kiện khớp"


async def test_nest_under_doi_ten_va_chon_cot_hai_tang(kho):
    events, _ = kho
    rows = await (events.query().where(id="e4").fields("label")
                  .nest_under(QCamera, name="su_kien", fields=["name"]).all())
    assert rows == [{"name": "Kho hàng", "su_kien": [{"label": "car"}]}]


async def test_nest_under_giu_thu_tu_theo_bang_goc(kho):
    events, _ = kho
    rows = await (events.query().order_by_desc("created_at").fields("id")
                  .nest_under(QCamera, fields=["id"]).all())
    assert [r["id"] for r in rows] == ["c1", "c2"], "e5 của c1 mới nhất nên c1 đứng trước"


async def test_nest_under_bo_dong_co_khoa_ngoai_NULL(kho):
    """Camera gốc không có cha — nó không thuộc nhóm nào để gom vào."""
    _, cameras = kho
    rows = await (cameras.query().fields("id")
                  .nest_under(QCamera, on=QCamera.parent_id, name="con", fields=["id"]).all())
    assert rows == [{"id": "c1", "con": [{"id": "c2"}, {"id": "c3"}]}]


async def test_nest_under_khong_lo_cot_ghep(kho):
    events, _ = kho
    rows = await (events.query().where(id="e4").fields("id")
                  .nest_under(QCamera, fields=["id"]).all())
    assert set(rows[0]["qevents"][0]) == {"id"}, "camera_id chỉ để gom, không phải để trả"


def _dem_cau_lenh(backend):
    """Ghi lại từng câu lệnh chạy qua backend: (tên bảng, các điều kiện)."""
    that = backend.run_query
    da_chay = []

    async def dem_lai(spec):
        da_chay.append((spec.entity.__name__, list(spec.conditions)))
        return await that(spec)

    backend.run_query = dem_lai
    return da_chay, that


async def test_nest_under_chi_them_MOT_cau_lenh(kho):
    events, _ = kho
    backend = events._db.backend
    da_chay, that = _dem_cau_lenh(backend)
    try:
        await events.query().nest_under(QCamera).all()
    finally:
        backend.run_query = that
    assert [ten for ten, _ in da_chay] == ["QEvent", "QCamera"]


async def test_nest_under_khong_bao_gio_nhet_NULL_vao_IN(kho):
    """Camera gốc có `parent_id` NULL — đừng hỏi database về id NULL làm gì."""
    _, cameras = kho
    backend = cameras._db.backend
    da_chay, that = _dem_cau_lenh(backend)
    try:
        await cameras.query().nest_under(QCamera, on=QCamera.parent_id, name="con").all()
    finally:
        backend.run_query = that

    trong_in = [c.value for _, dk in da_chay for c in dk if c.op == "in"]
    assert trong_in and all(None not in values for values in trong_in), trong_in


async def test_nest_under_chia_me_khi_qua_nhieu_id(kho, monkeypatch):
    from fastapi_modular.infrastructure.database import query as mod

    monkeypatch.setattr(mod, "IN_CHUNK", 1)
    events, _ = kho
    backend = events._db.backend
    da_chay, that = _dem_cau_lenh(backend)
    try:
        rows = await events.query().fields("id").nest_under(QCamera, fields=["id"]).all()
    finally:
        backend.run_query = that

    assert [ten for ten, _ in da_chay] == ["QEvent", "QCamera", "QCamera"], "2 id, mẻ 1"
    assert sorted(r["id"] for r in rows) == ["c1", "c2"]


async def test_nest_under_sai_chieu_thi_chi_sang_include(kho):
    _, cameras = kho
    with pytest.raises(BadRequestError) as loi:
        cameras.query().nest_under(QEvent)
    assert "include" in str(loi.value), "phải chỉ luôn cách làm đúng"


async def test_nest_under_khong_dung_chung_voi_group_by(kho):
    events, _ = kho
    with pytest.raises(BadRequestError):
        events.query().group_by(QEvent.camera_id).nest_under(QCamera)


# -------------------------------------------------------------- RIGHT JOIN
async def test_right_join_giu_ben_phai(kho):
    """Nối hẹp lại để có sự kiện không khớp camera nào — chúng phải còn."""
    _, cameras = kho
    rows = await (
        cameras.query()
        .right_join(QEvent, on=and_(F(QEvent).camera_id == QCamera.id,
                                    F(QEvent).label == "person"))
        .select(cam=QCamera.id, ev=F(QEvent).id)
        .all()
    )
    cap = sorted((r["cam"] or "-", r["ev"] or "-") for r in rows)
    assert ("-", "e2") in cap, "sự kiện label=fire không khớp camera nào vẫn phải còn"
    assert not any(c == "c3" for c, _ in cap), "camera không có sự kiện thì RIGHT bỏ"


async def test_right_join_khong_co_select_thi_chi_cach_dao_lai(kho):
    events, _ = kho
    with pytest.raises(BadRequestError) as loi:
        await events.query().right_join(QCamera).all()
    assert "select" in str(loi.value).lower()
    assert "left_join" in str(loi.value), "phải chỉ luôn cách làm đúng"


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_bon_kieu_join_sinh_ra_bon_cau_SQL(tmp_path):
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/kieu.db"))
    await backend.startup()
    await backend.create_schema(QCamera, QEvent)
    events = Repository(QEvent, _Db(backend))
    cameras = Repository(QCamera, _Db(backend))

    def gon(q):
        return " ".join(q.sql().split())

    assert "JOIN qcameras ON" in gon(events.query().join(QCamera))
    assert "LEFT OUTER JOIN qcameras ON" in gon(events.query().left_join(QCamera))
    assert "FULL OUTER JOIN qcameras ON" in gon(
        events.query().outer_join(QCamera).select("id"))

    # RIGHT JOIN sinh ra LEFT JOIN với hai vế đảo chỗ — SQLite cũ không có RIGHT
    right = gon(cameras.query().right_join(QEvent).select("id"))
    assert "FROM qevents LEFT OUTER JOIN qcameras ON" in right
    assert "RIGHT" not in right
    await backend.shutdown()


# --------------------------------------------------------------- FULL JOIN
async def test_full_join_giu_ca_hai_ben(kho):
    """Nối theo điều kiện hẹp: còn camera không khớp ai VÀ sự kiện không khớp ai."""
    _, cameras = kho
    rows = await (
        cameras.query()
        .outer_join(QEvent, on=and_(F(QEvent).camera_id == QCamera.id,
                                   F(QEvent).label == "person"))
        .select(cam=QCamera.id, ev=F(QEvent).id)
        .all()
    )
    cap = sorted((r["cam"] or "-", r["ev"] or "-") for r in rows)
    assert ("c3", "-") in cap, "camera không có sự kiện person nào vẫn phải còn"
    assert ("-", "e2") in cap, "sự kiện label=fire không khớp camera nào vẫn phải còn"


async def test_full_join_khong_co_select_thi_bao_ro(kho):
    _, cameras = kho
    with pytest.raises(BadRequestError) as loi:
        await cameras.query().outer_join(QEvent).all()
    assert "select" in str(loi.value).lower()


# ------------------------------------------------------- gộp nhóm + HAVING
async def test_group_by_va_cac_ham_gop(kho):
    events, _ = kho
    rows = await (
        events.query()
        .group_by(QEvent.camera_id)
        .select("camera_id", so=count(), tb=avg(QEvent.score),
                cao=max_(QEvent.score), thap=min_(QEvent.score), tong=sum_(QEvent.score))
        .order_by_asc("camera_id")
        .all()
    )
    assert [r["camera_id"] for r in rows] == ["c1", "c2"]
    assert [r["so"] for r in rows] == [4, 2]
    assert rows[1]["cao"] == 0.95 and rows[1]["thap"] == 0.85
    assert round(rows[1]["tong"], 6) == 1.8


async def test_having_loc_theo_nhom_chu_khong_theo_dong(kho):
    events, _ = kho
    rows = await (events.query().group_by(QEvent.camera_id)
                  .select("camera_id", so=count()).having(count() > 2).all())
    assert rows == [{"camera_id": "c1", "so": 4}]


async def test_where_truoc_having_sau_cho_ket_qua_khac_nhau(kho):
    """Đây là chỗ hay nhầm nhất: `where` bỏ DÒNG, `having` bỏ NHÓM."""
    events, _ = kho
    chi_diem_cao = await (events.query().where(F(QEvent).score >= 0.9)
                          .group_by(QEvent.camera_id)
                          .select("camera_id", so=count()).order_by_asc("camera_id").all())
    assert chi_diem_cao == [{"camera_id": "c1", "so": 2}, {"camera_id": "c2", "so": 1}]

    moi_dong = await (events.query().group_by(QEvent.camera_id)
                      .select("camera_id", so=count()).order_by_asc("camera_id").all())
    assert moi_dong == [{"camera_id": "c1", "so": 4}, {"camera_id": "c2", "so": 2}]


async def test_gop_ca_bang_khong_can_group_by(kho):
    events, _ = kho
    assert await events.query().select(so=count(), tb=avg(QEvent.score)).all() == [
        {"so": 6, "tb": pytest.approx(sum([0.95, 0.6, 0.3, 0.95, 0.85, 0.99]) / 6)}
    ]


async def test_gop_tren_bang_rong_theo_dung_luat_SQL(kho):
    """`count` = 0 nhưng `sum` = NULL chứ không phải 0 — memory phải giống SQL."""
    events, _ = kho
    rows = await (events.query().where(F(QEvent).label == "khong-ton-tai")
                  .select(so=count(), tong=sum_(QEvent.score)).all())
    assert rows == [{"so": 0, "tong": None}]


async def test_count_bo_qua_NULL_con_count_sao_thi_khong(kho):
    events, _ = kho
    rows = await events.query().select(
        dong=count(), da_duyet=count(QEvent.reviewed_at),
        nhan_khac_nhau=count(QEvent.label, distinct=True)).all()
    assert rows == [{"dong": 6, "da_duyet": 1, "nhan_khac_nhau": 3}]


async def test_gop_sau_join_va_sap_theo_ham_gop(kho):
    events, _ = kho
    rows = await (events.query().join(QCamera).group_by(QCamera.zone)
                  .select(zone=QCamera.zone, so=count()).order_by_desc(count()).all())
    assert rows == [{"zone": "Tầng 1", "so": 4}, {"zone": "Tầng 2", "so": 2}]


async def test_count_tren_truy_van_gop_la_dem_SO_NHOM(kho):
    events, _ = kho
    q = events.query().group_by(QEvent.camera_id).select("camera_id", so=count())
    assert await q.count() == 2


async def test_group_by_khong_co_select_thi_bao_ro(kho):
    events, _ = kho
    with pytest.raises(BadRequestError) as loi:
        await events.query().group_by(QEvent.camera_id).all()
    assert "select" in str(loi.value).lower()


async def test_ham_gop_trong_select_phai_dat_ten(kho):
    events, _ = kho
    with pytest.raises(BadRequestError) as loi:
        events.query().group_by(QEvent.camera_id).select(count())
    assert "đặt tên" in str(loi.value)


# --------------------------------------------------------------- OR / NOT
async def test_or_where_mo_nhanh_moi_where_sau_do_lai_AND(kho):
    """`.where(a).where(b).or_where(c).where(d)` = `(a AND b) OR (c AND d)`."""
    events, _ = kho
    rows = await (
        events.query()
        .where(F(QEvent).label == "person")
        .where(F(QEvent).score >= 0.9)
        .or_where(F(QEvent).label == "fire")
        .where(F(QEvent).score >= 0.3)
        .all()
    )
    assert ids(rows) == ["e0", "e2", "e3", "e5"]


async def test_or_where_cho_ket_qua_y_het_or_long_and(kho):
    """Hai cách viết cùng một câu — nếu lệch thì một trong hai đang sai."""
    events, _ = kho
    noi_tiep = await (events.query()
                      .where(F(QEvent).label == "person").where(F(QEvent).score >= 0.9)
                      .or_where(F(QEvent).label == "fire").where(F(QEvent).score >= 0.3)
                      .all())
    long_nhau = await (events.query().where(
        or_(and_(F(QEvent).label == "person", F(QEvent).score >= 0.9),
            and_(F(QEvent).label == "fire", F(QEvent).score >= 0.3))).all())
    assert ids(noi_tiep) == ids(long_nhau)


async def test_or_where_khi_chua_co_where_nao_thi_chi_la_where(kho):
    events, _ = kho
    assert ids(await events.query().or_where(F(QEvent).label == "fire").all()) == ["e2"]


async def test_or_where_nhan_ca_kieu_ngan(kho):
    events, _ = kho
    rows = await events.query().where(label="fire").or_where(label="car").all()
    assert ids(rows) == ["e2", "e4"]


async def test_or_having_cung_luat_voi_or_where(kho):
    events, _ = kho
    rows = await (events.query().group_by(QEvent.camera_id).select("camera_id", so=count())
                  .having(count() > 3).or_having(count() == 2)
                  .order_by_asc("camera_id").all())
    assert rows == [{"camera_id": "c1", "so": 4}, {"camera_id": "c2", "so": 2}]


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_or_where_khong_lam_cau_SQL_thuong_moc_them_ngoac(tmp_path):
    """Một nhánh thì câu lệnh phải y như trước khi có or_where."""
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/nhanh.db"))
    await backend.startup()
    await backend.create_schema(QCamera, QEvent)
    events = Repository(QEvent, _Db(backend))

    mot = " ".join(events.query().where(label="fire").where(score__gte=0.3).sql().split())
    assert "WHERE qevents.label = 'fire' AND qevents.score >= 0.3" in mot

    hai = " ".join(events.query().where(label="fire").or_where(label="car").sql().split())
    assert "WHERE qevents.label = 'fire' OR qevents.label = 'car'" in hai
    await backend.shutdown()


async def test_or_long_trong_and_hai_tang(kho):
    """`(a AND b) OR (c AND d)` — hỏi nhiều nhất, và là chỗ dấu ngoặc dễ sai."""
    events, _ = kho
    mong_doi = ["e0", "e2", "e3", "e5"]

    bang_ham = await (events.query().where(
        or_(and_(F(QEvent).label == "person", F(QEvent).score >= 0.9),
            and_(F(QEvent).label == "fire", F(QEvent).score >= 0.3))).all())
    assert ids(bang_ham) == mong_doi

    bang_toan_tu = await (events.query().where(
        ((F(QEvent).label == "person") & (F(QEvent).score >= 0.9))
        | ((F(QEvent).label == "fire") & (F(QEvent).score >= 0.3))).all())
    assert ids(bang_toan_tu) == mong_doi


async def test_and_ngoai_or_trong_va_nguoc_lai(kho):
    """`x AND (a OR b)` khác hẳn `(x AND a) OR b` — cả hai phải viết được."""
    events, _ = kho
    a = await (events.query().where(F(QEvent).score >= 0.9)
               .where(or_(F(QEvent).label == "fire", F(QEvent).label == "person")).all())
    assert ids(a) == ["e0", "e3", "e5"]

    b = await (events.query().where(
        or_(and_(F(QEvent).score >= 0.9, F(QEvent).label == "person"),
            F(QEvent).label == "fire")).all())
    assert ids(b) == ["e0", "e2", "e3", "e5"]


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
    theo_diem = [r.id for r in await events.query().order_by_desc("score").limit(3).all()]
    assert theo_diem == ["e5", "e0", "e3"] or theo_diem == ["e5", "e3", "e0"]

    trang2 = [r.id for r in await events.query().order_by_asc("created_at").offset(2).limit(2).all()]
    assert trang2 == ["e2", "e3"]


async def test_count_first_exists_one(kho):
    events, _ = kho
    assert await events.query().where(score__gte=0.9).count() == 3
    assert await events.query().where(label="khong-co").exists() is False
    assert await events.query().where(label="fire").exists() is True
    assert (await events.query().order_by_desc("score").first()).id == "e5"
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


async def test_khong_co_khoa_ngoai_thi_bao_chu_khong_doan(kho):
    """`.join(X)` mà giữa hai bảng chưa khai `reference` — phải nói rõ thiếu gì."""
    events, _ = kho

    @entity()
    @dataclass(slots=True)
    class QLa:
        id: str

    with pytest.raises(BadRequestError) as loi:
        events.query().join(QLa)
    assert "chưa" in str(loi.value) and "reference" in str(loi.value)


async def test_hai_khoa_ngoai_cung_bang_thi_bat_chon(kho):
    """Hai cột cùng trỏ sang QCamera thì không đoán bừa, bắt nói rõ."""
    _, cameras = kho

    @entity()
    @dataclass(slots=True)
    class QCap:
        id: str
        vao_id: str = field(metadata=reference(QCamera))
        ra_id: str = field(metadata=reference(QCamera))

    with pytest.raises(BadRequestError) as loi:
        cameras.query().join(QCap)
    assert "2 khoá ngoại" in str(loi.value)
    assert "QCap.vao_id" in str(loi.value) and "QCap.ra_id" in str(loi.value)


async def test_on_khong_phai_cot_thi_bao_ro(kho):
    """Entity không khai `slots=True` thì `X.truong` là giá trị mặc định, không phải cột."""
    events, _ = kho
    with pytest.raises(BadRequestError) as loi:
        events.query().join(QCamera, on=None)
    assert "slots=True" in str(loi.value)


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
        .order_by_desc("created_at")
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
    # QEvent.camera_id là khoá ngoại thật, phải có camera trước mới ghi được sự kiện.
    await Repository(QCamera, _Db(backend)).save(QCamera(id="c1", name="Cổng", zone="Tầng 1"))
    for i in range(50):
        await events.save(QEvent(id=f"x{i}", camera_id="c1", label="person", score=i / 100))

    sql = events.query().where(score__gte=0.1).limit(5).sql()
    assert "LIMIT 5" in " ".join(sql.split()), "LIMIT phải nằm trong câu SQL"
    assert len(await events.query().where(score__gte=0.1).limit(5).all()) == 5
    await backend.shutdown()
