"""Worker ghi database thì dữ liệu phải THẬT SỰ tới đĩa.

Vì sao có file này. `contextvars` được sao chép khi tạo Task/Thread, nên một
`@worker` sinh ra từ trong một HTTP request — hoặc từ `@interval`/`@job`, vốn
cũng mở request scope — thừa hưởng đúng cái store của request đó. Mà
`SqlUnitOfWork` là provider request-scoped: nó mở một transaction rồi chỉ commit
ở `on_request_end`. Worker sống lâu hơn request, nên transaction ấy KHÔNG BAO
GIỜ được commit.

Kiểu hỏng này im lặng đến khó chịu, và người dùng thật đã mất buổi vì nó:

    print("Deleted device offline:", await repo.delete(row.id))
    # -> True

`True` là đúng: DELETE khớp một dòng. Câu SELECT ngay sau cũng thấy dữ liệu mới,
vì cùng một connection. Chỉ có điều trên đĩa không có gì đổi, và tắt app là mất
sạch.

Nên mọi phép kiểm ở đây đọc bằng **một kết nối sqlite KHÁC** (`tren_dia`), chứ
không hỏi lại chính repository — hỏi lại repository là hỏi đúng cái connection
đang giữ transaction chưa commit, và nó sẽ trả lời "xong rồi" trong mọi trường
hợp.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from fastapi_modular import Entity, entity, injectable
from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import (
    DatabaseSettings,
    KafkaSettings,
    MqttSettings,
    RabbitSettings,
    RedisSettings,
    Settings,
    WebSocketSettings,
)
from fastapi_modular.core.controller import build_router, controller, post
from fastapi_modular.core.workers import WorkerContext, worker
from fastapi_modular.factory import create_app
from fastapi_modular.infrastructure.database import Repository
from fastapi_modular.infrastructure.database.repository import Database

CO_SQLITE = importlib.util.find_spec("aiosqlite") is not None
pytestmark = pytest.mark.skipif(not CO_SQLITE, reason="cần aiosqlite")

DA_CHAY: set[str] = set()
DB_PATH = ""


@entity(name="wk_rows")
@dataclass(slots=True)
class WkRow(Entity):
    id: str
    nguon: str = ""
    created_at: datetime = field(default_factory=utcnow)


def tren_dia() -> list[str]:
    """Đọc bằng kết nối KHÁC — chỉ thấy thứ đã COMMIT."""
    conn = sqlite3.connect(DB_PATH)
    try:
        return sorted(r[0] for r in conn.execute("SELECT id FROM wk_rows"))
    finally:
        conn.close()


@injectable
class WkService:
    def __init__(self, repo: Repository[WkRow], db: Database) -> None:
        self._repo = repo
        self._db = db

    async def them(self, id_: str) -> None:
        await self._repo.save(WkRow(id=id_, nguon="worker"))

    async def xoa(self, id_: str) -> bool:
        return await self._repo.delete(id_)

    async def them_roi_huy(self, id_: str) -> None:
        """Ghi trong transaction rồi ném lỗi — không được để lại gì."""
        try:
            async with self._db.transaction():
                await self._repo.save(WkRow(id=id_, nguon="worker"))
                raise RuntimeError("hỏng giữa chừng")
        except RuntimeError:
            pass


@injectable
class WkWorkers:
    def __init__(self, service: WkService) -> None:
        self._service = service

    @worker("wk-ghi", thread=True)
    def ghi_trong_thread(self, data: dict, ctx: WorkerContext) -> None:
        """Đúng hình dạng của worker thật: `def` thường + `ctx.run(...)`."""
        ctx.run(self._service.them(data["id"]))
        DA_CHAY.add("da-ghi")
        while ctx.running:
            ctx.wait(0.02)

    @worker("wk-xoa", thread=True)
    def ghi_roi_xoa(self, data: dict, ctx: WorkerContext) -> None:
        ctx.run(self._service.them(data["id"]))
        DA_CHAY.add("da-ghi-lan-hai")
        ctx.run(self._service.xoa(data["id"]))
        DA_CHAY.add("da-xoa")
        while ctx.running:
            ctx.wait(0.02)

    @worker("wk-tx", thread=True)
    def ghi_roi_huy(self, data: dict, ctx: WorkerContext) -> None:
        ctx.run(self._service.them_roi_huy(data["id"]))
        DA_CHAY.add("da-huy")
        while ctx.running:
            ctx.wait(0.02)


@controller(prefix="/wk")
class WkController:
    def __init__(self, workers: WkWorkers) -> None:
        self._workers = workers

    @post("/ghi")
    async def ghi(self, id_: str) -> dict:
        """Sinh worker TỪ TRONG một HTTP request — ca nguy hiểm nhất.

        Ở đây request scope chắc chắn đang mở, nên worker sao chép được nó.
        """
        await self._workers.ghi_trong_thread(id_, {"id": id_})
        return {"ok": True}

    @post("/ghi-xoa")
    async def ghi_xoa(self, id_: str) -> dict:
        await self._workers.ghi_roi_xoa(id_, {"id": id_})
        return {"ok": True}

    @post("/ghi-huy")
    async def ghi_huy(self, id_: str) -> dict:
        await self._workers.ghi_roi_huy(id_, {"id": id_})
        return {"ok": True}


@pytest.fixture
def client(tmp_path):
    global DB_PATH
    DB_PATH = str(tmp_path / f"{uuid.uuid4().hex}.db")
    DA_CHAY.clear()

    settings = Settings(
        APP_ENV="local",
        APP_DEBUG=True,
        APP_DB=DatabaseSettings(driver="sqlite", dsn=f"sqlite+aiosqlite:///{DB_PATH}"),
        APP_RABBITMQ=RabbitSettings(enabled=False),
        APP_REDIS=RedisSettings(enabled=False),
        APP_MQTT=MqttSettings(enabled=False),
        APP_KAFKA=KafkaSettings(enabled=False),
        APP_WS=WebSocketSettings(adapter="local"),
    )
    app = create_app(settings, package="tests")
    app.include_router(build_router(WkController), prefix="/api")
    with TestClient(app) as client:
        yield client
    assert os.path.exists(DB_PATH)


def cho(dau_moc: str, han: float = 5.0) -> None:
    """Chờ worker chạy tới mốc, tối đa `han` giây (đừng ngủ theo đồng hồ)."""
    moc = time.monotonic()
    while dau_moc not in DA_CHAY and time.monotonic() - moc < han:
        time.sleep(0.01)
    assert dau_moc in DA_CHAY, f"worker không tới được mốc {dau_moc!r}"


def cho_tren_dia(mong_doi: list[str], han: float = 5.0) -> None:
    moc = time.monotonic()
    while tren_dia() != mong_doi and time.monotonic() - moc < han:
        time.sleep(0.01)
    assert tren_dia() == mong_doi


def test_worker_ghi_thi_du_lieu_toi_dia(client):
    """Ca chính: trước đây câu INSERT nằm trong transaction không ai commit."""
    client.post("/api/wk/ghi", params={"id_": "r1"})
    cho("da-ghi")
    cho_tren_dia(["r1"])


def test_worker_xoa_thi_du_lieu_bien_khoi_dia(client):
    """`delete()` trả True mà dòng vẫn còn trên đĩa — đúng lỗi người dùng gặp."""
    client.post("/api/wk/ghi-xoa", params={"id_": "r2"})
    cho("da-xoa")
    cho_tren_dia([])


def test_worker_khong_dung_chung_UnitOfWork_voi_request_sinh_ra_no(client):
    """Chốt đúng cơ chế, không chỉ chốt hệ quả.

    Worker phải KHÔNG resolve được provider request-scoped: nó sống lâu hơn
    request sinh ra nó, nên dùng chung là dùng một transaction đã mồ côi.
    """
    from fastapi_modular.core.container import container
    from fastapi_modular.infrastructure.database.sql import SqlUnitOfWork

    ket_qua: list[str] = []

    @injectable
    class Soi:
        @worker("wk-soi", thread=True)
        def soi(self, data: dict, ctx: WorkerContext) -> None:
            try:
                container.resolve(SqlUnitOfWork)
                ket_qua.append("resolve được")
            except RuntimeError:
                ket_qua.append("đã cắt")
            DA_CHAY.add("da-soi")
            while ctx.running:
                ctx.wait(0.02)

    @controller(prefix="/soi")
    class SoiController:
        def __init__(self, soi: Soi) -> None:
            self._soi = soi

        @post("")
        async def chay(self) -> dict:
            await self._soi.soi("k", {})
            return {"ok": True}

    client.app.include_router(build_router(SoiController), prefix="/api")
    client.post("/api/soi")
    cho("da-soi")

    assert ket_qua == ["đã cắt"]


def test_transaction_trong_worker_van_huy_duoc(client):
    """Cắt request scope không được làm mất `db.transaction()` của worker."""
    client.post("/api/wk/ghi-huy", params={"id_": "r3"})
    cho("da-huy")
    time.sleep(0.2)                      # cho nó cơ hội ghi sai, nếu có
    assert tren_dia() == [], "khối transaction ném lỗi thì không được để lại gì"
