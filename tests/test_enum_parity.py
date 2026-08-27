"""Enum trong điều kiện lọc — ba backend phải hành xử giống hệt nhau.

Vì sao có file này: cột Enum được LƯU bằng `.value`, nhưng ba backend từng
NHẬN giá trị lọc ba kiểu khác nhau. Đo được với Enum THƯỜNG (không phải
StrEnum): `find(kind=Kind.B)` chạy trên memory nhưng nổ trên sqlite
("type 'Kind' is not supported") lẫn mongo ("cannot encode object") — đúng
kiểu lệch tệ nhất: `fam test` xanh, production đổ. Chiều ngược lại cũng lệch:
lọc bằng chuỗi `"dac_biet"` thì SQL khớp còn memory trượt.

Giờ mọi giá trị so sánh đi qua `bind_value` (Enum -> `.value`) ở cả ba đường:
filter của `find`, điều kiện của builder, và khoá sắp xếp.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass
from enum import Enum

import pytest

from fastapi_modular import Entity, entity
from fastapi_modular.core.compat import StrEnum
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.infrastructure.database import Repository, in_
from fastapi_modular.infrastructure.database.factory import create_backend

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None
MONGO_DSN = os.getenv("TEST_MONGO_DSN", "")
CO_MONGO = bool(MONGO_DSN) and importlib.util.find_spec("motor") is not None


class EpStatus(StrEnum):
    ON = "online"
    OFF = "offline"


class EpKind(Enum):
    """Enum THƯỜNG — chính là loại từng làm ba backend lệch nhau."""

    NORMAL = "thuong"
    SPECIAL = "dac_biet"


@entity()
@dataclass(slots=True)
class EpCamera(Entity):
    id: str
    name: str = ""
    status: EpStatus = EpStatus.OFF
    kind: EpKind = EpKind.NORMAL


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
            driver="mongodb", dsn=MONGO_DSN, name=f"fam_ep_{uuid.uuid4().hex[:8]}"
        )
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(EpCamera)

    cameras = Repository(EpCamera, _Db(backend))
    await cameras.save(EpCamera(id="c1", name="A", status=EpStatus.ON, kind=EpKind.NORMAL))
    await cameras.save(EpCamera(id="c2", name="B", status=EpStatus.OFF, kind=EpKind.SPECIAL))
    yield cameras
    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    await backend.shutdown()


def ids(rows) -> list[str]:
    return sorted(r.id if hasattr(r, "id") else r["id"] for r in rows)


async def test_find_loc_bang_Enum_thuong(kho):
    assert ids(await kho.find(kind=EpKind.SPECIAL)) == ["c2"]


async def test_find_loc_bang_chuoi_gia_tri(kho):
    """Client gửi JSON thì service nhận CHUỖI — cũng phải khớp, cả ba backend."""
    assert ids(await kho.find(kind="dac_biet")) == ["c2"]
    assert ids(await kho.find(status="online")) == ["c1"]


async def test_builder_where_bang_Enum_thuong(kho):
    assert ids(await kho.query().where(EpCamera.kind == EpKind.SPECIAL).all()) == ["c2"]
    assert ids(await kho.query().where(EpCamera.kind != EpKind.SPECIAL).all()) == ["c1"]


async def test_builder_in_voi_danh_sach_Enum(kho):
    rows = await kho.query().where(in_(EpCamera.kind, [EpKind.NORMAL, EpKind.SPECIAL])).all()
    assert ids(rows) == ["c1", "c2"]


async def test_order_theo_cot_Enum_thuong(kho):
    """Enum thường không so `<` với nhau được — khoá sắp phải quy về `.value`."""
    rows = await kho.query().order_by_asc(EpCamera.kind).all()
    assert [r.kind for r in rows] == [EpKind.SPECIAL, EpKind.NORMAL]  # "dac_biet" < "thuong"
