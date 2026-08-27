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

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Generic, TypeVar

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.providers import CapabilityNotSupportedError
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    Transaction,
    check_changes,
    is_transient_error,
    mapping_for,
)
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.query import Query

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

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Gộp nhiều thao tác ghi thành một: cùng thành công, hoặc cùng không.

            async with self._db.transaction():
                camera = await self._cameras.save(Camera(id="", name=ten))
                await self._logs.save(CameraLog(id="", camera_id=camera.id))

        Ghi bảng thứ hai hỏng thì bảng thứ nhất cũng bị huỷ — không còn camera
        mồ côi. Mọi repository trong khối đều đi chung một connection, nên gọi
        `transaction()` từ đâu cũng bao trùm tất cả.

        **Trong HTTP handler thì đã có sẵn một transaction cho cả request**, nên
        chỉ cần dùng khối này khi bạn muốn huỷ một PHẦN mà vẫn chạy tiếp:

            try:
                async with self._db.transaction():   # SAVEPOINT
                    ...
            except Exception:
                ...                                  # request vẫn tiếp tục

        Ngoài request — worker, job, cron, script — thì KHÔNG có sẵn gì cả: mỗi
        `save()` tự commit ngay. Ở đó bắt buộc phải bọc nếu cần nguyên tử.

        Muốn huỷ giữa chừng mà KHÔNG ném lỗi ra ngoài thì lấy tay cầm:

            async with self._db.transaction() as tx:
                await repo.save(...)
                if không_hợp_lệ:
                    await tx.rollback()      # thoát khối, không ném lỗi

        MongoDB một node không có transaction đa-document nên khối này ném lỗi
        nói rõ, thay vì chạy tiếp rồi để lại dữ liệu nửa vời.
        """
        tx = getattr(self._backend, "transaction", None)
        if tx is None:
            raise CapabilityNotSupportedError(
                f"Backend {self._backend.name!r} chưa có transaction."
            )
        async with tx() as handle:
            yield handle


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

    def query(self) -> Query[E]:
        """Builder cho truy vấn có JOIN, so sánh lớn/bé, và lọc NULL.

        `find()` chỉ so bằng. Khi cần hơn thế — và khi cần điều kiện chạy DƯỚI
        database chứ không lọc bằng Python như `match=` — thì dùng cái này:

            await (repo.query()
                   .join(Camera, on="camera_id")
                   .where(score__gte=0.8, deleted_at__isnull=True)
                   .order_by("-created_at").limit(20).all())
        """
        return Query(self._entity, self._db)

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

    async def update(
        self,
        where: str | dict[str, Any],
        changes: dict[str, Any] | None = None,
        *,
        match: Callable[[E], bool] | None = None,
        **set_fields: Any,
    ) -> int:
        """Sửa thẳng dưới database, KHÔNG đọc bản ghi về trước. Trả về số dòng khớp.

        Thay cho vòng ba bước quen thuộc:

            item = await repo.get(camera_id)        # 1 lượt đi database
            item.status = "offline"
            await repo.save(item)                   # 1 lượt nữa

        chỉ còn một dòng, và một lượt đi database:

            await repo.update(camera_id, status="offline")

        `where` nhận **id** (chuỗi) hoặc **dict điều kiện** — so bằng, trên bất
        kỳ trường nào, và sửa MỌI dòng khớp:

            await repo.update("cam-01", {"name": "Cổng chính"})   # theo id
            await repo.update({"zone": "Tầng 1"}, status="off")   # theo cột khác
            await repo.update({"zone": "T1", "status": "on"}, threshold=0.9)

        Giá trị cần ghi truyền bằng dict (tham số thứ hai) hay bằng kwargs đều
        được; hai cách gộp lại nếu dùng cả hai. Thứ tự tham số lấy đúng của
        TypeORM (`repo.update(criteria, partialEntity)`) cho người từ NestJS
        sang đỡ phải nhớ thêm.

        `updated_at` tự đóng dấu, y như `save()` — trừ khi bạn tự đặt nó.

        Ba điều phải biết:

        - **Không đọc về nên không chạy được logic trong Python.** Cần đọc giá
          trị cũ để tính giá trị mới (`so_lan += 1`) thì đây không phải chỗ:
          `update` chỉ GHI ĐÈ. Tăng giảm nguyên tử chưa có, dùng `.query()` với
          `db.transaction()`.
        - **Không đổi được `id`** — nó là danh tính bản ghi và là thứ khoá ngoại
          của bảng khác đang trỏ tới.
        - **`where` rỗng bị chặn**, vì gần như luôn là lỗi lập trình chứ không
          phải ý định sửa cả bảng. Thật sự muốn sửa hết thì nói rõ:
          `match=lambda _: True`.

        `match=` lọc bằng Python nên phải đọc dòng về trước — dùng khi điều kiện
        không viết được bằng phép so bằng.
        """
        filters = {"id": where} if isinstance(where, str) else dict(where)
        values: dict[str, Any] = {**(changes or {}), **set_fields}

        if not filters and match is None:
            raise BadRequestError(
                f"`update` trên {self._entity.__name__} không có điều kiện nào — "
                f"câu lệnh này sẽ sửa MỌI dòng. Truyền id, hoặc "
                f"`{{'ten_cot': gia_tri}}`. Cố ý sửa cả bảng thì nói rõ bằng "
                f"`match=lambda _: True`."
            )
        if not values:
            raise BadRequestError(
                f"`update` trên {self._entity.__name__} không có giá trị nào để ghi. "
                f"Truyền `{{'ten_cot': gia_tri}}` hoặc `ten_cot=gia_tri`."
            )
        if "updated_at" not in values and "updated_at" in mapping_for(self._entity).fields:
            # Đóng dấu ở đây chứ không ở service, cùng lý do với `save()`: mọi
            # đường ghi đều đi qua repository nên không có chỗ nào quên.
            values["updated_at"] = utcnow()

        return await self._backend.update_where(
            self._entity,
            filters=filters,
            changes=check_changes(self._entity, values),
            match=match,
        )

    async def delete(self, id_: str) -> bool:
        return await self._backend.delete(self._entity, id_)

    async def delete_where(
        self, *, match: Callable[[E], bool] | None = None, **equals: Any
    ) -> int:
        return await self._backend.delete_where(self._entity, filters=equals, match=match)

    def __repr__(self) -> str:
        return f"<Repository[{self._entity.__name__}] on {self._db.driver}>"
