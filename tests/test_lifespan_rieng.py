"""Ứng dụng phải chèn được việc riêng vào vòng đời, đúng thứ tự.

Thứ tự là cả vấn đề: việc khởi động của bạn phải chạy SAU khi database mở, và
việc lúc tắt phải chạy TRƯỚC khi database đóng — không thì bạn ghi nốt sổ vào
một kết nối đã chết.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_modular import bind_settings, new_fastapi, register_routes
from fastapi_modular import lifespan as framework_lifespan
from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.infrastructure.database import Database

NHAT_KY: list[str] = []


@asynccontextmanager
async def lifespan_cua_toi(app: FastAPI) -> AsyncIterator[None]:
    async with framework_lifespan(app):
        NHAT_KY.append("toi-khoi-dong")
        # Database đã sẵn sàng ở đây — đó là điểm của việc bọc thay vì thay thế.
        await app.state.database.ping()
        NHAT_KY.append("database-dung-duoc")
        try:
            yield
        finally:
            NHAT_KY.append("toi-tat")
            await app.state.database.ping()
            NHAT_KY.append("database-van-con-luc-tat")


def _dung_app() -> FastAPI:
    settings = bind_settings(Settings(APP_DB=DatabaseSettings(driver="memory")))
    app = new_fastapi(settings, lifespan=lifespan_cua_toi)
    register_routes(app, prefix=settings.api_prefix)
    return app


def test_viec_rieng_chay_dung_thu_tu():
    NHAT_KY.clear()
    with TestClient(_dung_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert NHAT_KY == ["toi-khoi-dong", "database-dung-duoc"]

    assert NHAT_KY == [
        "toi-khoi-dong",
        "database-dung-duoc",
        "toi-tat",
        "database-van-con-luc-tat",
    ]


def test_khung_van_don_dep_sau_khi_viec_rieng_xong():
    """Sau khi thoát, khung phải đã đóng database và xoá state."""
    app = _dung_app()
    with TestClient(app):
        pass
    assert app.state.database is None
    assert app.state.container is None


def test_lifespan_cua_ung_dung_van_la_asynccontextmanager():
    """src/core/lifespan.py sinh ra phải bọc được, không phải thay thế."""
    from src.core.lifespan import lifespan

    assert hasattr(lifespan, "__wrapped__"), "phải là @asynccontextmanager"
    assert lifespan is not framework_lifespan, "đây là file của ứng dụng, không phải của khung"


def test_main_dung_lifespan_cua_ung_dung():
    """src/main.py phải cắm lifespan CỦA ỨNG DỤNG, không phải của khung.

    Kiểm ở mức nguồn: FastAPI gộp lifespan lại thành một hàm mới nên so sánh
    định danh ở runtime không nói lên điều gì.
    """
    nguon = Path("src/main.py").read_text(encoding="utf-8")
    assert "from src.core.lifespan import lifespan" in nguon
    assert "lifespan=lifespan" in nguon


def test_app_that_su_khoi_dong_duoc():
    """Boot nguyên `src/main.py` — chuỗi lifespan lồng nhau phải chạy trót lọt."""
    import src.main

    with TestClient(src.main.app) as client:
        assert client.get("/api/health").status_code == 200
        assert src.main.app.state.database is not None


def test_database_van_lam_viec_duoc_trong_viec_rieng():
    """Không chỉ ping — truy vấn thật cũng phải chạy."""
    NHAT_KY.clear()
    app = _dung_app()
    with TestClient(app) as client:
        r = client.post("/api/users", json={"email": "a@b.c", "full_name": "A"})
        assert r.status_code == 201
        assert isinstance(app.state.database, Database)
