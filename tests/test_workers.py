"""Test `@worker` — vòng lặp sống mãi, N bản, mỗi bản một tham số."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from fastapi_modular.core.config import DatabaseSettings, Settings, WorkerSettings
from fastapi_modular.core.container import container
from fastapi_modular.core.exceptions import BadRequestError, ServiceUnavailableError
from fastapi_modular.core.workers import WorkerContext, WorkerPool, worker

DB: list[tuple[str, str]] = []
THREADS: dict[str, int] = {}


def _blocking_open(ip: str) -> str:
    time.sleep(0.01)
    return f"cap({ip})"


def _blocking_read(cap: str) -> str:
    time.sleep(0.005)
    return f"frame-{cap}"


class CameraService:
    """Không `@injectable`: sổ đăng ký là toàn cục, và các test khác sẽ thấy."""

    async def save(self, ip: str, frame: str) -> None:
        await asyncio.sleep(0)
        DB.append((ip, frame))

    @worker("watch")
    async def watch(self, data: dict, ctx: WorkerContext) -> None:
        ip = data["ip"]
        cap = await ctx.blocking(_blocking_open, ip)     # dựng, NGOÀI vòng lặp
        THREADS.setdefault(ip, threading.get_ident())
        try:
            while ctx.running:
                frame = await ctx.blocking(_blocking_read, cap)
                await self.save(ip, frame)               # await thẳng
        finally:
            DB.append((ip, "cleaned"))

    @worker("watch-thread", thread=True)
    def watch_thread(self, data: dict, ctx: WorkerContext) -> None:
        ip = data["ip"]
        cap = _blocking_open(ip)                         # đang ở thread, gọi thẳng
        try:
            while ctx.running:
                frame = _blocking_read(cap)
                ctx.run(self.save(ip, frame))            # cầu nối sang event loop
        finally:
            DB.append((ip, "cleaned-thread"))

    @worker(name="crashy", restart_delay=0.02, max_restart_delay=0.05)
    async def crashy(self, ctx: WorkerContext) -> None:
        DB.append(("crashy", "start"))
        raise RuntimeError("camera rớt mạng")

    @worker(name="once", restart=False)
    async def once(self, ctx: WorkerContext) -> None:
        DB.append(("once", "start"))
        raise RuntimeError("hỏng hẳn")

    @worker(name="finishes")
    async def finishes(self, ctx: WorkerContext) -> None:
        DB.append(("finishes", "done"))


def _settings(**workers) -> Settings:
    return Settings(
        APP_DB=DatabaseSettings(driver="memory"), APP_WORKERS=WorkerSettings(**workers)
    )


@pytest.fixture
def pool():
    settings = _settings()
    container.override("Settings", settings)
    made = WorkerPool(settings)
    container.override("WorkerPool", made)
    DB.clear()
    THREADS.clear()
    yield made


# ------------------------------------------------------------------ khai báo
def test_worker_thuong_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @worker()
        def dong_bo(self, ctx: WorkerContext) -> None: ...


def test_worker_thread_phai_la_def_thuong():
    with pytest.raises(RuntimeError, match="def` thường"):

        @worker(thread=True)
        async def sai(self, ctx: WorkerContext) -> None: ...


def test_chu_ky_chi_nhan_data_va_ctx():
    with pytest.raises(RuntimeError, match="chữ ký"):

        @worker("sai")
        async def sai(self, data: dict, them: str, ctx: WorkerContext) -> None: ...


def test_ten_mac_dinh_kem_ten_lop_nen_khong_dung_nhau():
    """Hai method trùng tên ở hai lớp khác nhau phải ra hai worker khác nhau."""

    class A:
        @worker()
        async def watch(self, ctx: WorkerContext) -> None: ...

    class B:
        @worker()
        async def watch(self, ctx: WorkerContext) -> None: ...

    from fastapi_modular.core.workers import _SPEC_ATTR

    assert getattr(A.watch, _SPEC_ATTR).name != getattr(B.watch, _SPEC_ATTR).name


def test_restart_delay_phai_duong():
    with pytest.raises(BadRequestError, match="lớn hơn 0"):
        worker(restart_delay=0)


# ------------------------------------------------------------------ chạy thật
async def test_moi_ip_mot_ban_va_deu_ghi_duoc_database(pool: WorkerPool):
    """Đúng hình dạng đã hỏi: nhiều camera, khác nhau mỗi cái IP."""
    service = CameraService()
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        await service.watch(ip, {"ip": ip})

    await asyncio.sleep(0.2)
    assert pool.stats()["count"] == 3

    await pool.stop_all()
    ips = {ip for ip, _ in DB if ip.startswith("10.")}
    assert ips == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}, "cả ba camera đều ghi được"


async def test_goi_lai_cung_khoa_khong_mo_them_ban(pool: WorkerPool):
    """Mở hai kết nối RTSP tới cùng một thiết bị là cách nhanh nhất để cả hai giật."""
    service = CameraService()
    first = await service.watch("10.0.0.1", {"ip": "10.0.0.1"})
    second = await service.watch("10.0.0.1", {"ip": "10.0.0.1"})

    assert pool.stats()["count"] == 1
    assert first is second
    await pool.stop_all()


async def test_vong_lap_khong_chan_event_loop(pool: WorkerPool):
    """Cả điểm của `ctx.blocking`: hàm chặn chạy ở thread khác."""
    service = CameraService()
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        await service.watch(ip, {"ip": ip})
    await asyncio.sleep(0.05)

    started = time.monotonic()
    await asyncio.sleep(0.05)
    tre = time.monotonic() - started - 0.05

    await pool.stop_all()
    assert tre < 0.05, f"event loop bị giữ {tre:.3f}s — hàm chặn đang chạy nhầm chỗ"


async def test_thread_mode_ghi_database_qua_ctx_run(pool: WorkerPool):
    """Câu trả lời cho 'chạy trong thread thì ghi database kiểu gì'."""
    service = CameraService()
    await service.watch_thread("10.0.0.9", {"ip": "10.0.0.9"})
    await asyncio.sleep(0.15)
    await pool.stop_all()

    assert any(ip == "10.0.0.9" and frame.startswith("frame") for ip, frame in DB)
    assert ("10.0.0.9", "cleaned-thread") in DB, "phần `finally` vẫn chạy"


async def test_ctx_run_bi_chan_o_worker_async(pool: WorkerPool):
    ctx = WorkerContext("x", "", asyncio.get_running_loop(), thread_mode=False)

    async def noop() -> None: ...

    with pytest.raises(RuntimeError, match="thread=True"):
        ctx.run(noop())


# ------------------------------------------------------------------ hỏng
async def test_hong_thi_dung_lai_chu_khong_chet_im(pool: WorkerPool):
    """Camera rớt mạng là chuyện thường ngày; chết im thì không ai biết."""
    service = CameraService()
    await service.crashy()
    await asyncio.sleep(0.2)
    await pool.stop_all()

    assert len([x for x in DB if x == ("crashy", "start")]) >= 3, "phải dựng lại nhiều lần"


async def test_restart_false_thi_hong_la_dung_han(pool: WorkerPool):
    service = CameraService()
    await service.once()
    await asyncio.sleep(0.15)

    assert len([x for x in DB if x == ("once", "start")]) == 1
    assert pool.stats()["count"] == 0, "dừng hẳn thì phải rời sổ"


async def test_vong_lap_tu_ket_thuc_thi_khong_dung_lai(pool: WorkerPool):
    """Người viết chủ động thoát vòng lặp — đừng dựng lại sau lưng họ."""
    service = CameraService()
    await service.finishes()
    await asyncio.sleep(0.1)

    assert len([x for x in DB if x == ("finishes", "done")]) == 1
    assert pool.stats()["count"] == 0


# ------------------------------------------------------------------ dừng
async def test_dung_dung_mot_ban(pool: WorkerPool):
    service = CameraService()
    for ip in ("10.0.0.1", "10.0.0.2"):
        await service.watch(ip, {"ip": ip})
    await asyncio.sleep(0.05)

    assert await pool.stop("watch", "10.0.0.2") is True
    assert {x["key"] for x in pool.running()} == {"10.0.0.1"}
    assert await pool.stop("watch", "khong-co") is False
    await pool.stop_all()


async def test_dung_het_thi_phan_finally_van_chay(pool: WorkerPool):
    service = CameraService()
    await service.watch("10.0.0.1", {"ip": "10.0.0.1"})
    await asyncio.sleep(0.05)
    await pool.stop_all()

    assert ("10.0.0.1", "cleaned") in DB
    assert pool.stats()["count"] == 0


async def test_tran_so_ban_chan_viec_sinh_worker_vo_han():
    """Không có trần thì một endpoint gọi nhầm sẽ phình tới lúc hết RAM."""
    settings = _settings(max_instances=2)
    container.override("Settings", settings)
    small = WorkerPool(settings)
    container.override("WorkerPool", small)
    service = CameraService()

    await service.watch("10.0.0.1", {"ip": "10.0.0.1"})
    await service.watch("10.0.0.2")
    with pytest.raises(ServiceUnavailableError, match="MAX_INSTANCES"):
        await service.watch("10.0.0.3")
    await small.stop_all()


async def test_ctx_wait_tinh_ngay_khi_co_lenh_dung(pool: WorkerPool):
    """`time.sleep(30)` giữ lúc tắt app thêm 30 giây; `ctx.wait` thì không."""
    ctx = WorkerContext("x", "", asyncio.get_running_loop(), thread_mode=True)
    started = time.monotonic()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        ctx.request_stop()

    stopper = asyncio.create_task(stop_soon())
    woke = await asyncio.to_thread(ctx.wait, 5.0)
    await stopper

    assert woke is True
    assert time.monotonic() - started < 1.0
    assert ctx.running is False
