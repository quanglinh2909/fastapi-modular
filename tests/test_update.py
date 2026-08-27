"""`repo.update(...)` — sửa thẳng dưới database, không đọc bản ghi về trước.

Thay cho vòng ba bước `get` -> sửa -> `save`: một lượt đi database thay vì hai,
và không có khe hở giữa lúc đọc và lúc ghi để ai đó chen vào.

Mọi phép kiểm chạy trên **cả ba backend**. Đây là chỗ dễ lệch nhất, và đã lệch
thật trong lúc viết: backend `memory` sửa thẳng vào object đang giữ nên cột
`Enum` còn nguyên chuỗi thô, trong khi SQL/Mongo đọc lên vẫn ra `Enum` —
`r.status.value` chạy ở hai chỗ kia và nổ ở đây.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pytest
from pydantic import BaseModel

from fastapi_modular import Entity, entity
from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.schemas import partial_of
from fastapi_modular.infrastructure.database import Repository, reference
from fastapi_modular.infrastructure.database.factory import create_backend

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None
MONGO_DSN = os.getenv("TEST_MONGO_DSN", "")
CO_MONGO = bool(MONGO_DSN) and importlib.util.find_spec("motor") is not None


class UpStatus(Enum):
    ON = "online"
    OFF = "offline"


@entity(name="up_zones", unique=["code"])
@dataclass(slots=True)
class UpZone(Entity):
    id: str
    code: str = ""


@entity(name="up_cameras")
@dataclass(slots=True)
class UpCamera(Entity):
    id: str
    name: str = ""
    zone: str = ""
    status: UpStatus = UpStatus.OFF
    threshold: float = 0.5
    zone_id: str | None = field(default=None, metadata=reference(UpZone, on_delete="SET NULL"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


class UpCameraBase(BaseModel):
    name: str
    zone: str
    threshold: float


class UpCameraUpdate(partial_of(UpCameraBase)):
    """DTO PATCH: mọi field optional, đúng thứ `fam module` sinh ra."""


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
        settings = DatabaseSettings(
            driver="mongodb", dsn=MONGO_DSN, name=f"fam_up_{uuid.uuid4().hex[:8]}"
        )
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(UpZone, UpCamera)

    db = _Db(backend)
    zones, cameras = Repository(UpZone, db), Repository(UpCamera, db)
    await zones.save(UpZone(id="z1", code="A"))
    await zones.save(UpZone(id="z2", code="B"))
    await cameras.save(UpCamera(id="c1", name="Cổng", zone="T1", zone_id="z1"))
    await cameras.save(UpCamera(id="c2", name="Kho", zone="T1"))
    await cameras.save(UpCamera(id="c3", name="Bãi", zone="T2", status=UpStatus.ON))

    yield cameras, zones, backend
    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    await backend.shutdown()


# ------------------------------------------------------------------ theo id
async def test_update_theo_id(kho):
    cameras, _, _ = kho
    assert await cameras.update("c1", name="Cổng chính") == 1
    assert (await cameras.get("c1")).name == "Cổng chính"
    assert (await cameras.get("c2")).name == "Kho", "dòng khác không được đụng"


async def test_gia_tri_truyen_bang_dict_hay_kwargs_deu_duoc(kho):
    cameras, _, _ = kho
    await cameras.update("c1", {"name": "A"})
    assert (await cameras.get("c1")).name == "A"

    await cameras.update("c1", {"name": "B"}, threshold=0.9)
    row = await cameras.get("c1")
    assert (row.name, row.threshold) == ("B", 0.9), "dict và kwargs gộp lại"


async def test_id_khong_ton_tai_thi_tra_ve_0(kho):
    cameras, _, _ = kho
    assert await cameras.update("khong-co", name="x") == 0


# --------------------------------------------------- theo cột khác, nhiều dòng
async def test_update_nhieu_dong_theo_cot_khac(kho):
    """Chính là việc người dùng cần: một câu lệnh sửa mọi dòng khớp điều kiện."""
    cameras, _, _ = kho
    assert await cameras.update({"zone": "T1"}, status=UpStatus.ON) == 2

    assert [r.status for r in await cameras.find(zone="T1")] == [UpStatus.ON] * 2
    assert (await cameras.get("c3")).zone == "T2", "vùng khác không bị đụng"


async def test_dieu_kien_nhieu_cot_la_AND(kho):
    cameras, _, _ = kho
    assert await cameras.update({"zone": "T1", "name": "Kho"}, threshold=0.7) == 1
    assert (await cameras.get("c2")).threshold == 0.7
    assert (await cameras.get("c1")).threshold == 0.5


async def test_khong_khop_dong_nao_thi_tra_ve_0(kho):
    cameras, _, _ = kho
    assert await cameras.update({"zone": "KHONG-CO"}, name="x") == 0


async def test_ghi_dung_gia_tri_dang_co_van_dem_la_mot_dong(kho):
    """Chỗ Mongo đếm khác SQL, và nó lặng lẽ.

    `update_many` trả `modified_count` = 0 khi giá trị mới trùng giá trị cũ,
    trong khi SQL vẫn đếm dòng đã KHỚP. Lấy `matched_count` để ba backend cho
    cùng con số — nếu không, "sửa 0 dòng" sẽ bị đọc thành "không tìm thấy".
    """
    cameras, _, _ = kho
    row = await cameras.get("c2")
    assert row.name == "Kho"

    # Phải ghim CẢ `updated_at`, nếu không dấu thời gian mới làm document đổi
    # thật và `modified_count` cũng thành 1 — phép kiểm sẽ xanh dù code sai.
    # Đo trên Mongo thật: matched=1, modified=0 khi không có gì đổi.
    assert await cameras.update("c2", name="Kho", updated_at=row.updated_at) == 1


# ----------------------------------------------------------------- kiểu dữ liệu
async def test_cot_Enum_doc_len_van_la_Enum(kho):
    """Backend `memory` sửa thẳng vào object, nên phải tự ép về kiểu đã khai."""
    cameras, _, _ = kho
    await cameras.update("c1", status=UpStatus.ON)
    assert (await cameras.get("c1")).status is UpStatus.ON


async def test_ghi_Enum_bang_chuoi_gia_tri_cung_duoc(kho):
    cameras, _, _ = kho
    await cameras.update("c1", status="online")
    assert (await cameras.get("c1")).status is UpStatus.ON


async def test_ghi_None_vao_cot_cho_phep_trong(kho):
    cameras, _, _ = kho
    await cameras.update("c1", zone_id=None)
    assert (await cameras.get("c1")).zone_id is None


# ---------------------------------------------------------------- truyền DTO
async def test_truyen_thang_DTO(kho):
    """Không phải `payload.model_dump()` nữa — DTO đi thẳng vào."""
    cameras, _, _ = kho
    assert await cameras.update("c1", UpCameraUpdate(name="Cổng chính")) == 1
    assert (await cameras.get("c1")).name == "Cổng chính"


async def test_DTO_chi_ghi_field_client_THUC_SU_gui(kho):
    """Cái bẫy đắt nhất của PATCH, và là lý do dùng `exclude_unset`.

    `model_dump()` trần trả về CẢ field client không gửi (giá trị `None` mặc
    định của `partial_of`), nên PATCH đổi mỗi `name` sẽ ghi `None` đè lên
    `zone` và `threshold` — mất dữ liệu, không ai báo.
    """
    cameras, _, _ = kho
    truoc = await cameras.get("c1")

    await cameras.update("c1", UpCameraUpdate(name="Chỉ đổi tên"))

    sau = await cameras.get("c1")
    assert sau.name == "Chỉ đổi tên"
    assert sau.zone == truoc.zone, "field không gửi phải CÒN NGUYÊN"
    assert sau.threshold == truoc.threshold


async def test_DTO_gui_None_tuong_minh_thi_van_xoa_duoc_cot(kho):
    """`exclude_unset` phân biệt "không gửi" với "gửi = null"."""
    cameras, _, _ = kho
    assert (await cameras.get("c1")).zone_id == "z1"

    await cameras.update("c1", UpCameraUpdate(), zone_id=None)

    assert (await cameras.get("c1")).zone_id is None


async def test_DTO_gop_duoc_voi_kwargs(kho):
    cameras, _, _ = kho
    await cameras.update("c1", UpCameraUpdate(name="A"), zone="T9")
    row = await cameras.get("c1")
    assert (row.name, row.zone) == ("A", "T9")


async def test_where_cung_nhan_DTO(kho):
    """Hợp với bộ lọc sinh bằng `partial_of(...)`."""
    cameras, _, _ = kho
    assert await cameras.update(UpCameraUpdate(zone="T1"), status=UpStatus.ON) == 2


async def test_where_la_DTO_bo_qua_field_khong_gui_KE_CA_khi_mac_dinh_khac_None(kho):
    """`dict(model)` không thay được `model_dump(exclude_unset=True)`.

    Hai cách chỉ trùng nhau khi mặc định là `None` — vì `active_filters` vốn đã
    bỏ giá trị `None`. Mặc định khác `None` thì `dict(model)` biến nó thành một
    điều kiện lọc mà client không hề gửi, và câu lệnh khớp 0 dòng.
    """

    class BoLoc(BaseModel):
        zone: str = ""
        name: str = "KHÔNG-AI-TÊN-THẾ"

    cameras, _, _ = kho

    assert await cameras.update(BoLoc(zone="T1"), threshold=0.8) == 2


async def test_DTO_rong_bi_chan_va_noi_ro_vi_sao(kho):
    """PATCH với body rỗng: chặn, và nói rõ là `exclude_unset` bỏ hết."""
    cameras, _, _ = kho
    with pytest.raises(BadRequestError, match="exclude_unset"):
        await cameras.update("c1", UpCameraUpdate())


async def test_truyen_entity_thi_chi_sang_save(kho):
    """Đã có sẵn cả bản ghi thì `save(obj)` mới là đường đúng."""
    cameras, _, _ = kho
    row = await cameras.get("c1")
    with pytest.raises(BadRequestError, match="save"):
        await cameras.update("c1", row)


# ------------------------------------------------------------------ updated_at
async def test_updated_at_tu_dong_dau(kho):
    cameras, _, _ = kho
    truoc = (await cameras.get("c1")).updated_at

    await cameras.update("c1", name="Mới")

    assert (await cameras.get("c1")).updated_at > truoc


async def test_tu_dat_updated_at_thi_ton_trong(kho):
    cameras, _, _ = kho
    moc = datetime(2020, 1, 2, 3, 4, 5, tzinfo=utcnow().tzinfo)

    await cameras.update("c1", name="Mới", updated_at=moc)

    assert (await cameras.get("c1")).updated_at == moc


# ------------------------------------------------------------------ chặn sai
async def test_cot_khong_co_that_bi_chan(kho):
    """Gõ sai tên cột mà im lặng bỏ qua thì câu lệnh báo "đã sửa" và không sửa gì."""
    cameras, _, _ = kho
    with pytest.raises(BadRequestError, match="không có trường"):
        await cameras.update("c1", ten_sai=1)


async def test_doi_id_bi_chan(kho):
    cameras, _, _ = kho
    with pytest.raises(BadRequestError, match="id"):
        await cameras.update("c1", id="khac")


async def test_dieu_kien_rong_bi_chan(kho):
    """`where` rỗng gần như luôn là lỗi lập trình, không phải ý định sửa cả bảng."""
    cameras, _, _ = kho
    with pytest.raises(BadRequestError, match="sẽ sửa MỌI dòng"):
        await cameras.update({}, name="x")

    assert (await cameras.get("c1")).name == "Cổng", "không được sửa gì cả"


async def test_khong_co_gia_tri_nao_bi_chan(kho):
    """`updated_at` tự thêm không được che mất lỗi "quên truyền giá trị"."""
    cameras, _, _ = kho
    with pytest.raises(BadRequestError, match="không có giá trị"):
        await cameras.update("c1")


async def test_co_y_sua_ca_bang_thi_noi_ro_bang_match(kho):
    cameras, _, _ = kho
    assert await cameras.update({}, zone="X", match=lambda _: True) == 3
    assert {r.zone for r in await cameras.find()} == {"X"}


async def test_gia_tri_mang_toan_tu_bi_chan(kho):
    """Cùng luật với `find`: `{"$ne": ...}` là toán tử của Mongo, không phải giá trị."""
    cameras, _, _ = kho
    with pytest.raises(BadRequestError):
        await cameras.update("c1", name={"$ne": ""})


# ------------------------------------------------------------ ràng buộc dữ liệu
async def test_khoa_ngoai_tro_toi_cha_khong_ton_tai_bi_chan(kho):
    """SQL áp ràng buộc cho cả UPDATE, không riêng INSERT — hai backend kia phải theo."""
    cameras, _, _ = kho
    with pytest.raises(Exception) as loi:
        await cameras.update("c1", zone_id="khong-co-that")
    assert "khong-co-that" in str(loi.value) or "FOREIGN KEY" in str(loi.value)


async def test_khoa_ngoai_hop_le_thi_doi_duoc(kho):
    cameras, _, _ = kho
    assert await cameras.update("c1", zone_id="z2") == 1
    assert (await cameras.get("c1")).zone_id == "z2"


async def test_update_lam_trung_cot_unique_bi_chan(kho):
    _, zones, _ = kho
    with pytest.raises(Exception):  # noqa: B017 - mỗi backend một lớp lỗi, đều thành 409
        await zones.update("z2", code="A")


# ------------------------------------------------------------------ transaction
@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_update_trong_transaction_huy_duoc(tmp_path):
    """Sửa hàng loạt rồi đổi ý: rollback phải trả về nguyên trạng."""
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/tx.db"))
    await backend.startup()
    await backend.create_schema(UpZone, UpCamera)
    cameras = Repository(UpCamera, _Db(backend))
    await cameras.save(UpCamera(id="c1", name="Cổng", zone="T1"))

    async with backend.transaction() as tx:
        await cameras.update({"zone": "T1"}, name="ĐỔI RỒI")
        assert (await cameras.get("c1")).name == "ĐỔI RỒI", "trong khối thì thấy"
        await tx.rollback()

    assert (await cameras.get("c1")).name == "Cổng"
    await backend.shutdown()
