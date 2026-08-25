"""Test `@interval` / `@cron` / `@timeout` và `SchedulerRunner`.

Không cần hạ tầng gì. Phần "bốn tiến trình chỉ chạy một lần" kiểm bằng khoá
`flock` thật trên đĩa, chạy trong tiến trình con — xem `test_khoa_that.py`
không có; ở đây kiểm `FileLock` ở mức API.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

import pytest

from fastapi_modular.core.config import DatabaseSettings, SchedulerSettings, Settings
from fastapi_modular.core.container import container, injectable
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.locks import FileLock, NoLock
from fastapi_modular.core.scheduler import (
    SchedulerRunner,
    cron,
    discover_scheduled,
    interval,
    timeout,
)

DA_CHAY: list[str] = []


@injectable
class ViecTheoLich:
    @interval(seconds=0.05, run_on_startup=True, name="test-nhip")
    async def nhip(self) -> None:
        DA_CHAY.append("nhip")

    @timeout(seconds=0.05, name="test-mot-lan")
    async def mot_lan(self) -> None:
        DA_CHAY.append("mot-lan")

    @interval(seconds=0.05, run_on_startup=True, name="test-hong")
    async def hay_hong(self) -> None:
        DA_CHAY.append("hong")
        raise RuntimeError("hỏng cố ý")

    @cron("0 3 * * *", timezone="Asia/Ho_Chi_Minh", name="test-cron")
    async def theo_cron(self) -> None: ...


def _settings(**scheduler) -> Settings:
    return Settings(
        APP_DB=DatabaseSettings(driver="memory"),
        APP_SCHEDULER=SchedulerSettings(**scheduler),
    )


# ------------------------------------------------------------------ khai báo
def test_quet_duoc_ba_loai():
    specs = {s.label: s for s in discover_scheduled()}
    assert specs["test-nhip"].kind == "interval"
    assert specs["test-mot-lan"].kind == "timeout"
    assert specs["test-cron"].kind == "cron"
    assert specs["test-cron"].timezone == "Asia/Ho_Chi_Minh"


def test_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @interval(seconds=1)
        def dong_bo(self) -> None: ...


def test_khong_duoc_nhan_tham_so():
    """Việc theo lịch tự chạy, không ai truyền gì vào — nói ngay thay vì để
    người dùng gặp TypeError khó hiểu lúc chạy."""
    with pytest.raises(RuntimeError, match="tham số"):

        @interval(seconds=1)
        async def co_tham_so(self, payload: dict) -> None: ...


@pytest.mark.parametrize("seconds", [0, -1])
def test_chu_ky_khong_duong_bi_chan(seconds):
    with pytest.raises(BadRequestError, match="lớn hơn 0"):
        interval(seconds=seconds)


def test_mui_gio_khong_co_bi_chan_ngay_luc_khai_bao():
    """Sai múi giờ mà không chặn thì lịch vẫn chạy, chỉ là sai giờ — và người
    ta chỉ phát hiện sau vài ngày."""
    with pytest.raises(BadRequestError, match="Múi giờ"):
        cron("0 3 * * *", timezone="Asia/Hanoi_Sai")


def test_bieu_thuc_cron_sai_bi_chan_luc_khai_bao():
    with pytest.raises(BadRequestError):
        cron("sai bét")


def test_trung_ten_bi_chan(monkeypatch):
    """Tên là danh tính của KHOÁ: trùng tên thì hai việc tranh nhau một khoá."""
    from fastapi_modular.core import scheduler as scheduler_module

    # KHÔNG @injectable: sổ đăng ký là toàn cục, thêm vào đây là mọi test sau
    # đều thấy hai việc trùng tên. `discover_scheduled` chỉ đọc `vars(cls)`.
    class A:
        @interval(seconds=1, name="trung-ten")
        async def x(self) -> None: ...

    class B:
        @interval(seconds=1, name="trung-ten")
        async def y(self) -> None: ...

    monkeypatch.setattr(scheduler_module, "_REGISTRY", {"A": A, "B": B})
    with pytest.raises(RuntimeError, match="cùng tên"):
        discover_scheduled()


# ------------------------------------------------------------------ chạy thật
async def test_interval_chay_lap_va_timeout_chay_dung_mot_lan():
    DA_CHAY.clear()
    settings = _settings(single=False)
    container.override("Settings", settings)
    runner = SchedulerRunner(settings)
    await runner.startup()
    await asyncio.sleep(0.32)
    await runner.shutdown()

    assert DA_CHAY.count("nhip") >= 3, "interval phải lặp"
    assert DA_CHAY.count("mot-lan") == 1, "timeout chỉ chạy đúng một lần"


async def test_handler_hong_khong_lam_chet_vong_lap():
    """Một lần gọi API hỏng không được làm việc này im vĩnh viễn."""
    DA_CHAY.clear()
    settings = _settings(single=False)
    container.override("Settings", settings)
    runner = SchedulerRunner(settings)
    await runner.startup()
    await asyncio.sleep(0.32)
    await runner.shutdown()

    assert DA_CHAY.count("hong") >= 3, "vẫn phải chạy lại sau khi ném lỗi"


async def test_max_seconds_huy_luot_treo_chu_khong_treo_mai():
    treo = []

    @injectable
    class ViecTreo:
        @interval(seconds=0.05, run_on_startup=True, name="test-treo", max_seconds=0.05)
        async def treo_lau(self) -> None:
            treo.append(1)
            await asyncio.sleep(10)

    settings = _settings(single=False)
    container.override("Settings", settings)
    runner = SchedulerRunner(settings)
    await runner.startup()
    await asyncio.sleep(0.4)
    await runner.shutdown()

    assert len(treo) >= 2, "lượt treo bị huỷ, lượt sau vẫn chạy"


async def test_stats_noi_ro_dang_khoa_kieu_gi():
    settings = _settings(single=False)
    container.override("Settings", settings)
    runner = SchedulerRunner(settings)
    await runner.startup()
    stats = runner.stats()
    await runner.shutdown()

    assert stats["lock"] == "không khoá"
    assert any(j["job"] == "test-nhip" for j in stats["jobs"])


# ------------------------------------------------------------------- khoá
async def test_flock_chi_mot_nguoi_giu_duoc(tmp_path):
    """Hai `FileLock` khác nhau = hai "tiến trình" khác nhau với cùng file."""
    a, b = FileLock(tmp_path), FileLock(tmp_path)
    assert await a.acquire("viec") is True
    assert await b.acquire("viec") is False, "người thứ hai không được vào"

    await a.release("viec")
    assert await b.acquire("viec") is True, "nhả rồi thì người khác lên thay"
    await b.release("viec")


async def test_flock_giu_thi_giu_luon_chu_khong_nha_moi_luot(tmp_path):
    """Giành MỘT LẦN rồi giữ. Khoá-rồi-nhả-ngay vẫn để cả 4 worker cùng chạy."""
    a = FileLock(tmp_path)
    assert await a.acquire("viec") is True
    assert await a.acquire("viec") is True, "chủ gọi lại vẫn là chủ"
    assert await a.renew("viec") is True
    await a.release("viec")
    assert await a.renew("viec") is False


async def test_nolock_luon_cho_qua():
    lock = NoLock()
    assert await lock.acquire("x") is True
    assert lock.scope == "không khoá"


def test_cron_tinh_dung_lan_chay_ke_tiep_theo_mui_gio():
    from zoneinfo import ZoneInfo

    from fastapi_modular.core.cron import parse_cron

    expression = parse_cron("0 3 * * *")
    hanoi = ZoneInfo("Asia/Ho_Chi_Minh")
    nxt = expression.next_after(datetime(2026, 8, 25, 10, 0, tzinfo=hanoi))
    assert (nxt.hour, nxt.day) == (3, 26)


async def test_tat_app_cho_luot_dang_chay_xong():
    xong = []

    @injectable
    class ViecCham:
        @interval(seconds=5, run_on_startup=True, name="test-cham-tat")
        async def cham(self) -> None:
            await asyncio.sleep(0.15)
            xong.append(1)

    settings = _settings(single=False, drain_seconds=2.0)
    container.override("Settings", settings)
    runner = SchedulerRunner(settings)
    await runner.startup()
    await asyncio.sleep(0.05)          # đang chạy dở
    started = time.monotonic()
    await runner.shutdown()

    assert time.monotonic() - started < 2.0
