"""Subscriber theo entity — bản của `EntitySubscriberInterface` (TypeORM / NestJS).

    # src/api/cameras/camera_subscriber.py
    @injectable
    @entity_subscriber(Camera)          # bỏ tham số = nghe MỌI entity (listenTo)
    class CameraSubscriber:
        async def after_insert(self, event: EntityEvent) -> None:
            ...

Giống TypeORM ở những chỗ quan trọng nhất, và cố ý giống:

- **Khai như một provider bình thường**, `@injectable`, khung tự quét — đúng
  cách NestJS đăng ký subscriber vào `providers`.
- **`entity_subscriber(X)` = `listenTo()`**, bỏ trống là nghe mọi entity.
- **Handler chạy TRONG cùng transaction với lời ghi**, và được `await` ngay tại
  chỗ. Handler ném lỗi thì lời ghi hỏng theo và transaction rollback — TypeORM
  cũng vậy, vì handler chạy trên chính `queryRunner` của thao tác đó.
- **Subscriber là singleton**, không dùng được `Scope.REQUEST`. Docs NestJS nói
  thẳng: "Event subscribers can not be request-scoped".

Ba chỗ KHÔNG giống được, và khung báo lỗi ngay lúc khởi động thay vì để handler
nằm im mãi mãi (xem `UNSUPPORTED`):

- `before_soft_remove` / `after_soft_remove` / `before_recover` / `after_recover`
  — khung không có xoá mềm, không có recover.
- `before_transaction_*` / `after_transaction_*` — MongoDB ở đây không có
  transaction, nên sự kiện sẽ chạy ở backend này mà không chạy ở backend kia.
- `before_query` / `after_query` — backend `memory` không có câu lệnh nào để mà
  nghe.

Và một khác biệt về ngữ nghĩa, ghi ở đây để khỏi ai đoán: TypeORM theo dõi
"dirty" nên `@BeforeUpdate` chỉ chạy khi model thật sự đổi. Khung này không theo
dõi dirty, nên `before_update`/`after_update` chạy MỖI lần `save()` trên bản ghi
đã có. Muốn biết đổi gì thì đọc `event.updated_columns`; rỗng nghĩa là không có
trường nào khác bản dưới database.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi_modular.core.container import _REGISTRY, container
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)

_MARKER = "__entity_subscriber__"

#: Tên method được hỗ trợ, đặt theo đúng TypeORM (`remove`, không phải `delete`,
#: dù API ở đây là `repo.delete()` — giữ tên của TypeORM để người đọc tài liệu
#: bên đó không phải dịch trong đầu).
HOOKS = (
    "after_load",
    "before_insert",
    "after_insert",
    "before_update",
    "after_update",
    "before_remove",
    "after_remove",
)

#: Method có trong TypeORM nhưng khung chưa có chỗ gắn. Khai vào là app dừng
#: ngay lúc khởi động — thà chết lúc boot còn hơn chờ một sự kiện không tới.
UNSUPPORTED = {
    "before_soft_remove": "khung chưa có xoá mềm (soft delete)",
    "after_soft_remove": "khung chưa có xoá mềm (soft delete)",
    "before_recover": "khung chưa có recover",
    "after_recover": "khung chưa có recover",
    "before_query": "backend `memory` không có câu lệnh để nghe",
    "after_query": "backend `memory` không có câu lệnh để nghe",
    "before_transaction_start": "MongoDB ở khung này không có transaction",
    "after_transaction_start": "MongoDB ở khung này không có transaction",
    "before_transaction_commit": "MongoDB ở khung này không có transaction",
    "after_transaction_commit": "MongoDB ở khung này không có transaction",
    "before_transaction_rollback": "MongoDB ở khung này không có transaction",
    "after_transaction_rollback": "MongoDB ở khung này không có transaction",
}


@dataclass(slots=True)
class EntityEvent:
    """Thứ handler nhận được — tương đương InsertEvent/UpdateEvent/RemoveEvent.

        entity            bản ghi liên quan: sau thao tác với insert/update, và
                          bản vừa bị xoá với `*_remove`
        database_entity   bản ghi TRƯỚC khi sửa/xoá, đọc từ database. Chỉ có khi
                          subscriber thật sự khai `*_update` / `*_remove`, vì
                          lấy nó tốn thêm một lượt đi database.
        updated_columns   tên các trường khác nhau giữa `database_entity` và
                          `entity` — tương đương `updatedColumns`.
        changes           bộ giá trị truyền cho `repo.update(...)`; `None` với
                          `save()`.
        id                khoá chính; ở `*_remove` đây là thứ duy nhất chắc chắn có.
        database          để handler tự mở `Repository(X, event.database)` —
                          tương đương `event.manager` của TypeORM, và nó đi chung
                          transaction với lời ghi đang chạy.
    """

    entity_name: str
    entity: Any = None
    database_entity: Any = None
    updated_columns: frozenset[str] = frozenset()
    changes: dict[str, Any] | None = None
    id: Any = None
    database: Any = None


def entity_subscriber(*entities: type) -> Callable[[type], type]:
    """Đánh dấu class là subscriber. Không truyền gì = nghe MỌI entity.

        @injectable
        @entity_subscriber(Camera)          # chỉ Camera
        class CameraSubscriber: ...

        @injectable
        @entity_subscriber()                # mọi entity, như bỏ listenTo()
        class AuditSubscriber: ...

    Thứ tự với `@injectable` không quan trọng, nhưng phải có `@injectable` —
    khung tìm subscriber trong sổ provider, y như cách NestJS đòi bạn khai nó
    vào `providers`.
    """

    def decorate(cls: type) -> type:
        setattr(cls, _MARKER, tuple(entities))
        return cls

    return decorate


@dataclass(slots=True)
class _Subscriber:
    cls: type
    listen_to: tuple[type, ...]
    hooks: dict[str, Callable[..., Any]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.cls.__name__

    def covers(self, entity: type) -> bool:
        return not self.listen_to or entity in self.listen_to


class EntitySubscribers:
    """Sổ subscriber của tiến trình, và chỗ gọi chúng.

    `Repository` hỏi sổ này trước mỗi thao tác ghi, nên hai phép tra phải rẻ:
    không ai nghe thì `active()` trả False sau đúng một lần kiểm list rỗng, và
    kết quả tra theo (hook, entity) được nhớ lại.
    """

    def __init__(self) -> None:
        self._subscribers: list[_Subscriber] = []
        self._cache: dict[tuple[str, type], tuple[_Subscriber, ...]] = {}
        self._entities: dict[type, bool] = {}

    # ------------------------------------------------------------------ quét
    def discover(self) -> None:
        """Quét sổ provider. Gọi trong `Database.startup()` — lúc module đã nạp đủ."""
        self._subscribers = []
        self._cache = {}
        self._entities = {}
        for cls in _REGISTRY.values():
            listen_to = getattr(cls, _MARKER, None)
            if listen_to is None:
                continue
            self._subscribers.append(_Subscriber(cls=cls, listen_to=listen_to,
                                                 hooks=_hooks_of(cls)))
        if self._subscribers:
            log.info(
                "db.entity_subscribers",
                subscribers=[
                    f"{s.label}({', '.join(e.__name__ for e in s.listen_to) or 'mọi entity'})"
                    f": {', '.join(sorted(s.hooks))}"
                    for s in sorted(self._subscribers, key=lambda s: s.label)
                ],
            )

    def clear(self) -> None:
        self.__init__()          # type: ignore[misc]

    # ------------------------------------------------------------------- tra
    def active(self, entity: type) -> bool:
        """Có ai nghe entity này không — chốt rẻ nhất, hỏi trước mọi thứ khác."""
        if not self._subscribers:
            return False
        found = self._entities.get(entity)
        if found is None:
            found = any(s.hooks and s.covers(entity) for s in self._subscribers)
            self._entities[entity] = found
        return found

    def wants(self, hook: str, entity: type) -> bool:
        return bool(self._matching(hook, entity))

    def wants_any(self, hooks: Sequence[str], entity: type) -> bool:
        return any(self.wants(hook, entity) for hook in hooks)

    def _matching(self, hook: str, entity: type) -> tuple[_Subscriber, ...]:
        if not self._subscribers:
            return ()
        key = (hook, entity)
        found = self._cache.get(key)
        if found is None:
            found = tuple(s for s in self._subscribers if hook in s.hooks and s.covers(entity))
            self._cache[key] = found
        return found

    # ------------------------------------------------------------------ chạy
    async def run(self, hook: str, entity: type, event: EntityEvent) -> None:
        """Gọi mọi handler khớp, lần lượt, và KHÔNG nuốt lỗi.

        Không nuốt là chủ ý: handler chạy trong cùng transaction với lời ghi
        (đúng như TypeORM), nên handler hỏng mà vẫn commit thì dữ liệu và việc
        phụ đi kèm nó lệch nhau vĩnh viễn.
        """
        for subscriber in self._matching(hook, entity):
            instance = container.resolve(subscriber.cls)
            await subscriber.hooks[hook](instance, event)

    async def run_load(self, entity: type, items: Sequence[Any], database: Any) -> None:
        """`after_load` cho một lô bản ghi vừa đọc lên."""
        matching = self._matching("after_load", entity)
        if not matching:
            return
        name = entity.__name__
        for item in items:
            event = EntityEvent(
                entity_name=name, entity=item, id=getattr(item, "id", None), database=database
            )
            for subscriber in matching:
                instance = container.resolve(subscriber.cls)
                await subscriber.hooks["after_load"](instance, event)


def _hooks_of(cls: type) -> dict[str, Callable[..., Any]]:
    """Method nào của class là hook — và chặn ngay những cái khung chưa có."""
    found: dict[str, Callable[..., Any]] = {}
    for name, value in vars(cls).items():
        if name in UNSUPPORTED:
            raise RuntimeError(
                f"{cls.__name__}.{name}: {UNSUPPORTED[name]}. "
                f"Bỏ method này đi — để lại thì nó không bao giờ chạy. "
                f"Hiện có: {', '.join(HOOKS)}."
            )
        if name not in HOOKS:
            continue
        if not inspect.iscoroutinefunction(value):
            raise RuntimeError(f"{cls.__name__}.{name} phải là `async def`")
        params = list(inspect.signature(value).parameters.values())[1:]
        if len(params) != 1:
            raise RuntimeError(
                f"{cls.__name__}.{name}: chữ ký phải là (self, event: EntityEvent)"
            )
        found[name] = value
    if not found:
        raise RuntimeError(
            f"{cls.__name__} mang @entity_subscriber nhưng không có method nào "
            f"trong: {', '.join(HOOKS)}."
        )
    return found


def changed_columns(before: Any, after: Any, fields: Sequence[str]) -> frozenset[str]:
    """Trường nào khác nhau giữa hai bản ghi — tương đương `updatedColumns`."""
    if before is None or after is None:
        return frozenset()
    return frozenset(
        name for name in fields if getattr(before, name, None) != getattr(after, name, None)
    )


def snapshot(obj: Any) -> Any:
    """Bản sao NÔNG của bản ghi vừa đọc, để dùng làm `database_entity`.

    Cần với backend `memory`: ở đó `get()` trả về chính object đang nằm trong
    bảng, nên không sao lại thì lời ghi ngay sau đó sửa luôn cả "bản cũ".
    """
    return copy.copy(obj) if obj is not None else None


#: Sổ dùng chung cho cả tiến trình. `Database.startup()` quét, `Repository` hỏi.
subscribers = EntitySubscribers()
