"""Container DI kiểu NestJS: đăng ký provider, tự nối phụ thuộc theo kiểu.

Bốn thứ cần biết:

1. `@injectable` — đánh dấu một class là provider (tương đương @Injectable()).
   Class được ghi vào sổ đăng ký theo TÊN, nên container tra cứu được mà
   không cần file nào import file nào.

2. `container.resolve(X)` — lấy instance của X, tự khởi tạo mọi phụ thuộc mà
   __init__ của nó khai báo.

3. `Lazy[X]` — tương đương forwardRef(() => X). Dùng khi hai provider cần
   nhau: phụ thuộc được thay bằng proxy, chỉ resolve thật lúc gọi method đầu
   tiên. Nhờ vậy không có vòng tròn lúc khởi tạo.

4. `@injectable(scope=Scope.REQUEST)` — tương đương Scope.REQUEST của Nest.
   Instance sống trong một request, bị dọn khi request kết thúc. Dùng cho
   session/transaction database, thông tin người dùng hiện tại...

Vì sao không cần import chéo: dưới `from __future__ import annotations`, mọi
annotation là chuỗi. `Lazy[DeviceService]` ở module User chỉ là chữ, không
phải tham chiếu — nên User không phải import Device. Đặt import thật trong
khối `if TYPE_CHECKING` để IDE và mypy vẫn hiểu kiểu.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Annotated, Any, TypeVar, get_args, get_origin

from pymodular.core.compat import StrEnum
from pymodular.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class Scope(StrEnum):
    SINGLETON = "singleton"
    REQUEST = "request"


# Sổ đăng ký toàn cục: tên class -> class. @injectable ghi vào đây lúc import.
_REGISTRY: dict[str, type] = {}
_SCOPES: dict[str, Scope] = {}

# Entity không phải provider (không tự khởi tạo được) nhưng cần tra theo tên để
# làm tham số kiểu cho provider generic, ví dụ Repository[User].
_ENTITIES: dict[str, type] = {}

_LAZY_MARKER = "__container_lazy__"

# Lazy[X] chỉ là Annotated[X, marker]. Theo PEP 593, type checker và IDE coi
# Annotated[X, ...] hệt như X — nên `self._devices` vẫn gợi ý được method của
# DeviceService, trong khi container đọc marker để biết cần resolve muộn.
# Tương đương forwardRef của NestJS.
Lazy = Annotated[T, _LAZY_MARKER]

# Kho chứa instance request-scoped của request đang chạy. None = ngoài request.
_request_store: ContextVar[dict[str, Any] | None] = ContextVar(
    "container_request_store", default=None
)


def injectable(
    cls: type[T] | None = None, *, scope: Scope = Scope.SINGLETON
) -> Any:
    """Đăng ký class làm provider. Dùng được cả `@injectable` lẫn `@injectable(...)`."""

    def decorate(target: type[T]) -> type[T]:
        name = target.__name__
        existing = _REGISTRY.get(name)
        if existing is not None and existing is not target:
            raise RuntimeError(
                f"Trùng tên provider '{name}': {existing.__module__} và {target.__module__}. "
                "Đổi tên một trong hai — container tra cứu theo tên class."
            )
        _REGISTRY[name] = target
        _SCOPES[name] = scope
        return target

    return decorate if cls is None else decorate(cls)


def _as_column_groups(
    declared: Sequence[str | Sequence[str]],
) -> tuple[tuple[str, ...], ...]:
    """Đưa cả cột đơn lẫn cụm cột về cùng một dạng: tuple của tuple."""
    groups: list[tuple[str, ...]] = []
    for item in declared:
        groups.append((item,) if isinstance(item, str) else tuple(item))
    return tuple(groups)


def entity(
    cls: type[T] | None = None,
    *,
    name: str | None = None,
    unique: Sequence[str | Sequence[str]] = (),
    indexes: Sequence[str | Sequence[str]] = (),
) -> Any:
    """Đánh dấu class là entity (tương đương @Entity của TypeORM).

    - `name`    : tên bảng/collection; mặc định là tên class viết thường + "s".
    - `unique`  : các trường phải duy nhất. Ràng buộc được tạo DƯỚI DATABASE,
                  không chỉ kiểm tra trong service — kiểm tra rồi mới ghi là
                  một cuộc đua: hai request đồng thời đều thấy "chưa có" rồi
                  cùng ghi.
    - `indexes` : các trường hay dùng để lọc, tạo index thường.

    Mỗi phần tử là MỘT cột (chuỗi) hoặc MỘT CỤM cột (tuple/list):

        @entity(
            unique=["serial", ("owner_id", "name")],   # cụm: duy nhất theo cặp
            indexes=[("owner_id", "status")],          # cụm: lọc theo cả hai
        )

    Với cụm, THỨ TỰ cột rất quan trọng — xem docs/database.md.
    """

    def decorate(target: type[T]) -> type[T]:
        _ENTITIES[target.__name__] = target
        target.__storage_name__ = name or f"{target.__name__.lower()}s"  # type: ignore[attr-defined]
        target.__storage_unique__ = _as_column_groups(unique)  # type: ignore[attr-defined]
        target.__storage_indexes__ = _as_column_groups(indexes)  # type: ignore[attr-defined]
        return target

    return decorate if cls is None else decorate(cls)


def _parse_annotation(annotation: Any) -> tuple[str, tuple[str, ...], bool]:
    """Đọc annotation -> (tên provider, tham số kiểu, có lazy hay không).

    "Repository[User]"    -> ("Repository", ("User",), False)
    "Lazy[DeviceService]" -> ("DeviceService", (), True)
    "UserRepository"      -> ("UserRepository", (), False)
    """
    if not isinstance(annotation, str):
        metadata = getattr(annotation, "__metadata__", ())
        if metadata:
            inner = get_args(annotation)[0]
            name, args, _ = _parse_annotation(inner)
            return name, args, _LAZY_MARKER in metadata

        origin = get_origin(annotation)
        if origin is not None:
            args = tuple(getattr(a, "__name__", str(a)) for a in get_args(annotation))
            return getattr(origin, "__name__", str(origin)), args, False
        return getattr(annotation, "__name__", str(annotation)), (), False

    text = annotation.strip().strip("'\"")
    lazy = False
    if text.startswith("Lazy[") and text.endswith("]"):
        text = text[len("Lazy[") : -1].strip().strip("'\"")
        lazy = True

    if text.endswith("]") and "[" in text:
        base, _, inside = text.partition("[")
        args = tuple(a.strip().strip("'\"") for a in inside[:-1].split(","))
        return base.strip(), args, lazy

    return text, (), lazy


class _LazyProxy:
    """Đứng thay cho provider thật; resolve ở lần truy cập thuộc tính đầu tiên."""

    __slots__ = ("_container", "_name")

    def __init__(self, container: Container, name: str) -> None:
        object.__setattr__(self, "_container", container)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, item: str) -> Any:
        container = object.__getattribute__(self, "_container")
        name = object.__getattribute__(self, "_name")
        return getattr(container.resolve(name), item)

    def __repr__(self) -> str:
        return f"<Lazy {object.__getattribute__(self, '_name')}>"


class Container:
    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._building: list[str] = []

    # ------------------------------------------------------------------ resolve
    def resolve(self, token: type[T] | str, type_args: tuple[str, ...] = ()) -> T:
        name = token if isinstance(token, str) else token.__name__
        key = f"{name}[{','.join(type_args)}]" if type_args else name
        scope = _SCOPES.get(name, Scope.SINGLETON)

        store = self._store_for(scope, key)
        if key in store:
            return store[key]

        cls = _REGISTRY.get(name)
        if cls is None:
            raise RuntimeError(
                f"Không có provider '{name}'. Thiếu @injectable, hoặc module chứa nó "
                "chưa được nạp — package ứng dụng phải nằm trong thư mục mà "
                "create_app(package=...) quét tới."
            )

        if key in self._building:
            chain = " -> ".join([*self._building, key])
            raise RuntimeError(
                f"Vòng tròn phụ thuộc lúc khởi tạo: {chain}. "
                f"Đổi một cạnh sang Lazy[...] để cắt vòng."
            )

        # Tham số kiểu (Repository[User]) được truyền vào __init__ theo thứ tự,
        # trước các phụ thuộc tự nối.
        positional: list[type] = []
        for arg in type_args:
            resolved = _ENTITIES.get(arg) or _REGISTRY.get(arg)
            if resolved is None:
                raise RuntimeError(
                    f"Không biết kiểu '{arg}' trong '{key}'. Thiếu @entity trên class {arg}?"
                )
            positional.append(resolved)

        self._building.append(key)
        try:
            instance = cls(*positional, **self._build_kwargs(cls, scope, skip=len(positional)))
        finally:
            self._building.pop()

        store[key] = instance
        log.debug("container.provider_created", provider=key, scope=scope.value)
        return instance

    def _store_for(self, scope: Scope, key: str) -> dict[str, Any]:
        if scope is Scope.SINGLETON:
            return self._instances

        store = _request_store.get()
        if store is None:
            raise RuntimeError(
                f"'{key}' là provider request-scoped nhưng không có request scope nào đang mở. "
                "Bọc lời gọi trong `async with request_scope():` (endpoint đã tự làm việc này)."
            )
        return store

    def _build_kwargs(self, cls: type, scope: Scope, skip: int = 0) -> dict[str, Any]:
        init = getattr(cls, "__init__", None)
        if init is None or init is object.__init__:
            return {}

        params = list(inspect.signature(init).parameters.values())[1 + skip :]
        kwargs: dict[str, Any] = {}
        for param in params:
            if param.annotation is inspect.Parameter.empty:
                if param.default is inspect.Parameter.empty:
                    raise RuntimeError(
                        f"{cls.__name__}.__init__ thiếu annotation cho tham số "
                        f"'{param.name}' nên container không biết nối gì vào."
                    )
                continue

            dep_name, dep_args, lazy = _parse_annotation(param.annotation)

            # Tham số có giá trị mặc định mà kiểu của nó không phải provider nào
            # (int, str | None, frozenset...) thì đó là giá trị cấu hình thường,
            # không phải phụ thuộc — cứ để nguyên mặc định.
            known = dep_name in _REGISTRY or dep_name in _ENTITIES
            if not known and param.default is not inspect.Parameter.empty:
                continue

            # Singleton giữ tham chiếu tới instance request-scoped sẽ rò rỉ dữ
            # liệu của request này sang request khác. Chặn ngay lúc khởi tạo.
            if (
                scope is Scope.SINGLETON
                and _SCOPES.get(dep_name, Scope.SINGLETON) is Scope.REQUEST
                and not lazy
            ):
                raise RuntimeError(
                    f"{cls.__name__} là singleton nhưng phụ thuộc '{dep_name}' là "
                    "request-scoped. Hoặc cho nó scope=Scope.REQUEST, hoặc gọi "
                    "container.resolve() ngay trong method thay vì nhận qua __init__."
                )

            kwargs[param.name] = (
                _LazyProxy(self, dep_name) if lazy else self.resolve(dep_name, dep_args)
            )
        return kwargs

    # -------------------------------------------------------------------- tiện ích
    @property
    def registered(self) -> list[str]:
        """Tên mọi provider đã đăng ký — tiện log lúc boot để soi module nào nạp."""
        return list(_REGISTRY)

    def scope_of(self, token: type | str) -> Scope:
        name = token if isinstance(token, str) else token.__name__
        return _SCOPES.get(name, Scope.SINGLETON)

    def override(self, token: type | str, instance: Any) -> None:
        """Cắm sẵn một instance — dùng cho test (tương đương overrideProvider)."""
        name = token if isinstance(token, str) else token.__name__
        if _SCOPES.get(name, Scope.SINGLETON) is Scope.REQUEST:
            store = _request_store.get()
            if store is not None:
                store[name] = instance
                return
        self._instances[name] = instance

    def reset(self) -> None:
        self._instances.clear()


container = Container()


@asynccontextmanager
async def request_scope() -> AsyncIterator[dict[str, Any]]:
    """Mở một vùng đời request cho các provider Scope.REQUEST.

    Lúc đóng, mọi instance có method `on_request_end(error)` sẽ được gọi theo
    thứ tự ngược với lúc tạo — chỗ để commit hoặc rollback transaction.
    """
    store: dict[str, Any] = {}
    token = _request_store.set(store)
    error: BaseException | None = None
    try:
        yield store
    except BaseException as exc:
        error = exc
        raise
    finally:
        for instance in reversed(list(store.values())):
            hook: Callable[..., Any] | None = getattr(instance, "on_request_end", None)
            if hook is not None:
                await hook(error)
        _request_store.reset(token)


def Inject(token: type[T] | str) -> Any:
    """Cầu nối container -> FastAPI Depends.

    Dùng trong router hàm:  service: Annotated[UserService, Inject(UserService)]
    Controller dạng class không cần cái này.
    """
    from fastapi import Depends

    def _provide() -> Any:
        return container.resolve(token)

    return Depends(_provide)
