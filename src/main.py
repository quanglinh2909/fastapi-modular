"""Điểm vào — chạy bằng `pym dev`.

FILE NÀY LÀ CỦA BẠN. Khung cố ý không giấu phần lắp ráp: mỗi dòng dưới đây làm
đúng một việc, xoá được, đổi thứ tự được, chèn thêm được.

Thêm module nghiệp vụ thì KHÔNG phải sửa file này — `register_routes` tự quét
thư mục `app/`. Còn thêm middleware, đổi CORS, gắn router của thư viện ngoài,
bọc lifespan... thì sửa ngay tại đây.

Chưa cần sửa gì thì cả khối dưới rút lại còn hai dòng:

    from pymodular import create_app
    app = create_app(AppSettings())
"""

from __future__ import annotations

from pymodular import (
    add_middleware,
    bind_settings,
    configure_logging,
    new_fastapi,
    register_error_handlers,
    register_routes,
)
from src.core.config import AppSettings
from src.core.lifespan import lifespan

# Đọc .env, chốt lớp cấu hình cho cả tiến trình, cắm vào DI container.
settings = bind_settings(AppSettings())
configure_logging(settings.log)

# lifespan: khung lo database và hạ tầng, phần việc riêng nằm ở
# src/core/lifespan.py — sửa ở đó, không phải ở đây.
app = new_fastapi(settings, lifespan=lifespan)

# CORS + access log + request-id. Middleware của bạn thêm sau dòng này sẽ chạy
# TRƯỚC ba cái đó (FastAPI chạy ngược thứ tự add).
add_middleware(app, settings)

# Đổi lỗi nghiệp vụ thành JSON có mã và request_id.
register_error_handlers(app, debug=settings.debug)

# Quét app/, gắn mọi @controller và @gateway tìm được.
register_routes(app, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": settings.name, "version": settings.version}
