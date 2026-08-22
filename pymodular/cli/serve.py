"""`pym dev` và `pym run` — chạy ứng dụng mà không phải nhớ dòng uvicorn nào.

Khác nhau đúng một chỗ: `dev` bật autoreload và chạy MỘT tiến trình, `run` tắt
reload và chạy nhiều worker. Host/cổng lấy từ cấu hình (`APP_HOST`, `APP_PORT`)
nên đổi trong .env là đủ, không phải sửa lệnh.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _chuan_bi_sys_path() -> None:
    """Thêm thư mục hiện tại vào sys.path.

    Lệnh `uvicorn` tự làm việc này, nhưng khi gọi uvicorn từ trong một
    console_script thì sys.path[0] là thư mục chứa script trong venv, không phải
    dự án — nên `src.main` sẽ không import được.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def _thieu_app(target: str) -> int:
    ten_module = target.split(":")[0]
    print(
        f"Không import được {ten_module!r} từ {Path.cwd()}.\n"
        "Đứng ở thư mục gốc dự án (chỗ có app/), hoặc chỉ đường bằng --app.\n"
        "Chưa có dự án thì tạo bằng: pym init"
    )
    return 1


def serve(*, target: str, reload: bool, workers: int | None, host: str | None,
          port: int | None) -> int:
    _chuan_bi_sys_path()

    import uvicorn

    from pymodular.core.config import get_settings

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port

    ten_module = target.split(":")[0]
    goc = ten_module.split(".")[0]
    if not (Path.cwd() / goc).exists() and not (Path.cwd() / f"{goc}.py").exists():
        return _thieu_app(target)

    if reload:
        # Chỉ theo dõi package ứng dụng: theo dõi cả cây thư mục sẽ khiến mỗi
        # lần ghi file .db hay .env cũng khởi động lại server.
        uvicorn.run(target, host=host, port=port, reload=True, reload_dirs=[goc])
    else:
        uvicorn.run(target, host=host, port=port, workers=workers or 4)
    return 0
