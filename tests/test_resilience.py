"""Test hành vi khi database rớt kết nối.

Phần map lỗi -> HTTP chạy với backend giả nên không cần server thật.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.core.container import container
from fastapi_modular.infrastructure.database.repository import Database


class _BrokenBackend:
    """Backend luôn ném lỗi mất kết nối — dùng cho test lúc khởi động."""

    name = "broken"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def ping(self) -> bool:
        raise self._error

    def __getattr__(self, _name):
        async def _raise(*args, **kwargs):
            raise self._error

        return _raise


class _FlakyBackend:
    """Chạy bình thường cho tới khi bị "rút dây" bằng cách gán `.error`.

    Cần vậy vì app phải khởi động được trước đã; rớt kết nối giữa chừng mới là
    tình huống muốn kiểm tra.
    """

    name = "flaky"

    def __init__(self) -> None:
        from fastapi_modular.infrastructure.database.memory import MemoryBackend

        self._real = MemoryBackend()
        self.error: Exception | None = None

    async def startup(self) -> None:
        await self._real.startup()

    async def shutdown(self) -> None:
        await self._real.shutdown()

    async def ping(self) -> bool:
        if self.error is not None:
            raise self.error
        return await self._real.ping()

    def __getattr__(self, name: str):
        # Lấy trước để tên nào backend thật không có thì ném AttributeError như
        # thường; nếu trả bừa một callable, getattr(backend, "create_schema",
        # None) sẽ không còn là None và Database.startup() gọi nhầm.
        target = getattr(self._real, name)

        async def _call(*args, **kwargs):
            if self.error is not None:
                raise self.error
            return await target(*args, **kwargs)

        return _call


@contextmanager
def client_that_loses_db(error: Exception):
    """Mở app khoẻ mạnh, rồi ngắt database ngay trước khi gọi request."""
    from fastapi.testclient import TestClient

    from fastapi_modular.factory import create_app

    settings = Settings(
        APP_DEBUG=False,
        APP_DB=DatabaseSettings(driver="memory", startup_retries=1),
    )
    app = create_app(settings)

    database = Database(settings)
    backend = _FlakyBackend()
    database._backend = backend
    container.override(Database, database)

    with TestClient(app) as client:
        backend.error = error          # "rút dây" sau khi app đã lên
        yield client


def test_mat_ket_noi_tra_503_chu_khong_phai_500():
    """Database chết là lỗi vận hành: client nên biết là thử lại được."""
    with client_that_loses_db(ConnectionRefusedError(111, "Connection refused")) as client:
        response = client.get("/api/users")
    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"


def test_readiness_bao_503_khi_db_chet():
    with client_that_loses_db(ConnectionRefusedError(111, "Connection refused")) as client:
        response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["database"] is False


def test_liveness_khong_phu_thuoc_database():
    """/health phải xanh cả khi database chết, nếu không orchestrator sẽ giết
    tiến trình đang khoẻ chỉ vì database tạm rớt."""
    with client_that_loses_db(ConnectionRefusedError(111, "Connection refused")) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_khong_lo_chi_tiet_khi_debug_tat():
    with client_that_loses_db(ConnectionRefusedError(111, "bí mật nội bộ")) as client:
        body = client.get("/api/users").json()
    assert "details" not in body
    assert "bí mật" not in str(body)


@pytest.mark.asyncio
async def test_thu_lai_khi_khoi_dong_roi_bo_cuoc():
    settings = Settings(
        APP_DB=DatabaseSettings(
            driver="memory", startup_retries=3, startup_retry_delay_seconds=0.01
        )
    )
    database = Database(settings)
    database._backend = _BrokenBackend(ConnectionRefusedError(111, "refused"))

    with pytest.raises(ConnectionRefusedError):
        await database.startup()


@pytest.mark.asyncio
async def test_thu_lai_thanh_cong_thi_khoi_dong_binh_thuong():
    class _SlowBackend(_BrokenBackend):
        def __init__(self) -> None:
            super().__init__(ConnectionRefusedError(111, "refused"))
            self.attempts = 0

        async def ping(self) -> bool:
            self.attempts += 1
            if self.attempts < 3:
                raise self._error
            return True

    settings = Settings(
        APP_DB=DatabaseSettings(
            driver="memory", startup_retries=5, startup_retry_delay_seconds=0.01
        )
    )
    database = Database(settings)
    backend = _SlowBackend()
    database._backend = backend

    await database.startup()
    assert backend.attempts == 3, "phải thử lại đến khi database sẵn sàng"


def test_pool_pre_ping_bat_mac_dinh():
    """Không bật thì mỗi lần database restart sẽ có 1 request lỗi."""
    assert DatabaseSettings().pool_pre_ping is True


# ---------------------------------------------------------------- phân loại lỗi
class _FakeAuthError(Exception):
    """Giả lập asyncpg.InvalidPasswordError."""


class ServerSelectionTimeoutError(Exception):
    """Giả lập pymongo — phân loại theo TÊN lớp nên không cần import driver."""


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        (ConnectionRefusedError(111, "refused"), True),
        (ConnectionResetError(104, "reset"), True),
        (TimeoutError(), True),
        (ServerSelectionTimeoutError("mongo chưa chọn được server"), True),
        (_FakeAuthError("password authentication failed"), False),
        (ValueError("DSN sai cú pháp"), False),
    ],
)
def test_phan_loai_loi_tam_thoi(exc, retryable):
    from fastapi_modular.infrastructure.database.base import is_transient_error

    assert is_transient_error(exc) is retryable


def test_loi_bi_boc_van_nhan_ra_duoc():
    """Driver hay bọc lỗi gốc lại; phải duyệt cả chuỗi __cause__."""
    from fastapi_modular.infrastructure.database.base import is_transient_error

    wrapped = RuntimeError("bọc ngoài")
    wrapped.__cause__ = ConnectionRefusedError(111, "refused")
    assert is_transient_error(wrapped) is True


@pytest.mark.asyncio
async def test_loi_cau_hinh_khong_thu_lai():
    """Sai mật khẩu thì thử lại vô ích — phải dừng ngay ở lần đầu."""
    backend = _BrokenBackend(_FakeAuthError("password authentication failed"))
    settings = Settings(
        APP_DB=DatabaseSettings(
            driver="memory", startup_retries=10, startup_retry_delay_seconds=10
        )
    )
    database = Database(settings)
    database._backend = backend

    import time

    started = time.perf_counter()
    with pytest.raises(_FakeAuthError):
        await database.startup()
    assert time.perf_counter() - started < 1, "không được ngồi retry lỗi cấu hình"
