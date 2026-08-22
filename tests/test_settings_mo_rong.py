"""Ứng dụng phải thêm được biến cấu hình của riêng mình.

Khung định nghĩa `Settings` bên trong thư viện; nếu không kế thừa được thì mọi
dự án dùng fastapi-modular đều bị kẹt với đúng những biến khung nghĩ ra sẵn.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from fastapi_modular import Settings, container, create_app, injectable
from fastapi_modular.core.config import get_settings, settings_class, use_settings
from src.core.config import AppSettings


def test_bien_rieng_doc_duoc_tu_moi_truong(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_TEAM_NAME", "to-backend")
    monkeypatch.setenv("APP_JWT__SECRET", "bi-mat")
    monkeypatch.setenv("APP_JWT__TTL_SECONDS", "7200")

    settings = AppSettings()
    assert settings.team_name == "to-backend"
    assert settings.jwt.secret == "bi-mat"
    assert settings.jwt.ttl_seconds == 7200        # nhóm lồng nhau, ngăn bằng __
    assert settings.db.driver == "memory"          # phần của khung vẫn nguyên


def test_mac_dinh_van_chay_khi_khong_dat_gi():
    settings = AppSettings()
    assert settings.team_name == "chua-dat"
    assert settings.jwt.ttl_seconds == 3600


def test_di_tra_ve_cung_mot_doi_tuong_cho_ca_hai_kieu():
    """Service của bạn hỏi AppSettings, code trong khung hỏi Settings."""

    @injectable
    class DichVuCuaToi:
        def __init__(self, settings: AppSettings) -> None:
            self.settings = settings

    create_app(AppSettings(APP_TEAM_NAME="doi-a"))

    cua_toi = container.resolve(DichVuCuaToi)
    assert cua_toi.settings.team_name == "doi-a"
    # Cùng MỘT instance, không phải hai bản cấu hình song song.
    assert cua_toi.settings is container.resolve(Settings)
    assert cua_toi.settings is container.resolve(AppSettings)


def test_create_app_ghi_nho_lop_cho_cho_khac_trong_khung():
    """Alembic và gateway tự gọi get_settings() — phải ra đúng lớp của bạn."""
    goc = settings_class()
    try:
        create_app(AppSettings())
        assert settings_class() is AppSettings
        assert isinstance(get_settings(), AppSettings)
    finally:
        use_settings(goc)


def test_lop_khong_ke_thua_settings_bi_tu_choi():
    class KhongPhaiSettings(BaseModel):
        x: int = 1

    with pytest.raises(TypeError, match="lớp con của Settings"):
        use_settings(KhongPhaiSettings)      # type: ignore[arg-type]


def test_ke_thua_nhieu_tang_van_cam_duoc_vao_container():
    class TangHai(AppSettings):
        extra: str = Field(default="x", alias="APP_EXTRA")

    settings = TangHai()
    create_app(settings)
    for kieu in (Settings, AppSettings, TangHai):
        assert container.resolve(kieu) is settings

    use_settings(AppSettings)                # trả lại cho các test sau


def test_nhom_long_nhau_tu_viet_van_hoat_dong(monkeypatch: pytest.MonkeyPatch):
    class SmtpSettings(BaseModel):
        host: str = "localhost"
        port: int = 25

    class CoSmtp(Settings):
        smtp: SmtpSettings = Field(default_factory=SmtpSettings, alias="APP_SMTP")

    monkeypatch.setenv("APP_SMTP__HOST", "mail.example.com")
    monkeypatch.setenv("APP_SMTP__PORT", "587")
    settings = CoSmtp()
    assert (settings.smtp.host, settings.smtp.port) == ("mail.example.com", 587)


def test_lap_rap_tay_ra_ket_qua_y_het_create_app():
    """`src/main.py` tự lắp ráp phải tương đương `create_app()`.

    Nếu không thì tài liệu đang dạy một đường còn khung chạy một nẻo — và người
    sửa main.py sẽ âm thầm mất một lớp middleware nào đó.
    """
    from fastapi_modular import (
        add_middleware,
        bind_settings,
        configure_logging,
        lifespan,
        new_fastapi,
        register_error_handlers,
        register_routes,
    )

    tu_dong = create_app(AppSettings())

    container.reset()
    settings = bind_settings(AppSettings())
    configure_logging(settings.log)
    bang_tay = new_fastapi(settings, lifespan=lifespan)
    add_middleware(bang_tay, settings)
    register_error_handlers(bang_tay, debug=settings.debug)
    register_routes(bang_tay, prefix=settings.api_prefix)

    def duong_dan(app) -> set[str]:
        return {getattr(r, "path", None) for r in app.routes} - {None, "/"}

    assert duong_dan(bang_tay) == duong_dan(tu_dong)
    assert [m.cls.__name__ for m in bang_tay.user_middleware] == [
        m.cls.__name__ for m in tu_dong.user_middleware
    ]
    assert set(bang_tay.exception_handlers) == set(tu_dong.exception_handlers)
