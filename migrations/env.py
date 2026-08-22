"""Cấu hình Alembic cho template.

Hai điểm khác bản mẫu của Alembic:

1. DSN lấy từ `Settings` (tức từ .env), không viết trong alembic.ini — nhờ vậy
   migration và app luôn chạy trên cùng một database, không sợ lệch.

2. `target_metadata` dựng từ chính các dataclass entity qua `build_metadata()`,
   nên `--autogenerate` so được entity với database mà entity vẫn không dính ORM.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import AppSettings
from fastapi_modular.core.config import get_settings, use_settings
from fastapi_modular.core.container import _ENTITIES
from fastapi_modular.discovery import load_all_modules
from fastapi_modular.infrastructure.database.sql import build_metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic không đi qua create_app(), nên phải tự nói dùng lớp cấu hình nào.
# Dự án không có Settings riêng thì xoá hai dòng này.
use_settings(AppSettings)

settings = get_settings()
if settings.db.driver not in ("sqlite", "postgres"):
    raise SystemExit(
        f"Alembic chỉ dùng cho SQL, còn APP_DB__DRIVER đang là '{settings.db.driver}'. "
        "MongoDB không có schema cố định nên không cần migration."
    )

load_all_modules()                      # để mọi @entity kịp đăng ký
target_metadata = build_metadata(*_ENTITIES.values())


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,              # phát hiện cả đổi kiểu cột
        render_as_batch=settings.db.driver == "sqlite",  # SQLite cần batch mode
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Sinh câu SQL ra màn hình thay vì chạy — dùng khi cần DBA duyệt trước."""
    context.configure(
        url=settings.db.resolved_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.db.resolved_dsn, poolclass=None)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
