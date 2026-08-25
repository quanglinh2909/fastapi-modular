"""Test `@worker` — vòng lặp sống mãi, N bản, mỗi bản một tham số."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time

import pytest

from fastapi_modular.core.config import DatabaseSettings, Settings, WorkerSettings
from fastapi_modular.core.container import container
from fastapi_modular.core.exceptions import BadRequestError, ServiceUnavailableError
from fastapi_modular.core.workers import BlockingPool, WorkerContext, WorkerPool, worker

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
    # Rộng tay: mỗi lần hỏng, khung in cả traceback ra log và việc đó tốn gần
    # 0.1s — chờ sát nút thì test đo tốc độ in log chứ không đo việc dựng lại.
    await asyncio.sleep(0.6)
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


# ------------------------------------------- thread riêng, không mượn pool chung
async def test_nhieu_worker_thread_khong_lam_can_pool_chung(pool: WorkerPool):
    """Đây là một lỗi THẬT đã mắc: `thread=True` từng dùng `asyncio.to_thread`,
    tức mượn `ThreadPoolExecutor` dùng chung của event loop (trần
    `min(32, cpu+4)`, thường là 16). Vòng lặp chạy MÃI thì giữ chỗ đó vĩnh
    viễn, nên đủ 16 worker là pool cạn và mọi `ctx.blocking` treo cứng — đo
    được: 20 worker thì cả tiến trình chết, không phải chậm.

    Nay `@worker(thread=True)` có thread RIÊNG. Test này dựng số worker vượt
    hẳn trần pool rồi kiểm `ctx.blocking` vẫn chạy ngay.
    """
    import os

    tran_pool = min(32, (os.cpu_count() or 1) + 4)
    dung = threading.Event()

    class Giu:
        @worker("giu-cho", thread=True)
        def giu(self, ctx: WorkerContext) -> None:
            while ctx.running:
                dung.wait(0.02)

    service = Giu()
    for i in range(tran_pool + 4):          # VƯỢT hẳn trần pool
        await service.giu(f"w{i}")
    await asyncio.sleep(0.1)

    ctx = WorkerContext("do", "", asyncio.get_running_loop(), thread_mode=False)
    started = time.monotonic()
    await asyncio.wait_for(ctx.blocking(time.sleep, 0.01), timeout=5)
    tre = time.monotonic() - started

    await pool.stop_all()
    assert tre < 1.0, (
        f"ctx.blocking mất {tre:.2f}s với {tran_pool + 4} worker thread — "
        "worker đang mượn pool dùng chung thay vì có thread riêng"
    )


async def test_thread_rieng_van_giu_duoc_request_id(pool: WorkerPool):
    """`asyncio.to_thread` chép contextvars sang thread; bản tự viết cũng phải."""
    from fastapi_modular.core.context import get_request_id, set_request_id

    thay: list[str | None] = []

    class Doc:
        @worker("doc-id", thread=True)
        def doc(self, ctx: WorkerContext) -> None:
            thay.append(get_request_id())

    set_request_id("abc-123")
    service = Doc()
    await service.doc("x")
    await asyncio.sleep(0.15)
    await pool.stop_all()

    assert thay and thay[0] is not None, "request-id phải theo được sang thread"


# ------------------------------------------------- dừng theo yêu cầu và dọn dẹp
async def test_stop_ngay_tren_method_va_cho_don_dep_xong(pool: WorkerPool):
    """`await self.watch.stop(key)` — không phải nhắc lại tên worker dạng chuỗi.

    Và nó CHỜ tới lúc `finally:` chạy xong, nên viết phần dọn dẹp ngay dưới lời
    gọi là an toàn: khi nó trả về thì camera đã đóng.
    """
    dau_vet: list[str] = []

    class DeviceService:
        @worker("device-cam")
        async def watch(self, data: dict, ctx: WorkerContext) -> None:
            dau_vet.append(f"mo:{data['ip']}")
            try:
                while ctx.running:
                    await asyncio.sleep(0.01)
            finally:
                dau_vet.append(f"dong:{data['ip']}")

    service = DeviceService()
    await service.watch("cam-1", {"ip": "10.0.0.1"})
    await asyncio.sleep(0.05)
    assert service.watch.is_running("cam-1") is True

    assert await service.watch.stop("cam-1") is True
    assert dau_vet == ["mo:10.0.0.1", "dong:10.0.0.1"], "phải dọn xong rồi mới trả về"
    assert service.watch.is_running("cam-1") is False
    assert await service.watch.stop("cam-1") is False, "không còn bản nào mang khoá đó"


async def test_stop_chi_dung_dung_mot_khoa(pool: WorkerPool):
    """Gỡ một thiết bị thì những thiết bị khác không được rớt theo."""

    class DeviceService:
        @worker("device-many")
        async def watch(self, data: dict, ctx: WorkerContext) -> None:
            while ctx.running:
                await asyncio.sleep(0.01)

    service = DeviceService()
    for i in range(3):
        await service.watch(f"cam-{i}", {"ip": f"10.0.0.{i}"})
    await asyncio.sleep(0.05)

    await service.watch.stop("cam-1")
    con_lai = {row["key"] for row in service.watch.running()}
    assert con_lai == {"cam-0", "cam-2"}

    assert await service.watch.stop_all() == 2
    assert service.watch.running() == []


async def test_stop_thread_worker_cho_toi_khi_vong_lap_thoat(pool: WorkerPool):
    """`thread=True` không huỷ ngang được — `ctx.running` là đường duy nhất."""
    dau_vet: list[str] = []

    class DeviceService:
        @worker("device-thread", thread=True)
        def watch(self, data: dict, ctx: WorkerContext) -> None:
            try:
                while ctx.running:
                    ctx.wait(0.01)          # thay time.sleep: tỉnh ngay khi có lệnh dừng
            finally:
                dau_vet.append("dong")

    service = DeviceService()
    await service.watch("cam-9", {})
    await asyncio.sleep(0.05)
    await service.watch.stop("cam-9")

    assert dau_vet == ["dong"]


# ------------------------------------------------------- bẫy `while True:`
def test_while_true_khong_co_co_dung_thi_bi_keu(capsys):
    """Cái bẫy đắt nhất: nó chỉ lộ ra lúc TẮT, dưới dạng "Ctrl+C không ăn"."""

    class Quen:
        @worker("keu-len", thread=True)
        def watch(self, ctx: WorkerContext) -> None:
            while True:
                time.sleep(1)

    ra = capsys.readouterr().out
    assert "worker.endless_loop" in ra
    assert "ctx.running" in ra, "phải chỉ luôn cách sửa, đừng chỉ báo là sai"


def test_while_ctx_running_thi_khong_keu(capsys):
    class Dung:
        @worker("khong-keu", thread=True)
        def watch(self, ctx: WorkerContext) -> None:
            while True:
                if not ctx.running:
                    break
                ctx.wait(1)

    assert "worker.endless_loop" not in capsys.readouterr().out


def test_job_va_interval_cung_bi_keu(capsys):
    """Cùng cái bẫy, và với `@job(thread=True)` thì hậu quả còn nặng hơn."""
    from fastapi_modular.core.jobs import job
    from fastapi_modular.core.scheduler import interval

    class Quen:
        @job("keu-job", thread=True)
        def detect(self, payload: dict) -> None:
            while True:
                time.sleep(1)

        @interval(seconds=5, thread=True, name="keu-interval")
        def quet(self) -> None:
            while True:
                time.sleep(1)

    ra = capsys.readouterr().out
    assert ra.count("worker.endless_loop") == 2


# ------------------------------------------- tiến trình có thoát được không
_KICH_BAN = """
import sys, time
sys.path.insert(0, {repo!r})
{dung_pool}
pool.submit(time.sleep, 300)          # lời gọi chặn không bao giờ trả về
time.sleep(0.3)                       # đợi thread thật sự sinh ra
print("den cuoi", flush=True)
"""

_POOL_CUA_KHUNG = (
    "from fastapi_modular.core.workers import BlockingPool\n"
    "pool = BlockingPool(2)"
)
_POOL_CHUAN = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "pool = ThreadPoolExecutor(max_workers=2)"
)


def _chay(dung_pool: str) -> subprocess.CompletedProcess:
    from pathlib import Path

    repo = str(Path(__file__).resolve().parent.parent)
    return subprocess.run(
        [sys.executable, "-c", _KICH_BAN.format(repo=repo, dung_pool=dung_pool)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_loi_goi_chan_treo_khong_giu_tien_trinh_lai():
    """Đây chính là triệu chứng "Ctrl+C bấm mãi không được".

    Một `ctx.blocking(cap.read)` treo trên luồng RTSP đã chết là chuyện có
    thật, và nó không được phép biến thành `kill -9`.
    """
    xong = _chay(_POOL_CUA_KHUNG)
    assert xong.returncode == 0
    assert "den cuoi" in xong.stdout


def test_pool_chuan_thi_dung_la_treo_that():
    """Chứng minh cái trên không thừa: đổi sang ThreadPoolExecutor là treo.

    Python JOIN mọi thread không phải daemon lúc thoát, không timeout, không
    cách nào bỏ qua — nên chỉ một lời gọi không về là hết đường thoát.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        _chay(_POOL_CHUAN)


def test_blocking_van_chay_dung_va_tai_su_dung_thread():
    pool = BlockingPool(3)
    ket_qua = [pool.submit(lambda x: x * 2, i).result(timeout=5) for i in range(20)]
    assert ket_qua == [i * 2 for i in range(20)]
    assert pool.stats()["threads"] <= 3, "phải tái sử dụng chứ không mở 20 thread"
    pool.shutdown()


def test_blocking_chuyen_nguyen_ven_loi_ve_cho_goi():
    pool = BlockingPool(1)

    def hong() -> None:
        raise ValueError("hỏng cố ý")

    with pytest.raises(ValueError, match="hỏng cố ý"):
        pool.submit(hong).result(timeout=5)
    pool.shutdown()
