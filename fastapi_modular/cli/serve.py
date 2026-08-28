"""`fam dev` và `fam run` — chạy ứng dụng mà không phải nhớ dòng uvicorn nào.

Khác nhau đúng một chỗ: `dev` bật autoreload và chạy MỘT tiến trình, `run` tắt
reload và chạy nhiều worker. Host/cổng lấy từ cấu hình (`APP_HOST`, `APP_PORT`)
nên đổi trong .env là đủ, không phải sửa lệnh.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _prepare_sys_path() -> None:
    """Thêm thư mục hiện tại vào sys.path.

    Lệnh `uvicorn` tự làm việc này, nhưng khi gọi uvicorn từ trong một
    console_script thì sys.path[0] là thư mục chứa script trong venv, không phải
    dự án — nên `src.main` sẽ không import được.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def _missing_app(target: str) -> int:
    module_name = target.split(":")[0]
    print(
        f"Không import được {module_name!r} từ {Path.cwd()}.\n"
        "Đứng ở thư mục gốc dự án (chỗ có app/), hoặc chỉ đường bằng --app.\n"
        "Chưa có dự án thì tạo bằng: fam init"
    )
    return 1


def serve(*, target: str, reload: bool, workers: int | None, host: str | None,
          port: int | None) -> int:
    _prepare_sys_path()

    import uvicorn

    from fastapi_modular.core.config import get_settings

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port

    module_name = target.split(":")[0]
    root = module_name.split(".")[0]
    if not (Path.cwd() / root).exists() and not (Path.cwd() / f"{root}.py").exists():
        return _missing_app(target)

    if reload:
        # Chỉ theo dõi package ứng dụng: theo dõi cả cây thư mục sẽ khiến mỗi
        # lần ghi file .db hay .env cũng khởi động lại server.
        uvicorn.run(target, host=host, port=port, reload=True, reload_dirs=[root])
    else:
        # Mặc định MỘT tiến trình. Nhiều worker là quyết định của người triển
        # khai, không phải mặc định êm ái: bốn tiến trình nghĩa là bốn bản
        # `@interval`/`@worker` cùng chạy nếu quên khoá, bốn bộ đếm metrics
        # riêng, và WebSocket không thấy nhau nếu chưa bật adapter Redis.
        uvicorn.run(target, host=host, port=port, workers=workers or 1)
    return 0
