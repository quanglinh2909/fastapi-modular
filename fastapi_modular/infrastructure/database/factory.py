"""Chọn backend theo cấu hình, và chỉ import thư viện của driver được chọn.

Đây là điểm mấu chốt cho yêu cầu "dùng Postgres thì không cần cài thư viện của
SQLite/Mongo": mọi `import sqlalchemy` / `import motor` đều nằm TRONG hàm, nên
chúng chỉ chạy khi driver tương ứng được chọn. Thiếu thư viện sẽ báo lỗi nói rõ
cần chạy lệnh make nào, thay vì ImportError lúc khởi động.
"""

from __future__ import annotations

from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.infrastructure.database.base import DatabaseBackend

_INSTALL_HINT = {
    "sqlite": "pip install \'fastapi-modular[sqlite]\'",
    "postgres": "pip install \'fastapi-modular[postgres]\'",
    "mongodb": "pip install \'fastapi-modular[mongodb]\'",
}


def _missing(driver: str, package: str) -> RuntimeError:
    install_hint = _INSTALL_HINT.get(driver, "pip install 'fastapi-modular[<driver>]'")
    return RuntimeError(
        f"Driver database '{driver}' cần thư viện '{package}' nhưng chưa cài. "
        f"Chạy: {install_hint}"
    )


def _wrap(settings: DatabaseSettings, backend: DatabaseBackend) -> DatabaseBackend:
    """Bọc backend bằng lớp hạn-thời-gian + circuit breaker.

    Luôn bọc, kể cả khi tắt circuit breaker: phần hạn thời gian là bắt buộc,
    vì timeout của từng driver không phủ được trường hợp database treo giữa
    chừng, và khi đó request sẽ giữ chỗ worker vô thời hạn.
    """
    from fastapi_modular.infrastructure.database.circuit import CircuitBreakerBackend

    return CircuitBreakerBackend(
        backend,
        failure_threshold=settings.circuit_failure_threshold,
        reset_seconds=settings.circuit_reset_seconds,
        call_timeout_seconds=settings.query_timeout_seconds,
        breaker_enabled=settings.circuit_breaker,
    )


def create_backend(settings: DatabaseSettings) -> DatabaseBackend:
    driver = settings.driver

    if driver == "memory":
        from fastapi_modular.infrastructure.database.memory import MemoryBackend

        # Backend memory không đi qua mạng nên không có gì để ngắt.
        return MemoryBackend()

    if driver in ("sqlite", "postgres"):
        try:
            from fastapi_modular.infrastructure.database.sql import SqlBackend
        except ModuleNotFoundError as exc:
            raise _missing(driver, exc.name or "sqlalchemy") from exc

        return _wrap(settings, SqlBackend(
            settings.resolved_dsn,
            echo=settings.echo,
            schema_mode=settings.schema_mode,
            drop_columns=settings.drop_columns,
            pool_pre_ping=settings.pool_pre_ping,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_recycle_seconds=settings.pool_recycle_seconds,
            connect_timeout_seconds=settings.connect_timeout_seconds,
            query_timeout_seconds=settings.query_timeout_seconds,
        ))

    if driver == "mongodb":
        try:
            from fastapi_modular.infrastructure.database.mongo import MongoBackend
        except ModuleNotFoundError as exc:
            raise _missing(driver, exc.name or "motor") from exc

        return _wrap(settings, MongoBackend(
            settings.resolved_dsn,
            database=settings.name,
            connect_timeout_seconds=settings.connect_timeout_seconds,
            query_timeout_seconds=settings.query_timeout_seconds,
        ))

    raise RuntimeError(f"Driver database không hỗ trợ: {driver!r}")
