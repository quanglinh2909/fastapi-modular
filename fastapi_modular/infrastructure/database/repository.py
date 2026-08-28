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

import dataclasses
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

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
    check_lengths,
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


def _as_dict(value: Any, *, vai_tro: str) -> dict[str, Any]:
    """dict hoặc DTO pydantic -> dict. DTO thì chỉ lấy field client THỰC SỰ gửi.

    `exclude_unset=True` là mấu chốt, giống hệt `apply_changes`: nó phân biệt
    "không gửi field này" với "gửi field này = null". Thiếu nó thì PATCH đổi một
    field sẽ ghi `None` đè lên mọi field còn lại — mất dữ liệu, và không ai báo.
    """
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_unset=True)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        raise BadRequestError(
            f"`update` nhận dict hoặc DTO pydantic làm {vai_tro}, không nhận entity. "
            f"Đã có sẵn cả bản ghi thì dùng `save(obj)`."
        )
    raise BadRequestError(
        f"`update` không hiểu {vai_tro} kiểu {type(value).__name__}. "
        f"Truyền dict, hoặc DTO pydantic."
    )


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
        check_lengths(self._entity, obj)
        return await self._backend.save(self._entity, obj)

    async def update(
        self,
        id_: str,
        changes: dict[str, Any] | BaseModel | None = None,
        **set_fields: Any,
    ) -> E | None:
        """Sửa MỘT bản ghi theo id và trả về chính nó sau khi sửa. `None` = không có id đó.

        Thay cho vòng ba bước quen thuộc:

            item = await repo.get(camera_id)        # 1 lượt đi database
            item.status = "offline"
            await repo.save(item)                   # 1 lượt nữa

        chỉ còn một dòng, và **một** lượt đi database — `UPDATE ... RETURNING *`
        với SQL, `find_one_and_update` với Mongo:

            cam = await repo.update(camera_id, status="offline")

        DTO truyền thẳng vào được, không phải `model_dump()`:

            @patch("/{camera_id}")
            async def update(self, camera_id: str, payload: CameraUpdate) -> CameraOut:
                cam = await self._service.update(camera_id, payload)
                ...

        DTO được đọc bằng `exclude_unset=True`, y như `apply_changes`: chỉ field
        client THỰC SỰ gửi lên mới được ghi. Đây là chỗ phải cẩn thận — dùng
        `model_dump()` trần thì mọi field không gửi sẽ thành `None` và ghi đè
        sạch dữ liệu cũ. Gửi `null` tường minh vẫn xoá được cột, vì `null` đã
        gửi là đã "set".

        Giá trị truyền bằng dict, kwargs, hay DTO đều được; gộp lại nếu dùng
        nhiều hơn một. `updated_at` tự đóng dấu, y như `save()`.

        Bản trả về đọc từ DATABASE sau khi ghi, không phải bản trong bộ nhớ —
        database có thể tự đổi thêm (giá trị mặc định, trigger), và khi ấy thứ
        trả cho client phải là thứ đang thật sự nằm trong bảng.

        Hai điều dễ vấp:

        - **Không đọc giá trị cũ được.** `update` chỉ GHI ĐÈ, nên `so_lan += 1`
          không viết bằng nó — đọc rồi ghi trong `async with db.transaction():`.
        - **Không đổi được `id`** — nó là danh tính bản ghi và là thứ khoá ngoại
          của bảng khác đang trỏ tới.

        Sửa NHIỀU dòng theo điều kiện thì dùng `update_where(...)`.
        """
        if not isinstance(id_, str):
            raise BadRequestError(
                f"`update` sửa MỘT bản ghi theo id nên tham số đầu phải là chuỗi "
                f"(đang là {type(id_).__name__}). Sửa nhiều dòng theo điều kiện "
                f"thì dùng `update_where(...)`."
            )
        return await self._backend.update_one(
            self._entity, id_=id_, changes=self._changes(changes, set_fields)
        )

    async def update_where(
        self,
        where: dict[str, Any] | BaseModel,
        changes: dict[str, Any] | BaseModel | None = None,
        *,
        match: Callable[[E], bool] | None = None,
        **set_fields: Any,
    ) -> int:
        """Sửa MỌI bản ghi khớp điều kiện. Trả về số dòng khớp.

            # mọi camera ở Tầng 1 chuyển sang offline
            so_dong = await cameras.update_where({"zone": "Tầng 1"}, status="offline")

            # nhiều điều kiện = AND
            await cameras.update_where({"zone": "T1", "status": "online"}, threshold=0.9)

        Điều kiện chỉ so BẰNG, trên bất kỳ trường nào; `where` nhận cả DTO (hợp
        với bộ lọc sinh bằng `partial_of(...)`). Cần `>=`, `LIKE`, `IN` thì lọc
        bằng `.query()` rồi `update` theo từng id, hoặc truyền `match=` (lọc
        bằng Python nên phải đọc dòng về trước).

        **Trả về số dòng chứ không trả dữ liệu**, cố ý: một câu lệnh có thể khớp
        hàng trăm nghìn dòng, và đọc hết chúng về chỉ để trả cho người gọi là
        thứ không ai muốn xảy ra ngầm. Cần dữ liệu thì `find(...)` sau đó.

        **`where` rỗng bị chặn**, vì gần như luôn là lỗi lập trình chứ không
        phải ý định sửa cả bảng. Thật sự muốn sửa hết thì nói rõ:
        `match=lambda _: True`.
        """
        if isinstance(where, str):
            raise BadRequestError(
                "`update_where` nhận điều kiện dạng dict hoặc DTO. Sửa một bản "
                'ghi theo id thì dùng `update(id, ...)` — nó trả về chính bản '
                "ghi đã sửa."
            )
        filters = _as_dict(where, vai_tro="điều kiện")
        if not filters and match is None:
            raise BadRequestError(
                f"`update_where` trên {self._entity.__name__} không có điều kiện nào — "
                f"câu lệnh này sẽ sửa MỌI dòng. Truyền `{{'ten_cot': gia_tri}}`, "
                f"hoặc nói rõ ý định bằng `match=lambda _: True`."
            )
        return await self._backend.update_where(
            self._entity,
            filters=filters,
            changes=self._changes(changes, set_fields),
            match=match,
        )

    def _changes(self, changes: Any, set_fields: dict[str, Any]) -> dict[str, Any]:
        """Gộp dict/DTO/kwargs thành bộ giá trị đã soi, có đóng dấu `updated_at`."""
        values: dict[str, Any] = {
            **(_as_dict(changes, vai_tro="giá trị") if changes is not None else {}),
            **set_fields,
        }
        if not values:
            them = (
                " DTO không có field nào được gửi lên — `exclude_unset` bỏ hết."
                if isinstance(changes, BaseModel)
                else ""
            )
            raise BadRequestError(
                f"`update` trên {self._entity.__name__} không có giá trị nào để ghi."
                f"{them} Truyền `{{'ten_cot': gia_tri}}` hoặc `ten_cot=gia_tri`."
            )
        if "updated_at" not in values and "updated_at" in mapping_for(self._entity).fields:
            # Đóng dấu ở đây chứ không ở service, cùng lý do với `save()`: mọi
            # đường ghi đều đi qua repository nên không có chỗ nào quên.
            values["updated_at"] = utcnow()
        return check_changes(self._entity, values)

    async def delete(self, id_: str) -> bool:
        return await self._backend.delete(self._entity, id_)

    async def delete_where(
        self, *, match: Callable[[E], bool] | None = None, **equals: Any
    ) -> int:
        return await self._backend.delete_where(self._entity, filters=equals, match=match)

    def __repr__(self) -> str:
        return f"<Repository[{self._entity.__name__}] on {self._db.driver}>"
