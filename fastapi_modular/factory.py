"""Các mảnh ghép để dựng app — và `create_app()` gom sẵn chúng lại.

`src/main.py` là file CỦA BẠN, không phải của khung. Nên mỗi bước lắp ráp ở đây
là một hàm công khai, gọi riêng được, chèn thêm được:

    settings = bind_settings(AppSettings())
    configure_logging(settings.log)

    app = new_fastapi(settings, lifespan=lifespan)
    add_middleware(app, settings)
    register_error_handlers(app, debug=settings.debug)
    register_routes(app, prefix=settings.api_prefix)

Cần thêm middleware của mình, đổi thứ tự, bỏ CORS, gắn router bên thứ ba? Sửa
thẳng trong main.py của bạn, không phải ngồi tìm cách "ghi đè" cái gì trong
khung.

`create_app()` chạy đúng dãy trên, không hơn. Dùng nó khi chưa cần sửa gì:

    app = create_app(AppSettings())
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi_modular.core.config import Settings, get_settings, use_settings
from fastapi_modular.core.container import container
from fastapi_modular.core.error_handlers import register_error_handlers
from fastapi_modular.core.lifespan import lifespan
from fastapi_modular.core.logging import configure_logging
from fastapi_modular.core.providers import register_providers
from fastapi_modular.discovery import DEFAULT_PACKAGE, register_routes
from fastapi_modular.middleware.request_context import AccessLogMiddleware, RequestContextMiddleware


def bind_settings(settings: Settings | None = None) -> Settings:
    """Chốt cấu hình cho cả tiến trình và cắm nó vào DI container.

    Truyền `None` thì đọc từ `.env` bằng lớp `Settings` gốc. Truyền một instance
    lớp con thì lớp đó được ghi nhớ, để những chỗ khung tự dựng cấu hình
    (Alembic, gateway lúc không có request) không ra một bản cấu hình khác.

    Đăng ký dưới CẢ chuỗi kế thừa: container tra provider theo TÊN lớp, nên khai
    `def __init__(self, settings: AppSettings)` mà chỉ đăng ký "Settings" thì sẽ
    báo không có provider 'AppSettings'.
    """
    if settings is None:
        settings = get_settings()
    else:
        use_settings(type(settings))

    for lop in type(settings).__mro__:
        if not (isinstance(lop, type) and issubclass(lop, Settings)):
            break
        container.override(lop, settings)
    return settings


def new_fastapi(settings: Settings, **kwargs: Any) -> FastAPI:
    """`FastAPI(...)` với tiêu đề, phiên bản và đường dẫn docs lấy từ cấu hình.

    Ở prod, `docs_url`/`redoc_url`/`openapi_url` là None nên trang docs biến
    mất — đừng để lộ sơ đồ API ra ngoài mà không có chủ ý.

    `kwargs` truyền thẳng cho FastAPI, và ĐÈ được mọi giá trị ở trên.
    """
    mac_dinh: dict[str, Any] = {
        "title": settings.name,
        "version": settings.version,
        "debug": settings.debug,
        "lifespan": lifespan,
        "docs_url": settings.docs_url,
        "redoc_url": settings.redoc_url,
        "openapi_url": settings.openapi_url,
    }
    return FastAPI(**{**mac_dinh, **kwargs})


def add_middleware(app: FastAPI, settings: Settings) -> None:
    """Ba middleware của khung: CORS, access log, request-id.

    Thứ tự add là NGƯỢC với thứ tự chạy, nên RequestContext (thứ sinh ra
    request-id) được add sau cùng để chạy đầu tiên — mọi log của các lớp sau nó
    mới có id để bám theo.

    Thêm middleware của bạn thì gọi `app.add_middleware(...)` sau hàm này nếu
    muốn nó chạy TRƯỚC, hoặc trước hàm này nếu muốn nó chạy SAU.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allow_origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=settings.cors.allow_methods,
        allow_headers=settings.cors.allow_headers,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)


def create_app(
    settings: Settings | None = None,
    *,
    package: str = DEFAULT_PACKAGE,
    providers_package: str | None = None,
) -> FastAPI:
    """Dựng app theo cách mặc định. `package` là nơi chứa module ứng dụng.

    `providers_package` mặc định là gói `providers` nằm cạnh `package`, tức
    `src.api` -> `src.providers`. Không có thư mục đó thì bỏ qua, không lỗi.

    Không có gì ở đây mà `src/main.py` của bạn không gọi được từng phần.
    """
    settings = bind_settings(settings)
    configure_logging(settings.log)

    app = new_fastapi(settings)
    add_middleware(app, settings)
    register_error_handlers(app, debug=settings.debug)

    # Provider phải có mặt TRƯỚC khi route được dựng: controller nhận service,
    # service nhận Providers["..."] qua __init__.
    goc, _, _ = package.rpartition(".")
    register_providers(providers_package or (f"{goc}.providers" if goc else "providers"))

    register_routes(app, prefix=settings.api_prefix, package=package)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.name,
            "version": settings.version,
            "docs": settings.docs_url or "disabled",
        }

    return app
