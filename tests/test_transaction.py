"""Test transaction: ghi nhiều bảng thì cùng thành công hoặc cùng không.

Chạy trên **cả hai backend**: `memory` (mặc định của `fam test`) và `sqlite`.
Memory không có transaction thật — nó chụp ảnh dữ liệu rồi trả lại khi hỏng.
Hai cột kết quả phải khớp nhau, nếu không thì `fam test` đang nói dối.

Bản sqlite cần `TEST_SQLITE=1`.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field

import pytest

from fastapi_modular import Entity, controller, entity, get, injectable, post, reference
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.infrastructure.database import Repository
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.repository import Database

CO_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None


@entity()
@dataclass(slots=True)
class TxCamera(Entity):
    id: str
    name: str


@entity()
@dataclass(slots=True)
class TxLog(Entity):
    id: str
    message: str
    camera_id: str = field(metadata=reference(TxCamera, on_delete="CASCADE"))


class _Db(Database):
    """Database thật, backend dựng sẵn — khỏi cần cả bộ Settings."""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._settings = None


@pytest.fixture(params=["memory", pytest.param("sqlite", marks=pytest.mark.skipif(
    not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite"))])
async def kho(request, tmp_path):
    """(db, repo_camera, repo_log) trên backend đang thử."""
    if request.param == "memory":
        settings = DatabaseSettings(driver="memory")
    else:
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db"
        )
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(TxCamera, TxLog)

    db = _Db(backend)
    yield db, Repository(TxCamera, db), Repository(TxLog, db)
    await backend.shutdown()


async def ids(repo) -> list[str]:
    return sorted(o.id for o in await repo.query().all())


async def test_hai_bang_cung_thanh_cong(kho):
    db, cameras, logs = kho
    async with db.transaction():
        await cameras.save(TxCamera(id="c1", name="Cổng"))
        await logs.save(TxLog(id="l1", message="mở", camera_id="c1"))

    assert await ids(cameras) == ["c1"]
    assert await ids(logs) == ["l1"]


async def test_bang_thu_hai_hong_thi_bang_thu_nhat_bi_huy(kho):
    """Đây là cả lý do có transaction: không để lại camera mồ côi."""
    db, cameras, logs = kho
    with pytest.raises(Exception):  # noqa: B017 - kiểu lỗi khác nhau theo backend
        async with db.transaction():
            await cameras.save(TxCamera(id="c2", name="Kho"))
            await logs.save(TxLog(id="l2", message="x", camera_id="KHONG-TON-TAI"))

    assert await ids(cameras) == [], "camera phải bị huỷ theo"
    assert await ids(logs) == []


async def test_khong_bọc_transaction_thi_ghi_nua_voi(kho):
    """Chứng minh vì sao cần bọc: không bọc thì bản ghi đầu ở lại."""
    _, cameras, logs = kho
    with pytest.raises(Exception):  # noqa: B017
        await cameras.save(TxCamera(id="c3", name="Bãi"))
        await logs.save(TxLog(id="l3", message="x", camera_id="KHONG-TON-TAI"))

    assert await ids(cameras) == ["c3"], "không bọc thì nó ở lại — đúng như mong đợi"


async def test_long_nhau_khoi_trong_huy_rieng_phan_cua_no(kho):
    """SAVEPOINT: khối trong hỏng thì khối ngoài vẫn giữ nguyên phần của mình."""
    db, cameras, _ = kho
    async with db.transaction():
        await cameras.save(TxCamera(id="c4", name="Ngoài"))
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await cameras.save(TxCamera(id="c5", name="Trong"))
                raise RuntimeError("hỏng khối trong")

    assert await ids(cameras) == ["c4"]


async def test_doc_duoc_du_lieu_chua_commit_trong_cung_khoi(kho):
    """Trong khối, mọi thao tác đi chung một connection nên thấy nhau."""
    db, cameras, _ = kho
    async with db.transaction():
        await cameras.save(TxCamera(id="c6", name="Tạm"))
        assert (await cameras.get("c6")).name == "Tạm"


async def test_sua_thang_vao_object_roi_hong_cung_phai_tra_lai(kho):
    """Sửa entity tại chỗ rồi `save` — hỏng thì giá trị CŨ phải quay lại.

    Bẫy riêng của backend memory: nếu chỉ chụp ảnh cái dict mà không sao chép
    từng bản ghi thì ảnh chụp trỏ vào chính object vừa bị sửa, khôi phục xong
    vẫn mang giá trị mới. SQL không có bẫy này vì nó đọc lại từ bảng.
    """
    db, cameras, _ = kho
    await cameras.save(TxCamera(id="c8", name="Tên cũ"))

    with pytest.raises(RuntimeError):
        async with db.transaction():
            cam = await cameras.get("c8")
            cam.name = "Tên mới"
            await cameras.save(cam)
            raise RuntimeError("hỏng")

    assert (await cameras.get("c8")).name == "Tên cũ"


async def test_tx_rollback_huy_ma_khong_nem_loi(kho):
    """`await tx.rollback()` — thoát khối tại chỗ, không ném gì ra ngoài."""
    db, cameras, _ = kho
    da_chay_tiep = False

    async with db.transaction() as tx:
        await cameras.save(TxCamera(id="c9", name="Sẽ bị huỷ"))
        await tx.rollback()
        da_chay_tiep = True                      # không được chạy

    assert await ids(cameras) == []
    assert da_chay_tiep is False, "sau `tx.rollback()` thì phần còn lại của khối phải dừng"
    assert tx.rolled_back is True


async def test_tx_rollback_o_khoi_trong_khong_giet_khoi_ngoai(kho):
    db, cameras, _ = kho
    async with db.transaction():
        await cameras.save(TxCamera(id="c10", name="Ngoài"))
        async with db.transaction() as tx:
            await cameras.save(TxCamera(id="c11", name="Trong"))
            await tx.rollback()
        await cameras.save(TxCamera(id="c12", name="Sau đó"))

    assert await ids(cameras) == ["c10", "c12"]


async def test_loi_khong_bi_nuot(kho):
    """Rollback xong phải ném tiếp, không được biến lỗi thành im lặng."""
    db, cameras, _ = kho
    with pytest.raises(ValueError, match="tự ném"):
        async with db.transaction():
            await cameras.save(TxCamera(id="c7", name="X"))
            raise ValueError("tự ném")
    assert await ids(cameras) == []


async def test_hai_task_dong_thoi_khong_nuot_du_lieu_cua_nhau(kho):
    """Task A rollback thì KHÔNG được xoá thứ task B đã commit song song.

    Chỗ này backend memory từng hỏng: ảnh chụp là của CẢ kho, A rollback trả
    kho về trước lúc A vào — cuốn theo luôn bản ghi B vừa commit. Đo được:
    memory mất "t1" trong khi sqlite giữ. Giờ transaction memory xếp hàng qua
    một khoá nên hai backend cho cùng kết quả.
    """
    import asyncio

    db, cameras, _ = kho

    async def ghi(i: int) -> None:
        async with db.transaction() as tx:
            await cameras.save(TxCamera(id=f"t{i}", name=f"task {i}"))
            await asyncio.sleep(0.02)      # đủ để hai task gối lên nhau nếu không có khoá
            if i == 0:
                await tx.rollback()

    await asyncio.gather(ghi(0), ghi(1))

    assert await cameras.get("t0") is None, "task 0 đã rollback"
    assert await cameras.get("t1") is not None, "commit của task 1 phải còn"


# ------------------------------------------------ transaction của cả request
@injectable
class TxService:
    def __init__(self, cams: Repository[TxCamera], logs: Repository[TxLog]) -> None:
        self._cams, self._logs = cams, logs

    async def ghi_roi_hong(self) -> None:
        await self._cams.save(TxCamera(id="c1", name="Cổng"))
        await self._logs.save(TxLog(id="l1", message="mở", camera_id="c1"))
        raise RuntimeError("hỏng sau khi đã ghi")

    async def dem(self) -> dict:
        return {"cam": len(await self._cams.query().all()),
                "log": len(await self._logs.query().all())}


@controller(prefix="/tx")
class TxController:
    def __init__(self, svc: TxService) -> None:
        self._svc = svc

    @post("/hong")
    async def hong(self) -> dict:
        await self._svc.ghi_roi_hong()
        return {}

    @get("/dem")
    async def dem(self) -> dict:
        return await self._svc.dem()


def _client(driver: str, tmp_path):
    from fastapi.testclient import TestClient

    from fastapi_modular.core.config import (
        KafkaSettings,
        MqttSettings,
        RabbitSettings,
        RedisSettings,
        Settings,
        WebSocketSettings,
    )
    from fastapi_modular.core.controller import build_router
    from fastapi_modular.factory import create_app

    db = (DatabaseSettings(driver="memory") if driver == "memory"
          else DatabaseSettings(driver="sqlite",
                                dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db"))
    settings = Settings(
        APP_ENV="local", APP_DEBUG=True, APP_DB=db,
        APP_RABBITMQ=RabbitSettings(enabled=False), APP_REDIS=RedisSettings(enabled=False),
        APP_MQTT=MqttSettings(enabled=False), APP_KAFKA=KafkaSettings(enabled=False),
        APP_WS=WebSocketSettings(adapter="local"),
    )
    app = create_app(settings, package="tests")
    app.include_router(build_router(TxController), prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("driver", ["memory", pytest.param("sqlite", marks=pytest.mark.skipif(
    not CO_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite"))])
def test_handler_nem_loi_thi_ca_request_bi_huy(tmp_path, driver):
    """Không cần `transaction()` trong HTTP handler — cả request đã là một.

    Backend memory phải cho CÙNG kết quả, nếu không thì `fam test` xanh trong
    khi production rollback: người ta sẽ viết test sai rồi tin vào nó.
    """
    with _client(driver, tmp_path) as client:
        client.post("/api/tx/hong")
        assert client.get("/api/tx/dem").json() == {"cam": 0, "log": 0}
