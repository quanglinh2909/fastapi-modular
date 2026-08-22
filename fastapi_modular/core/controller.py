"""Controller dạng class — tương đương @Controller của NestJS.

Thay vì nhét service vào từng handler:

    async def list_users(service: UserServiceDep, limit: int = 20): ...

thì khai báo một lần ở __init__ như Nest:

    @controller(prefix="/users", tags=["users"])
    class UserController:
        def __init__(self, service: UserService) -> None:
            self._service = service

        @get("", response_model=Page[UserOut])
        async def list_users(self, limit: int = 20): ...

    router = build_router(UserController)

Cách hoạt động: mỗi method có metadata route được bọc thành một endpoint không
còn tham số `self`; lúc chạy, `self` được lấy từ container. Nhờ vậy FastAPI
vẫn thấy đúng chữ ký để sinh OpenAPI và validate, còn phụ thuộc thì do
container nối — không lẫn hai cơ chế DI vào nhau.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from typing import Any, TypeVar, get_type_hints

from fastapi import APIRouter
from starlette.requests import Request

from fastapi_modular.core.container import container, injectable, request_scope
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

_ROUTE_ATTR = "__route_meta__"
_GUARDS_ATTR = "__guards__"
_REQUEST_PARAM = "__guard_request"
_CONTROLLER_ATTR = "__controller_meta__"

# Sổ controller theo đúng thứ tự khai báo (= thứ tự import). app.py đọc sổ này
# để dựng router, nên module không phải tự export biến `router` nào cả.
_CONTROLLERS: list[type] = []


def controller(
    *,
    prefix: str = "",
    tags: list[str] | None = None,
    guards: Sequence[type] = (),
    **router_kwargs: Any,
) -> Callable[[type[T]], type[T]]:
    """Đánh dấu class là controller và đăng ký nó làm provider.

    `guards` áp cho MỌI route của controller; guard khai báo thêm ở từng route
    sẽ chạy nối tiếp sau.
    """

    def decorate(cls: type[T]) -> type[T]:
        setattr(cls, _CONTROLLER_ATTR, {"prefix": prefix, "tags": tags, **router_kwargs})
        setattr(cls, _GUARDS_ATTR, tuple(guards))
        _CONTROLLERS.append(cls)
        return injectable(cls)

    return decorate


def controllers_in(package: str) -> list[type]:
    """Mọi controller được khai báo bên trong một package, theo thứ tự import."""
    return [
        cls
        for cls in _CONTROLLERS
        if cls.__module__ == package or cls.__module__.startswith(f"{package}.")
    ]


def route(
    method: str, path: str, *, guards: Sequence[type] = (), **kwargs: Any
) -> Callable[[Callable], Callable]:
    """Gắn metadata route lên method. Dùng qua get/post/put/patch/delete."""

    def decorate(fn: Callable) -> Callable:
        setattr(fn, _ROUTE_ATTR, {"path": path, "methods": [method], **kwargs})
        setattr(fn, _GUARDS_ATTR, tuple(guards))
        return fn

    return decorate


def get(path: str = "", **kwargs: Any):
    return route("GET", path, **kwargs)


def post(path: str = "", **kwargs: Any):
    return route("POST", path, **kwargs)


def put(path: str = "", **kwargs: Any):
    return route("PUT", path, **kwargs)


def patch(path: str = "", **kwargs: Any):
    return route("PATCH", path, **kwargs)


def delete(path: str = "", **kwargs: Any):
    return route("DELETE", path, **kwargs)


def _make_endpoint(cls: type, fn: Callable, guards: Sequence[type] = ()) -> Callable:
    """Bọc method thành endpoint FastAPI, bỏ `self` khỏi chữ ký."""
    # Giải annotation NGAY tại đây, bằng globals của module chứa controller.
    # Nếu để nguyên chuỗi, FastAPI sẽ giải bằng globals của file này và không
    # tìm thấy Page/UserOut/... của module kia.
    hints = get_type_hints(fn, include_extras=True)
    signature = inspect.signature(fn)

    params = [
        p.replace(annotation=hints.get(p.name, p.annotation))
        for p in list(signature.parameters.values())[1:]  # bỏ self
    ]

    if guards:
        # Thêm một tham số Request để guard soi được header/đường dẫn. FastAPI
        # điền tham số kiểu Request và KHÔNG đưa nó vào OpenAPI, nên hợp đồng
        # API không đổi. Đặt KEYWORD_ONLY để không phá thứ tự tham số có sẵn.
        params.append(
            inspect.Parameter(
                _REQUEST_PARAM, inspect.Parameter.KEYWORD_ONLY, annotation=Request
            )
        )

    @functools.wraps(fn)
    async def endpoint(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.pop(_REQUEST_PARAM, None)

        # Mở request scope quanh handler, KHÔNG đặt ở middleware: dọn dẹp của
        # middleware chạy sau khi response đã gửi đi, nên transaction sẽ commit
        # muộn hơn lúc client nhận kết quả (xem ghi chú ở middleware/request_context.py).
        # Ở đây commit xảy ra trước khi FastAPI serialize và gửi response.
        async with request_scope():
            # Guard chạy TRONG request scope để chúng dùng được provider
            # request-scoped (ví dụ điền Principal cho request này).
            for guard_cls in guards:
                await container.resolve(guard_cls).check(request)
            return await fn(container.resolve(cls), *args, **kwargs)

    # get_type_hints đổi `-> None` thành NoneType; FastAPI cần đúng None, nếu
    # không nó coi NoneType là response_model và chặn các route 204.
    returns = hints.get("return", signature.return_annotation)
    if returns is type(None):
        returns = None

    # __signature__ được đặt sau wraps nên inspect.signature dừng ở đây,
    # không lần ngược về method gốc (vốn vẫn còn `self`).
    endpoint.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=params,
        return_annotation=returns,
    )
    return endpoint


def build_router(*controllers: type) -> APIRouter:
    """Dựng một APIRouter từ các class controller, theo đúng thứ tự khai báo."""
    root = APIRouter()

    for cls in controllers:
        meta = getattr(cls, _CONTROLLER_ATTR, None)
        if meta is None:
            raise RuntimeError(f"{cls.__name__} thiếu @controller(...)")

        sub = APIRouter(**{k: v for k, v in meta.items() if v is not None})

        # vars() giữ nguyên thứ tự khai báo trong class — quan trọng vì route
        # khớp theo thứ tự đăng ký (/users/me phải đứng trước /users/{id}).
        count = 0
        for fn in vars(cls).values():
            route_meta = getattr(fn, _ROUTE_ATTR, None)
            if route_meta is None:
                continue
            # Guard của controller chạy trước, rồi tới guard của riêng route.
            guards = (*getattr(cls, _GUARDS_ATTR, ()), *getattr(fn, _GUARDS_ATTR, ()))
            sub.add_api_route(
                route_meta["path"],
                _make_endpoint(cls, fn, guards),
                **{k: v for k, v in route_meta.items() if k != "path"},
            )
            count += 1

        if count == 0:
            # Controller không có method nào mang @get/@post/... thì không sinh
            # route nào cả. Phải kêu, nếu không sẽ là 404 không rõ nguyên nhân.
            log.warning(
                "controller.no_routes",
                controller=cls.__name__,
                module=cls.__module__,
                hint="thiếu @get/@post/@patch/@delete trên method?",
            )

        root.include_router(sub)

    return root
