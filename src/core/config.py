"""Cấu hình RIÊNG của ứng dụng này — ví dụ cách thêm biến vào .env.

Khung không biết gì về `APP_JWT__*` hay `APP_TEAM_NAME`; chúng là của bạn. Kế
thừa `Settings` là đủ để pydantic-settings đọc chúng từ `.env` hoặc biến môi
trường, theo đúng quy tắc sẵn có: biến môi trường thắng `.env`, `.env` thắng giá
trị mặc định, nhóm lồng nhau ngăn bằng `__`.

    APP_TEAM_NAME=to-backend
    APP_JWT__SECRET=doi-cai-nay-di
    APP_JWT__TTL_SECONDS=7200
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi_modular import Settings


class JwtSettings(BaseModel):
    """Nhóm lồng nhau -> đặt bằng APP_JWT__<TÊN TRƯỜNG>."""

    secret: str = ""
    ttl_seconds: int = 3600


class AppSettings(Settings):
    """Settings của khung, cộng thêm phần của ứng dụng."""

    team_name: str = Field(default="chua-dat", alias="APP_TEAM_NAME")
    jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")
