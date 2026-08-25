"""Test `@job` + `JobQueue` + `JobRunner` — hàng đợi việc trong tiến trình."""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel

from fastapi_modular.core.config import DatabaseSettings, JobSettings, Settings
from fastapi_modular.core.container import container, injectable
from fastapi_modular.core.exceptions import BadRequestError, ServiceUnavailableError
from fastapi_modular.core.jobs import JobQueue, JobRunner, discover_jobs, job

DA_LAM: list[object] = []
SO_LAN_HONG = {"dem": 0}


class ThamSo(BaseModel):
    ma: str
    so_luong: int


@injectable
class ViecNen:
    @job("test-ghi")
    async def ghi(self, payload: dict) -> None:
        await asyncio.sleep(0.01)
        DA_LAM.append(payload["i"])

    @job("test-nang", blocking=True)
    def nang(self, payload: dict) -> None:
        time.sleep(0.01)
        DA_LAM.append(("nang", payload["i"]))

    @job("test-hong", max_retries=2, retry_delay=0.01)
    async def hong(self, payload: dict) -> None:
        SO_LAN_HONG["dem"] += 1
        raise RuntimeError("hỏng cố ý")

    @job("test-model")
    async def theo_model(self, don: ThamSo) -> None:
        DA_LAM.append(don.ma)


def _settings(**jobs) -> Settings:
    return Settings(APP_DB=DatabaseSettings(driver="memory"), APP_JOBS=JobSettings(**jobs))


async def _chay(settings: Settings):
    container.override("Settings", settings)
    queue = JobQueue(settings)
    container.override("JobQueue", queue)
    runner = JobRunner(queue, settings)
    await runner.startup()
    return queue, runner


# ------------------------------------------------------------------ khai báo
def test_quet_duoc_viec():
    table = discover_jobs()
    assert {"test-ghi", "test-nang", "test-hong", "test-model"} <= set(table)
    assert table["test-nang"].blocking is True
    assert table["test-model"].model is ThamSo


def test_viec_thuong_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @job("x-dong-bo")
        def dong_bo(self, payload: dict) -> None: ...


def test_viec_blocking_phai_la_def_thuong():
    """Cả điểm của `blocking=True` là chạy NGOÀI vòng lặp sự kiện."""
    with pytest.raises(RuntimeError, match="def` thường"):

        @job("x-blocking-sai", blocking=True)
        async def sai(self, payload: dict) -> None: ...


def test_chu_ky_phai_co_dung_mot_payload(monkeypatch):
    from fastapi_modular.core import jobs as jobs_module

    class Sai:
        @job("x-chu-ky")
        async def thua(self, a: dict, b: dict) -> None: ...

    monkeypatch.setattr(jobs_module, "_REGISTRY", {"Sai": Sai})
    with pytest.raises(RuntimeError, match="chữ ký"):
        discover_jobs()


def test_trung_ten_viec_bi_chan(monkeypatch):
    from fastapi_modular.core import jobs as jobs_module

    class A:
        @job("x-trung")
        async def m(self, payload: dict) -> None: ...

    class B:
        @job("x-trung")
        async def n(self, payload: dict) -> None: ...

    monkeypatch.setattr(jobs_module, "_REGISTRY", {"A": A, "B": B})
    with pytest.raises(RuntimeError, match="không bao giờ chạy"):
        discover_jobs()


def test_ten_rong_bi_chan():
    with pytest.raises(BadRequestError, match="không được để trống"):
        job("   ")


# ------------------------------------------------------------------ chạy thật
async def test_chay_dung_thu_tu_gui_vao():
    """`workers=1` là bảo đảm thứ tự — đây chính là điều người ta cần khi nói
    'xử lý tuần tự'."""
    DA_LAM.clear()
    queue, runner = await _chay(_settings(workers=1))
    for i in range(6):
        await queue.submit("test-ghi", {"i": i})
    await asyncio.wait_for(queue.raw.join(), timeout=5)
    await runner.shutdown()

    assert DA_LAM == [0, 1, 2, 3, 4, 5]


async def test_viec_blocking_chay_trong_thread():
    DA_LAM.clear()
    queue, runner = await _chay(_settings())
    await queue.submit("test-nang", {"i": 1})
    await asyncio.wait_for(queue.raw.join(), timeout=5)
    await runner.shutdown()

    assert DA_LAM == [("nang", 1)]


async def test_thu_lai_dung_so_lan_roi_bo():
    SO_LAN_HONG["dem"] = 0
    queue, runner = await _chay(_settings())
    await queue.submit("test-hong", {})
    await asyncio.wait_for(queue.raw.join(), timeout=5)
    await runner.shutdown()

    assert SO_LAN_HONG["dem"] == 3, "1 lần đầu + 2 lần thử lại"


async def test_mot_viec_hong_khong_lam_chet_worker():
    DA_LAM.clear()
    SO_LAN_HONG["dem"] = 0
    queue, runner = await _chay(_settings())
    await queue.submit("test-hong", {})
    await queue.submit("test-ghi", {"i": 42})
    await asyncio.wait_for(queue.raw.join(), timeout=5)
    await runner.shutdown()

    assert DA_LAM == [42], "việc sau vẫn phải chạy"


async def test_payload_sai_khuon_bi_chan_truoc_khi_vao_handler():
    DA_LAM.clear()
    queue, runner = await _chay(_settings())
    await queue.submit("test-model", {"ma": "A1"})          # thiếu so_luong
    await queue.submit("test-model", {"ma": "A2", "so_luong": 3})
    await asyncio.wait_for(queue.raw.join(), timeout=5)
    await runner.shutdown()

    assert DA_LAM == ["A2"]


async def test_ten_viec_khong_co_thi_ghi_log_chu_khong_lam_sap():
    DA_LAM.clear()
    queue, runner = await _chay(_settings())
    await queue.submit("khong-he-co", {})
    await queue.submit("test-ghi", {"i": 7})
    await asyncio.wait_for(queue.raw.join(), timeout=5)
    await runner.shutdown()

    assert DA_LAM == [7]


# ------------------------------------------------------------------ áp lực ngược
async def test_hang_doi_day_thi_bao_ngay_chu_khong_cho():
    """Hàng đợi đầy nghĩa là bên chạy chậm hơn bên gửi. Giấu điều đó bằng cách
    chờ chỉ làm request treo theo."""
    settings = _settings(max_queued=3, enabled=False)   # không chạy worker
    container.override("Settings", settings)
    queue = JobQueue(settings)

    for i in range(3):
        await queue.submit("test-ghi", {"i": i})
    assert queue.depth() == 3

    with pytest.raises(ServiceUnavailableError, match="đầy"):
        await queue.submit("test-ghi", {"i": 99})


async def test_wait_true_thi_cho_toi_khi_co_cho():
    settings = _settings(max_queued=1, enabled=False)
    container.override("Settings", settings)
    queue = JobQueue(settings)
    await queue.submit("test-ghi", {"i": 0})

    cho = asyncio.create_task(queue.submit("test-ghi", {"i": 1}, wait=True))
    await asyncio.sleep(0.02)
    assert not cho.done(), "phải đang chờ chỗ"

    queue.raw.get_nowait()                   # nhường một chỗ
    await asyncio.wait_for(cho, timeout=2)
    assert queue.depth() == 1


# ------------------------------------------------------------------ lúc tắt
async def test_tat_app_chay_not_hang_doi():
    DA_LAM.clear()
    queue, runner = await _chay(_settings(drain_seconds=5.0))
    for i in range(4):
        await queue.submit("test-ghi", {"i": i})
    await runner.shutdown()

    assert DA_LAM == [0, 1, 2, 3], "phải chạy nốt trước khi tắt"


async def test_qua_han_don_thi_bao_ro_con_bao_nhieu_viec_mat():
    """Việc nằm trong RAM. Mất thì phải nói ra bằng con số, không im lặng."""
    DA_LAM.clear()
    queue, runner = await _chay(_settings(drain_seconds=0.05))
    for i in range(50):
        await queue.submit("test-ghi", {"i": i})
    await runner.shutdown()

    assert len(DA_LAM) < 50, "hết giờ dọn thì phần còn lại bị bỏ"


def test_stats_noi_ro_do_sau_hang_doi():
    settings = _settings(enabled=False)
    queue = JobQueue(settings)
    runner = JobRunner(queue, settings)
    assert runner.stats()["queued"] == 0
