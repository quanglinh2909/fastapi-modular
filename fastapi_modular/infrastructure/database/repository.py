"""`Database` (kết nối) và `Repository[E]` (kho dữ liệu của một entity).

Service chỉ khai báo `repo: Repository[User]` là có sẵn CRUD, giống
`Repository<Camera>` của TypeORM trong NestJS. Repository không biết đang chạy
trên memory, SQL hay Mongo — nó chuyển tiếp xuống backend do `Database` chọn
theo cấu hình.

Quy ước lọc: tham số `**equals` có giá trị None đều bị BỎ QUA (coi như "không
lọc theo trường này"). Cần điều kiện phức tạp hơn bằng nhau thì dùng `match=`
với một predicate — lưu ý predicate chạy trong Python nên KHÔNG đẩy được
xuống SQL/Mongo (backend sẽ lấy dữ liệu về rồi mới lọc).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.database.base import DatabaseBackend, is_transient_error
from fastapi_modular.infrastructure.database.factory import create_backend

log = get_logger(__name__)

E = TypeVar("E")


@injectable
class Database:
    """Sở hữu kết nối tới database đang được cấu hình."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._backend: DatabaseBackend = create_backend(settings.db)

    @property
    def backend(self) -> DatabaseBackend:
        return self._backend

    @property
    def driver(self) -> str:
        return self._backend.name

    async def startup(self, *entities: type) -> None:
        await self._backend.startup()
        await self._wait_until_reachable()

        create_schema = getattr(self._backend, "create_schema", None)
        if create_schema is not None and entities:
            await create_schema(*entities)

    async def _wait_until_reachable(self) -> None:
        """Thử ping vài lần trước khi bỏ cuộc.

        Hay gặp với docker compose: app khởi động xong trước khi database sẵn
        sàng nhận kết nối. Thử lại vài giây rẻ hơn nhiều so với để container
        chết rồi chờ orchestrator dựng lại.
        """
        import asyncio

        attempts = max(1, self._settings.db.startup_retries)
        delay = self._settings.db.startup_retry_delay_seconds

        for attempt in range(1, attempts + 1):
            try:
                await self._backend.ping()
                if attempt > 1:
                    log.info("db.connected_after_retry", backend=self._backend.name,
                             attempts=attempt)
                return
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"

                # Sai mật khẩu / sai tên database thì thử lại vô ích, chỉ làm
                # chậm lúc phát hiện cấu hình sai. Dừng ngay.
                if not is_transient_error(exc):
                    log.error(
                        "db.config_error_at_startup",
                        backend=self._backend.name,
                        error=reason,
                        hint="lỗi này không tự hết khi thử lại — kiểm tra APP_DB__DSN",
                    )
                    raise

                if attempt == attempts:
                    log.error(
                        "db.unreachable_at_startup",
                        backend=self._backend.name,
                        attempts=attempts,
                        error=reason,
                    )
                    raise

                log.warning(
                    "db.retry_connect",
                    backend=self._backend.name,
                    attempt=attempt,
                    of=attempts,
                    retry_in=delay,
                    error=reason,          # nói luôn lý do, đừng bắt chờ tới lần cuối
                )
                await asyncio.sleep(delay)

    async def shutdown(self) -> None:
        await self._backend.shutdown()

    async def ping(self) -> bool:
        return await self._backend.ping()


@injectable
class Repository(Generic[E]):
    """CRUD sẵn có cho một entity, bất kể backend nào bên dưới."""

    def __init__(self, entity: type[E], database: Database) -> None:
        self._entity = entity
        self._db = database

    @property
    def _backend(self) -> DatabaseBackend:
        return self._db.backend

    # ------------------------------------------------------------------ đọc
    async def get(self, id_: str) -> E | None:
        return await self._backend.get(self._entity, id_)

    async def find(
        self,
        *,
        match: Callable[[E], bool] | None = None,
        order_by: str | None = "created_at",
        limit: int | None = None,
        offset: int = 0,
        **equals: Any,
    ) -> list[E]:
        return await self._backend.find(
            self._entity,
            filters=equals,
            match=match,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    async def find_one(
        self, *, match: Callable[[E], bool] | None = None, **equals: Any
    ) -> E | None:
        return await self._backend.find_one(self._entity, filters=equals, match=match)

    async def count(
        self, *, match: Callable[[E], bool] | None = None, **equals: Any
    ) -> int:
        return await self._backend.count(self._entity, filters=equals, match=match)

    async def exists(
        self, *, match: Callable[[E], bool] | None = None, **equals: Any
    ) -> bool:
        return await self.find_one(match=match, **equals) is not None

    # ------------------------------------------------------------------ ghi
    async def save(self, obj: E) -> E:
        """Upsert. Tự sinh id nếu entity chưa có, và tự đóng dấu `updated_at`.

        Đóng dấu ở đây chứ không ở service: mọi đường ghi đều đi qua save(),
        nên không có chỗ nào quên. Tương đương @UpdateDateColumn của TypeORM.
        Entity không có trường `updated_at` thì bỏ qua.
        """
        if hasattr(obj, "updated_at"):
            # Bản ghi mới: cho updated_at trùng created_at thay vì lệch vài
            # micro giây, để "chưa từng sửa" nhận ra được bằng created == updated.
            is_new = not getattr(obj, "id", None)
            created = getattr(obj, "created_at", None)
            obj.updated_at = created if (is_new and created is not None) else utcnow()  # type: ignore[attr-defined]
        return await self._backend.save(self._entity, obj)

    async def delete(self, id_: str) -> bool:
        return await self._backend.delete(self._entity, id_)

    async def delete_where(
        self, *, match: Callable[[E], bool] | None = None, **equals: Any
    ) -> int:
        return await self._backend.delete_where(self._entity, filters=equals, match=match)

    def __repr__(self) -> str:
        return f"<Repository[{self._entity.__name__}] on {self._db.driver}>"
