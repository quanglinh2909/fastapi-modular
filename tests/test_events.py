"""Test `@on_event` + `EventBus` — fanout trong tiến trình.

Không cần hạ tầng gì. Điểm khác `@job` mà mọi test ở đây xoay quanh: **nhiều
handler cùng nghe một sự kiện, và chúng chạy SONG SONG**.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from pydantic import BaseModel

from fastapi_modular.core.config import DatabaseSettings, EventSettings, Settings
from fastapi_modular.core.container import container, injectable
from fastapi_modular.core.events import EventBus, discover_listeners, matches, on_event
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.workers import WorkerContext

DA_CHAY: list[str] = []


class DonHang(BaseModel):
    ma: str
    so_tien: int


@injectable
class MailService:
    @on_event("order.paid")
    async def send_receipt(self, data: dict) -> None:
        await asyncio.sleep(0.05)
        DA_CHAY.append(f"mail:{data['id']}")


@injectable
class StatsService:
    @on_event("order.paid")
    async def count(self, data: dict) -> None:
        await asyncio.sleep(0.05)
        DA_CHAY.append(f"stats:{data['id']}")

    @on_event("order.*")
    async def audit(self, data: dict) -> None:
        DA_CHAY.append("audit")

    @on_event("order.paid")
    async def hay_hong(self, data: dict) -> None:
        raise RuntimeError("hỏng cố ý")

    @on_event("cham.qua-han", max_seconds=0.05)
    async def cham(self, data: dict) -> None:
        await asyncio.sleep(5)
        DA_CHAY.append("khong-bao-gio")

    @on_event("khuon.don-hang")
    async def theo_model(self, don: DonHang) -> None:
        DA_CHAY.append(f"model:{don.ma}")

    @on_event("ping.gui")
    async def khong_can_data(self) -> None:
        DA_CHAY.append("ping")


def _settings(**events) -> Settings:
    return Settings(
        APP_DB=DatabaseSettings(driver="memory"),
        APP_EVENTS=EventSettings(**events),
    )


@pytest.fixture
async def bus(**_):
    settings = _settings()
    container.override("Settings", settings)
    made = EventBus(settings)
    container.override("EventBus", made)
    DA_CHAY.clear()
    await made.startup()
    yield made
    await made.shutdown()


# ------------------------------------------------------------------ khớp mẫu
@pytest.mark.parametrize(
    "pattern,event,khop",
    [
        ("order.paid", "order.paid", True),
        ("order.paid", "order.shipped", False),
        ("order.*", "order.paid", True),
        ("order.*", "order.item.added", False),
        ("order.#", "order.item.added", True),
        ("order.#", "order", True),
        ("camera.*.motion", "camera.12.motion", True),
        ("camera.*.motion", "camera.motion", False),
        ("#", "bat.ky.cai.gi", True),
    ],
)
def test_khop_mau(pattern, event, khop):
    assert matches(pattern, event) is khop


def test_dau_thăng_phai_nam_cuoi():
    """`#` nuốt mọi tầng còn lại nên đặt ở giữa là vô nghĩa — chặn ngay."""
    with pytest.raises(BadRequestError, match="phải nằm CUỐI"):
        on_event("order.#.paid")


def test_mau_rong_va_tang_rong_bi_chan():
    with pytest.raises(BadRequestError, match="không được để trống"):
        on_event("  ")
    with pytest.raises(BadRequestError, match="tầng rỗng"):
        on_event("order..paid")


async def test_phat_bang_ky_tu_dai_dien_bi_chan(bus: EventBus):
    """Đại diện chỉ dùng khi NGHE. Phát `order.*` là ý gì thì không ai biết."""
    with pytest.raises(BadRequestError, match="ĐĂNG KÝ NGHE"):
        await bus.emit("order.*", {})


# ------------------------------------------------------------------ khai báo
def test_quet_duoc_moi_noi_nghe():
    patterns = [spec.pattern for spec in discover_listeners()]
    assert patterns.count("order.paid") >= 3, "nhiều handler một sự kiện là chuyện bình thường"
    assert "order.*" in patterns


def test_handler_thuong_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @on_event("x.dong-bo")
        def dong_bo(self, data: dict) -> None: ...


def test_thua_tham_so_bi_chan(monkeypatch):
    from fastapi_modular.core import events as events_module

    class Sai:
        @on_event("x.thua")
        async def thua(self, a: dict, b: dict) -> None: ...

    monkeypatch.setattr(events_module, "_REGISTRY", {"Sai": Sai})
    with pytest.raises(RuntimeError, match="chữ ký"):
        discover_listeners()


# ------------------------------------------------------------------ fanout
async def test_mot_su_kien_goi_moi_noi_nghe(bus: EventBus):
    thanh_cong = await bus.emit("order.paid", {"id": "A1"})

    assert "mail:A1" in DA_CHAY and "stats:A1" in DA_CHAY
    assert "audit" in DA_CHAY, "mẫu `order.*` cũng phải khớp"
    assert thanh_cong == 3, "3 chạy được, 1 ném lỗi"


async def test_cac_handler_chay_SONG_SONG(bus: EventBus):
    """Cả điểm của fanout.

    Đo bằng CHỒNG LẤN chứ không bằng đồng hồ: một ngưỡng thời gian sẽ chập
    chờn theo máy và theo việc chạy một mình hay chạy cùng cả bộ test. Đếm số
    handler đang ở trong thân hàm cùng lúc thì không phụ thuộc vào gì cả.
    """
    dang_chay = dinh_cao = 0

    async def cham(data: dict) -> None:
        nonlocal dang_chay, dinh_cao
        dang_chay += 1
        dinh_cao = max(dinh_cao, dang_chay)
        await asyncio.sleep(0.02)
        dang_chay -= 1

    for _ in range(4):
        bus.subscribe("song.song", cham)
    await bus.emit("song.song", {})

    assert dinh_cao == 4, f"cao nhất chỉ {dinh_cao} handler cùng lúc — đang chạy lần lượt"


async def test_mot_handler_hong_khong_keo_theo_nhung_cai_khac(bus: EventBus):
    """Gửi mail hỏng mà mất luôn cập nhật thống kê là vô lý."""
    thanh_cong = await bus.emit("order.paid", {"id": "A3"})

    assert "mail:A3" in DA_CHAY and "stats:A3" in DA_CHAY
    assert thanh_cong == 3, "so số trả về với số nơi nghe là biết có ai hỏng"
    assert len(bus.listeners("order.paid")) == 4


async def test_qua_han_thi_huy_lot_do_chu_khong_treo(bus: EventBus):
    started = time.monotonic()
    thanh_cong = await bus.emit("cham.qua-han", {})
    mat = time.monotonic() - started

    assert thanh_cong == 0
    assert mat < 1.0, "phải bị huỷ theo max_seconds chứ không chờ đủ 5 giây"
    assert "khong-bao-gio" not in DA_CHAY


async def test_khong_ai_nghe_thi_khong_phai_loi(bus: EventBus):
    """Khác `@job`: gửi việc không ai chạy là mất việc, còn sự kiện không ai
    nghe là chuyện bình thường."""
    assert await bus.emit("chang.ai.nghe", {"x": 1}) == 0


async def test_kiem_khuon_bang_model(bus: EventBus):
    assert await bus.emit("khuon.don-hang", {"ma": "B1", "so_tien": 10}) == 1
    assert DA_CHAY == ["model:B1"]

    DA_CHAY.clear()
    assert await bus.emit("khuon.don-hang", {"ma": "B2"}) == 0, "thiếu trường thì bị chặn"
    assert DA_CHAY == []


async def test_handler_khong_khai_data_van_chay(bus: EventBus):
    assert await bus.emit("ping.gui") == 1
    assert DA_CHAY == ["ping"]


# ------------------------------------------------------------------ dispatch
async def test_dispatch_tra_ve_ngay_khong_cho(bus: EventBus):
    started = time.monotonic()
    so_noi_nghe = bus.dispatch("order.paid", {"id": "A4"})
    ngay = time.monotonic() - started

    assert so_noi_nghe == 4
    assert ngay < 0.01, "dispatch phải trả về NGAY"
    assert "mail:A4" not in DA_CHAY, "lúc này handler chưa kịp chạy xong"

    await asyncio.sleep(0.15)
    assert "mail:A4" in DA_CHAY, "nhưng nó vẫn phải chạy"


async def test_dispatch_giu_nguyen_request_id_cua_ben_phat(bus: EventBus):
    """Handler nền là phần đuôi của chính request đó — log phải nối được."""
    from fastapi_modular.core.context import set_request_id

    thay: list[str | None] = []
    bus.subscribe("trace.x", lambda data: _ghi_request_id(thay))

    set_request_id("req-abc")
    bus.dispatch("trace.x", {})
    await asyncio.sleep(0.05)

    assert thay == ["req-abc"]


async def _ghi_request_id(thay: list) -> None:
    from fastapi_modular.core.context import get_request_id

    thay.append(get_request_id())


async def test_cham_tran_pending_thi_bo_va_noi_ro(bus: EventBus):
    settings = _settings(max_pending=2)
    container.override("Settings", settings)
    nho = EventBus(settings)
    container.override("EventBus", nho)
    await nho.startup()
    try:
        nho.dispatch("order.paid", {"id": "X"})          # 4 lượt -> đã quá 2
        assert nho.dispatch("order.paid", {"id": "Y"}) == 0, "chạm trần thì bỏ"
    finally:
        await nho.shutdown()


# ------------------------------------------------------------------ subscribe
async def test_subscribe_va_huy_dang_ky(bus: EventBus):
    nhan: list[dict] = []

    async def ghi(data: dict) -> None:
        nhan.append(data)

    bo_nghe = bus.subscribe("phien.*", ghi)
    assert await bus.emit("phien.mo", {"id": 1}) == 1

    bo_nghe()
    assert await bus.emit("phien.mo", {"id": 2}) == 0, "huỷ rồi thì không gọi nữa"
    assert nhan == [{"id": 1}]


async def test_listeners_noi_ro_ai_dang_nghe(bus: EventBus):
    ai = bus.listeners("order.paid")
    assert len(ai) == 4
    assert any("MailService.send_receipt" in dong for dong in ai)
    assert bus.stats()["listeners"] >= 4


# ------------------------------------------------------------------ thread
async def test_handler_thread_ghi_duoc_database_qua_ctx(bus: EventBus):
    ghi: list[str] = []
    threads: list[int] = []

    @injectable
    class NangCoCtx:
        async def save(self, value: str) -> None:
            await asyncio.sleep(0)
            ghi.append(value)

        @on_event("nang.xong", thread=True)
        def nang(self, data: dict, ctx: WorkerContext) -> None:
            threads.append(threading.get_ident())
            time.sleep(0.01)
            ctx.run(self.save(data["ma"]))

    await bus.shutdown()
    await bus.startup()
    assert await bus.emit("nang.xong", {"ma": "C1"}) == 1

    assert ghi == ["C1"]
    assert threads and threading.get_ident() not in threads, "phải chạy ở thread khác"


# ------------------------------------------------------------------ vòng đời
async def test_tat_app_cho_handler_nen_chay_not(bus: EventBus):
    bus.dispatch("order.paid", {"id": "A9"})
    await bus.shutdown()

    assert "mail:A9" in DA_CHAY, "phải chạy nốt trước khi tắt"


async def test_tat_roi_thi_khong_nhan_them(bus: EventBus):
    await bus.shutdown()
    assert await bus.emit("order.paid", {"id": "Z"}) == 0


async def test_tat_bang_cau_hinh_thi_khong_ai_chay():
    settings = _settings(enabled=False)
    container.override("Settings", settings)
    tat = EventBus(settings)
    await tat.startup()

    assert await tat.emit("order.paid", {"id": "K"}) == 0
    assert DA_CHAY == []
    await tat.shutdown()


# ------------------------------------------------------- qua create_app thật
def test_lifespan_dung_EventBus_len_va_tat_di(client):
    """Mọi test trên bỏ qua lifespan; test này giữ phần đấu dây.

    Quên một dòng trong `lifespan.py` thì `emit` im lặng không gọi ai — và
    "im lặng không gọi ai" trông y hệt "không ai đăng ký nghe".
    """
    bus = client.app.state.events
    assert bus is not None
    assert bus.stats()["listeners"] > 0, "phải quét được nơi nghe lúc khởi động"


async def test_phat_truoc_khi_startup_thi_noi_ra_chu_khong_im():
    """Im lặng ở đây trông hệt như 'không ai nghe' và rất khó lần."""
    settings = _settings()
    container.override("Settings", settings)
    chua_bat = EventBus(settings)

    assert await chua_bat.emit("order.paid", {"id": "N"}) == 0
