"""Module registry — tự động gom controller của mọi module trong package ứng dụng.

Tương đương module scanning của NestJS. Mỗi thư mục con của package ứng dụng là
một module; hàm ở đây nạp hết submodule bên trong để các decorator @controller
chạy, rồi dựng router từ những controller vừa đăng ký.

Thêm module mới = tạo thư mục, viết @controller. Không sửa file này, và cũng
không phải export biến `router` nào trong module.

Trường hợp cần một APIRouter dựng tay (websocket, router của thư viện ngoài),
module vẫn có thể export biến `router` — nếu có, khung dùng luôn cái đó.

Package ứng dụng mặc định là `src.api`. Dự án xếp khác thì truyền vào:

    create_app(settings, package="cong_ty.dich_vu")
"""

from __future__ import annotations

import pkgutil
from importlib import import_module
from pathlib import Path

from fastapi import APIRouter, FastAPI

from fastapi_modular.core.controller import build_router, controllers_in
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.websocket import build_ws_router, gateways_in

log = get_logger(__name__)

DEFAULT_PACKAGE = "src.api"
"""Package chứa các module ứng dụng, khi không truyền gì khác.

Khớp với bộ khung `fam init` sinh ra: `src/api/<module>/`. Dự án xếp khác thì
nói ra một lần trong `src/main.py`:

    register_routes(app, package="cong_ty.dich_vu")
"""


def _package_dir(package: str) -> Path:
    """Thư mục thật của package — quét file phải dựa vào đây, không phải __file__.

    Trước đây hàm này lấy `Path(__file__).parent`, đúng khi khung và ứng dụng
    nằm chung một cây thư mục. Cài bằng pip thì khung nằm trong site-packages
    còn ứng dụng nằm ở dự án của người dùng, nên phải hỏi chính package đó.
    """
    module = import_module(package)
    paths = list(getattr(module, "__path__", []))
    if not paths:
        raise RuntimeError(
            f"{package!r} không phải một package (thiếu __init__.py?). "
            "Đây là nơi chứa các module ứng dụng — xem fastapi_modular.discovery."
        )
    return Path(paths[0])


def _import_submodules(package: str, package_dir: Path) -> None:
    """Nạp mọi submodule để decorator @controller/@injectable/@entity chạy."""
    for info in pkgutil.walk_packages([str(package_dir)], prefix=f"{package}."):
        import_module(info.name)


def load_all_modules(package: str = DEFAULT_PACKAGE) -> None:
    """Nạp mọi module trong package ứng dụng để decorator @entity/@injectable chạy.

    Migration cần hàm này: Alembic phải thấy đủ entity mới sinh được diff, mà
    nó không đi qua đường khởi động app.
    """
    _iter_packages(package)


def _iter_packages(package: str) -> list[str]:
    """Tên các module con, đã nạp xong, theo thứ tự alphabet."""
    root = _package_dir(package)
    names: list[str] = []
    for info in sorted(pkgutil.iter_modules([str(root)]), key=lambda i: i.name):
        if not info.ispkg or info.name.startswith("_"):
            continue
        import_module(f"{package}.{info.name}")
        _import_submodules(f"{package}.{info.name}", root / info.name)
        names.append(info.name)
    return names


def discover_routers(package: str = DEFAULT_PACKAGE) -> list[tuple[str, APIRouter]]:
    """Quét package ứng dụng, trả về [(tên module, router HTTP)] theo alphabet."""
    found: list[tuple[str, APIRouter]] = []

    for name in _iter_packages(package):
        module_package = f"{package}.{name}"
        module = import_module(module_package)

        explicit = getattr(module, "router", None)
        if isinstance(explicit, APIRouter):
            found.append((name, explicit))
            continue

        controllers = controllers_in(module_package)
        if not controllers:
            if gateways_in(module_package):
                # Module chỉ có gateway WebSocket là hợp lệ, không phải thiếu sót.
                continue
            # Im lặng ở đây là nguyên nhân số một của "sao route của tôi 404?".
            log.warning("api.module_without_controller", module=name)
            continue

        log.debug(
            "api.module_scanned",
            module=name,
            controllers=[c.__name__ for c in controllers],
        )
        found.append((name, build_router(*controllers)))

    return found


def discover_gateways(package: str = DEFAULT_PACKAGE) -> list[tuple[str, list[type]]]:
    """Quét package ứng dụng, trả về [(tên module, các class gateway)]."""
    return [
        (name, found)
        for name in _iter_packages(package)
        if (found := gateways_in(f"{package}.{name}"))
    ]


def register_routes(
    app: FastAPI, *, prefix: str = "/api", package: str = DEFAULT_PACKAGE
) -> None:
    """Gắn toàn bộ module đã phát hiện vào app."""
    api = APIRouter(prefix=prefix)
    names: list[str] = []

    for name, router in discover_routers(package):
        api.include_router(router)
        names.append(name)

    app.include_router(api)
    log.info(
        "api.modules_registered",
        package=package,
        prefix=prefix,
        count=len(names),
        modules=names,
    )

    register_gateways(app, package=package)


def register_gateways(app: FastAPI, *, package: str = DEFAULT_PACKAGE) -> None:
    """Gắn route WebSocket.

    KHÔNG nằm dưới tiền tố /api: đường dẫn WebSocket do @gateway(path=...) khai
    trọn vẹn, vì WebSocket không phải REST và thường được reverse proxy định
    tuyến riêng (nginx phải bật Upgrade cho đúng những đường dẫn này).
    """
    classes: list[type] = []
    paths: dict[str, str] = {}

    for module_name, gateways in discover_gateways(package):
        for cls in gateways:
            path = cls.__gateway_meta__.path
            if path in paths:
                raise RuntimeError(
                    f"Hai gateway cùng path {path!r}: {paths[path]} và {cls.__name__}"
                )
            paths[path] = cls.__name__
            classes.append(cls)
        log.debug("api.gateways_scanned", module=module_name)

    if not classes:
        return

    app.include_router(build_ws_router(*classes))
    log.info(
        "api.gateways_registered",
        count=len(classes),
        gateways=[f"{cls.__name__} {cls.__gateway_meta__.path}" for cls in classes],
    )
