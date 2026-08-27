"""Chống injection: giá trị và tên cột do người dùng gửi lên.

Ba backend phải từ chối GIỐNG HỆT nhau. Trước khi có bộ chặn này, đo được:

| Ca | memory | sqlite | mongodb |
|---|---|---|---|
| `token={"$ne": ""}` | không khớp | lỗi driver khó hiểu | **qua được cửa đăng nhập** |
| khoá `$where` | không khớp | **trả về TOÀN BỘ bảng** | **chạy JavaScript trên server** |
| khoá không có thật | không khớp | **trả về TOÀN BỘ bảng** | không khớp |

Hai ô in đậm ở cột sqlite là bộ lọc BIẾN MẤT — nguy hiểm không kém injection.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass

import pytest

from fastapi_modular import Entity, entity
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.infrastructure.database import F, Repository, like
from fastapi_modular.infrastructure.database.factory import create_backend

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None
MONGO_DSN = os.getenv("TEST_MONGO_DSN", "")
CO_MONGO = bool(MONGO_DSN) and importlib.util.find_spec("motor") is not None

DOC = "'; DROP TABLE injs; --"


@entity(name="injs")
@dataclass(slots=True)
class Inj(Entity):
    id: str
    name: str
    token: str = "bi-mat"
    score: float = 0.0


class _Db:
    def __init__(self, backend) -> None:
        self.backend, self.driver = backend, backend.name


@pytest.fixture(params=[
    "memory",
    pytest.param("sqlite", marks=pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1")),
    pytest.param("mongodb", marks=pytest.mark.skipif(not CO_MONGO, reason="đặt TEST_MONGO_DSN")),
])
async def kho(request, tmp_path):
    if request.param == "memory":
        settings = DatabaseSettings(driver="memory")
    elif request.param == "sqlite":
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db")
    else:
        settings = DatabaseSettings(driver="mongodb", dsn=MONGO_DSN,
                                    name=f"fam_inj_{uuid.uuid4().hex[:8]}")
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(Inj)

    repo = Repository(Inj, _Db(backend))
    await repo.save(Inj(id="1", name="an", token="tok-an", score=1))
    await repo.save(Inj(id="2", name=DOC, token="tok-binh", score=2))
    yield repo
    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    await backend.shutdown()


# --------------------------------------------------- giá trị: phải là DỮ LIỆU
async def test_chuoi_sql_doc_hai_chi_la_mot_gia_tri(kho):
    """Chuỗi `'; DROP TABLE ...` được so bằng như mọi chuỗi khác, không chạy."""
    assert [r.id for r in await kho.query().where(Inj.name == DOC).all()] == ["2"]
    assert [r.id for r in await kho.query().where(name=DOC).all()] == ["2"]
    assert [r.id for r in await kho.query().in_(Inj.name, [DOC]).all()] == ["2"]
    assert [r.id for r in await kho.find(name=DOC)] == ["2"]
    assert await kho.query().count() == 2, "bảng còn nguyên"


async def test_gia_tri_mang_toan_tu_bi_tu_choi(kho):
    """`{"$ne": ""}` từ JSON người dùng: trên Mongo nó QUA được cửa đăng nhập."""
    doc = {"$ne": ""}
    for goi in (
        lambda: kho.find(name="an", token=doc),
        lambda: kho.query().where(name="an", token=doc).all(),
        lambda: kho.query().where(Inj.token == doc).all(),
        lambda: kho.query().in_(Inj.token, [doc]).all(),
    ):
        with pytest.raises(BadRequestError) as loi:
            await goi()
        assert "toán tử" in str(loi.value)


async def test_dang_nhap_dung_van_chay(kho):
    assert len(await kho.find(name="an", token="tok-an")) == 1
    assert len(await kho.find(name="an", token="sai")) == 0


# ------------------------------------------- tên cột: phải là cột CÓ THẬT
async def test_khoa_la_bi_tu_choi_chu_khong_bi_bo_qua(kho):
    """Bỏ qua âm thầm nghĩa là bộ lọc biến mất — trả về cả bảng."""
    for xau in ("$where", "khong_co_truong_nay", "name; DROP TABLE injs; --"):
        with pytest.raises(BadRequestError) as loi:
            await kho.find(**{xau: 1})
        assert "không có trường" in str(loi.value)
        assert "Có:" in str(loi.value), "phải liệt kê tên đúng"


async def test_ten_cot_la_trong_builder_bi_tu_choi(kho):
    doc_hai = "name FROM injs; DROP TABLE injs; --"
    for goi in (
        lambda: kho.query().select(doc_hai),
        lambda: kho.query().order_by_asc(doc_hai),
        lambda: kho.query().group_by(doc_hai),
        lambda: kho.query().where(**{f"{doc_hai}": 1}),
        lambda: F(Inj).__getattr__(doc_hai),
    ):
        with pytest.raises(BadRequestError):
            goi()


async def test_toan_tu_la_bi_tu_choi(kho):
    with pytest.raises(BadRequestError) as loi:
        kho.query().where(**{"score__gte; DROP TABLE injs": 1})
    assert "toán tử" in str(loi.value)


async def test_mau_LIKE_chi_la_du_lieu(kho):
    """Ký tự đặc biệt của regex/SQL trong mẫu LIKE không được thoát ra ngoài."""
    assert await kho.query().where(like(Inj.name, "%' OR '1'='1")).all() == []
    assert await kho.query().count() == 2


# --------------------------------------------------- riêng SQL: tham số buộc
@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_gia_tri_di_xuong_driver_duoi_dang_THAM_SO(tmp_path):
    """Không phải nhìn `.sql()` mà đoán: soi đúng câu gửi cho driver."""
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/tham_so.db"))
    await backend.startup()
    await backend.create_schema(Inj)
    repo = Repository(Inj, _Db(backend))

    spec = repo.query().where(Inj.name == DOC)._spec
    bien = backend._compile(spec).compile(backend._engine)

    assert "?" in str(bien), f"giá trị phải là placeholder, không phải nhúng thẳng: {bien}"
    assert DOC in bien.params.values(), "chuỗi độc hại nằm ở THAM SỐ"
    assert DOC not in str(bien), "và không nằm trong câu lệnh"
    await backend.shutdown()


@pytest.mark.skipif(not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")
async def test_alias_bang_duoc_dat_trong_ngoac_kep(tmp_path):
    backend = create_backend(DatabaseSettings(
        driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/alias.db"))
    await backend.startup()
    await backend.create_schema(Inj)
    repo = Repository(Inj, _Db(backend))

    sql = repo.query().join(Inj, on=Inj.id, alias='x" ; DROP TABLE injs; --').sql()
    assert 'AS "x"" ; DROP TABLE injs; --"' in sql, "dấu nháy phải được nhân đôi"
    await backend.shutdown()
