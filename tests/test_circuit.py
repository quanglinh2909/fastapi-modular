"""Test circuit breaker và hạn thời gian gọi database."""

from __future__ import annotations

import asyncio

import pytest

from pymodular.infrastructure.database.circuit import (
    CircuitBreakerBackend,
    CircuitOpenError,
    CircuitState,
)


class _Backend:
    """Backend giả: điều khiển được bằng `error` và `delay`."""

    name = "gia"

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.delay = 0.0
        self.calls = 0

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...

    async def ping(self) -> bool:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return True


def _wrap(inner, **kw) -> CircuitBreakerBackend:
    kw.setdefault("failure_threshold", 3)
    kw.setdefault("reset_seconds", 0.2)
    kw.setdefault("call_timeout_seconds", 0.2)
    return CircuitBreakerBackend(inner, **kw)


@pytest.mark.asyncio
async def test_dong_mach_khi_binh_thuong():
    breaker = _wrap(_Backend())
    assert await breaker.ping() is True
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_ngat_mach_sau_khi_hong_du_nguong():
    inner = _Backend()
    inner.error = ConnectionRefusedError(111, "refused")
    breaker = _wrap(inner)

    for _ in range(3):
        with pytest.raises(ConnectionRefusedError):
            await breaker.ping()
    assert breaker.state is CircuitState.OPEN

    # từ đây không chạm backend nữa
    calls_truoc = inner.calls
    with pytest.raises(CircuitOpenError):
        await breaker.ping()
    assert inner.calls == calls_truoc, "mạch ngắt thì không được gọi xuống database"


@pytest.mark.asyncio
async def test_loi_nghiep_vu_khong_lam_ngat_mach():
    """Trùng khoá nghĩa là database vẫn khoẻ — không được tính vào số lần hỏng."""
    inner = _Backend()
    inner.error = ValueError("trùng khoá")
    breaker = _wrap(inner)

    for _ in range(10):
        with pytest.raises(ValueError):
            await breaker.ping()
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_nua_mo_roi_dong_lai_khi_database_song_lai():
    inner = _Backend()
    inner.error = ConnectionRefusedError(111, "refused")
    breaker = _wrap(inner)

    for _ in range(3):
        with pytest.raises(ConnectionRefusedError):
            await breaker.ping()
    assert breaker.state is CircuitState.OPEN

    await asyncio.sleep(0.25)          # hết thời gian nghỉ
    inner.error = None
    assert await breaker.ping() is True
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_nua_mo_ma_van_hong_thi_mo_lai_ngay():
    inner = _Backend()
    inner.error = ConnectionRefusedError(111, "refused")
    breaker = _wrap(inner)

    for _ in range(3):
        with pytest.raises(ConnectionRefusedError):
            await breaker.ping()

    await asyncio.sleep(0.25)
    with pytest.raises(ConnectionRefusedError):
        await breaker.ping()           # phép thử ở trạng thái nửa mở
    assert breaker.state is CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        await breaker.ping()


@pytest.mark.asyncio
async def test_han_thoi_gian_cat_loi_goi_treo():
    inner = _Backend()
    inner.delay = 5.0
    breaker = _wrap(inner, call_timeout_seconds=0.1)

    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await breaker.ping()
    assert asyncio.get_running_loop().time() - started < 1.0


@pytest.mark.asyncio
async def test_han_thoi_gian_van_ap_dung_khi_tat_breaker():
    """Tắt breaker chỉ tắt phần ngắt mạch, KHÔNG được tắt hạn thời gian."""
    inner = _Backend()
    inner.delay = 5.0
    breaker = _wrap(inner, call_timeout_seconds=0.1, breaker_enabled=False)

    with pytest.raises(TimeoutError):
        await breaker.ping()
    assert breaker.state is CircuitState.CLOSED, "tắt breaker thì mạch không đổi trạng thái"


@pytest.mark.asyncio
async def test_qua_han_nhieu_lan_cung_lam_ngat_mach():
    inner = _Backend()
    inner.delay = 5.0
    breaker = _wrap(inner, failure_threshold=2, call_timeout_seconds=0.05)

    for _ in range(2):
        with pytest.raises(TimeoutError):
            await breaker.ping()
    assert breaker.state is CircuitState.OPEN


def test_memory_khong_bi_boc():
    """Backend memory không đi qua mạng nên không cần ngắt mạch."""
    from pymodular.core.config import DatabaseSettings
    from pymodular.infrastructure.database.factory import create_backend

    backend = create_backend(DatabaseSettings(driver="memory"))
    assert not isinstance(backend, CircuitBreakerBackend)
