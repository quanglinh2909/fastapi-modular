"""Hợp đồng chung cho mọi backend database, và phần ánh xạ entity <-> bản ghi.

Ba backend (memory / SQL / MongoDB) đều cài đúng bộ method dưới đây, nên tầng
`Repository` ở core không cần biết đang chạy trên cái nào.

Ánh xạ dựa hoàn toàn vào dataclass entity: tên trường thành tên cột hoặc khoá
document, kiểu Python thành kiểu cột. Entity vì vậy vẫn là dataclass thuần,
không dính ORM.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import Enum
from functools import cache
from typing import Any, Protocol, TypeVar, get_args, get_type_hints

from pymodular.core.compat import UTC, TimeoutErrors

E = TypeVar("E")

Filters = dict[str, Any]
Match = Callable[[Any], bool] | None


MAX_INDEX_NAME = 63  # PostgreSQL cắt tên định danh ở 63 ký tự


def index_name(prefix: str, storage: str, columns: Sequence[str]) -> str:
    """Tên index ổn định, không vượt giới hạn độ dài của PostgreSQL."""
    name = f"{prefix}_{storage}_{'_'.join(columns)}"
    if len(name) <= MAX_INDEX_NAME:
        return name
    digest = hashlib.sha1("_".join(columns).encode()).hexdigest()[:8]
    return f"{prefix}_{storage[:40]}_{digest}"


@dataclasses.dataclass(frozen=True, slots=True)
class EntityMapping:
    entity: type
    storage: str                 # tên bảng (SQL) hoặc collection (Mongo)
    fields: dict[str, type]      # tên trường -> kiểu đã giải
    unique: tuple[tuple[str, ...], ...]   # mỗi phần tử là một cột hoặc một cụm cột
    indexes: tuple[tuple[str, ...], ...]

    def index_specs(self) -> list[tuple[str, tuple[str, ...], bool]]:
        """[(tên index, các cột, có unique không)] cho mọi index đã khai báo."""
        return [
            *((index_name("uq", self.storage, cols), cols, True) for cols in self.unique),
            *((index_name("ix", self.storage, cols), cols, False) for cols in self.indexes),
        ]


@cache
def mapping_for(entity: type) -> EntityMapping:
    if not dataclasses.is_dataclass(entity):
        raise TypeError(f"{entity.__name__} phải là dataclass mới ánh xạ được")

    hints = get_type_hints(entity)
    fields = {f.name: hints.get(f.name, str) for f in dataclasses.fields(entity)}
    return EntityMapping(
        entity=entity,
        storage=getattr(entity, "__storage_name__", f"{entity.__name__.lower()}s"),
        fields=fields,
        unique=tuple(getattr(entity, "__storage_unique__", ())),
        indexes=tuple(getattr(entity, "__storage_indexes__", ())),
    )


def to_document(obj: Any) -> dict[str, Any]:
    """Entity -> dict thuần (Enum thành giá trị, datetime giữ nguyên)."""
    doc: dict[str, Any] = {}
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        doc[field.name] = value.value if isinstance(value, Enum) else value
    return doc


def _allows_none(declared: Any) -> bool:
    """Kiểu có chấp nhận None không (Optional[X] hoặc X | None)."""
    return declared is Any or type(None) in get_args(declared)


@cache
def _defaults_of(entity: type) -> dict[str, Callable[[], Any]]:
    """Hàm sinh giá trị mặc định cho từng trường có khai báo default."""
    out: dict[str, Callable[[], Any]] = {}
    for f in dataclasses.fields(entity):
        if f.default is not dataclasses.MISSING:
            out[f.name] = lambda value=f.default: value
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            out[f.name] = f.default_factory  # type: ignore[assignment]
    return out


def from_document(entity: type[E], doc: dict[str, Any]) -> E:
    """dict -> entity, ép lại Enum/datetime và bù giá trị mặc định.

    Bù mặc định là chỗ quan trọng khi schema tiến hoá: thêm một trường vào
    entity thì bản ghi cũ chưa có giá trị (SQL trả NULL, Mongo thiếu hẳn khoá).
    Nếu kiểu khai báo không nhận None mà entity có default, ta dùng default —
    nhờ vậy dữ liệu cũ đọc ra vẫn hợp lệ thay vì mang None trái kiểu.
    """
    mapping = mapping_for(entity)
    defaults = _defaults_of(entity)
    kwargs: dict[str, Any] = {}

    for name, declared in mapping.fields.items():
        value = doc.get(name)

        if value is None and not _allows_none(declared) and name in defaults:
            value = defaults[name]()
        elif value is not None:
            if isinstance(declared, type) and issubclass(declared, Enum):
                value = declared(value)
            elif declared is datetime:
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if isinstance(value, datetime) and value.tzinfo is None:
                    # SQLite và MongoDB trả datetime KHÔNG mang múi giờ (cả hai
                    # lưu theo UTC nhưng không kèm tzinfo). Gắn lại UTC ở đây để
                    # ba driver cho ra cùng một dạng, và để một response không
                    # lẫn lộn "có Z" với "không Z".
                    value = value.replace(tzinfo=UTC)

        kwargs[name] = value

    return entity(**kwargs)  # type: ignore[call-arg]


def matches(obj: Any, filters: Filters, match: Match) -> bool:
    """Lọc trong Python — dùng cho backend memory và cho tham số `match=`."""
    for key, value in filters.items():
        if getattr(obj, key, None) != value:
            return False
    return match is None or match(obj)


def active_filters(filters: Filters) -> Filters:
    """Bỏ mọi điều kiện có giá trị None (quy ước: None = không lọc)."""
    return {k: v for k, v in filters.items() if v is not None}


class DuplicateKeyViolation(Exception):
    """Vi phạm ràng buộc duy nhất, do backend không có sẵn kiểu lỗi riêng.

    SQL ném IntegrityError, Mongo ném DuplicateKeyError; backend memory dùng
    lớp này để ba đường đi cho ra cùng một kết quả HTTP 409.
    """

    def __init__(self, storage: str, columns: Sequence[str], values: Sequence[Any]) -> None:
        self.storage = storage
        self.columns = tuple(columns)
        self.values = tuple(values)
        pairs = ", ".join(f"{c}={v!r}" for c, v in zip(self.columns, self.values, strict=True))
        super().__init__(f"{storage}: đã tồn tại bản ghi với {pairs}")


# Tên lớp lỗi "tạm thời" của các driver. Dùng tên thay vì import để file này
# không phụ thuộc vào thư viện của driver nào.
_TRANSIENT_NAMES = frozenset({
    "ServerSelectionTimeoutError",   # pymongo: chưa chọn được server
    "AutoReconnect",                 # pymongo: mất kết nối, sẽ tự nối lại
    "NetworkTimeout",                # pymongo
    "ConnectionFailure",             # pymongo (lớp cha)
    "CannotConnectNowError",         # asyncpg: database đang khởi động
    "TooManyConnectionsError",       # asyncpg: hết slot, chờ chút sẽ có
})


def is_transient_error(exc: BaseException) -> bool:
    """Lỗi này có khả năng tự hết nếu thử lại không?

    Phân biệt "database chưa kịp lên" (đáng thử lại) với "sai mật khẩu / sai
    tên database" (thử lại bao nhiêu lần cũng vậy, chỉ làm chậm lúc phát hiện
    cấu hình sai). Duyệt cả chuỗi __cause__ vì driver hay bọc lỗi gốc lại.
    """
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, *TimeoutErrors)):
            return True
        if type(current).__name__ in _TRANSIENT_NAMES:
            return True
        # OSError chung (host chưa phân giải được, mạng chưa lên...)
        if isinstance(current, OSError) and not isinstance(current, (IsADirectoryError, NotADirectoryError, PermissionError)):
            return True
        current = current.__cause__ or current.__context__

    return False


class DatabaseBackend(Protocol):
    """Bộ method mà mọi backend phải có."""

    name: str

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def ping(self) -> bool: ...

    async def get(self, entity: type[E], id_: str) -> E | None: ...
    async def find(
        self,
        entity: type[E],
        *,
        filters: Filters,
        match: Match = None,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[E]: ...
    async def find_one(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> E | None: ...
    async def count(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int: ...
    async def save(self, entity: type[E], obj: E) -> E: ...
    async def delete(self, entity: type[E], id_: str) -> bool: ...
    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int: ...
