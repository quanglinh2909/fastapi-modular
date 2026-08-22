"""`fam install <thành-phần>` — cài thư viện của một thành phần rồi ghi .env.

Hai việc luôn đi cùng nhau nên gộp làm một: cài `aio-pika` mà quên ghi
`APP_RABBITMQ__*` thì lớp đó vẫn nằm im, còn ghi biến mà chưa cài thư viện thì
app báo `ComponentNotEnabledError` lúc khởi động.

Cài THẲNG các gói phụ thuộc chứ không chạy `pip install "fastapi-modular[x]"`. Lý do
rất thực tế: cách sau bắt pip đi tìm chính fastapi-modular trên PyPI, nên hỏng ngay
khi bạn đang dùng bản cài từ file .whl, bản `pip install -e .`, hay bản chưa
phát hành.
"""

from __future__ import annotations

import subprocess
import sys

# Nguồn sự thật là `[project.optional-dependencies]` trong pyproject.toml.
# `test_extras_khop_pyproject` giữ hai chỗ này không lệch nhau.
GOI: dict[str, list[str]] = {
    "sqlite": ["sqlalchemy[asyncio]>=2.0.30,<3.0.0", "aiosqlite>=0.20.0", "alembic>=1.13.0"],
    "postgres": ["sqlalchemy[asyncio]>=2.0.30,<3.0.0", "asyncpg>=0.29.0", "alembic>=1.13.0"],
    "mongodb": ["motor>=3.6.0,<4.0.0"],
    "rabbitmq": ["aio-pika>=9.4.0,<10.0.0"],
    "redis": ["redis>=5.0.0,<7.0.0"],
    "mqtt": ["aiomqtt>=2.0.0,<3.0.0"],
    "kafka": ["aiokafka>=0.10.0,<0.13.0"],
    "dev": ["pytest>=8.3.0", "pytest-asyncio>=0.24.0", "httpx>=0.27.0", "ruff>=0.6.0"],
}

# Thành phần -> khối .env tương ứng. `ws-redis` dùng chung thư viện với `redis`
# nhưng ghi khối cấu hình khác: một bên là lớp cache/pub-sub, một bên là adapter
# phát tin WebSocket xuyên worker.
KHOI_ENV: dict[str, str] = {
    "sqlite": "sqlite",
    "postgres": "postgres",
    "mongodb": "mongodb",
    "rabbitmq": "rabbitmq",
    "redis": "redis",
    "mqtt": "mqtt",
    "kafka": "kafka",
    "ws-redis": "ws-redis",
}

_ALIAS = {"mongo": "mongodb", "ws-redis": "redis", "postgresql": "postgres"}

THANH_PHAN = sorted({*GOI, *KHOI_ENV, "all"})


def install(ten: str, *, ghi_env: bool = True, env_file: object = None) -> int:
    from pathlib import Path

    if ten not in THANH_PHAN:
        print(f"Không biết thành phần {ten!r}. Chọn một trong: {', '.join(THANH_PHAN)}")
        return 1

    goi_can = (
        sorted({g for k, v in GOI.items() if k != "dev" for g in v})
        if ten == "all"
        else GOI[_ALIAS.get(ten, ten)]
    )

    print(f"Cài {len(goi_can)} gói cho '{ten}':")
    for g in goi_can:
        print(f"    {g}")
    ma = subprocess.call([sys.executable, "-m", "pip", "install", *goi_can])
    if ma != 0:
        print(
            f"\npip thoát với mã {ma}. Cài tay:\n"
            f"    {sys.executable} -m pip install {' '.join(goi_can)}"
        )
        return ma

    khoi = KHOI_ENV.get(ten)
    if not ghi_env or khoi is None:
        if khoi is None and ghi_env:
            print(f"\n'{ten}' không có biến cấu hình riêng — không ghi .env.")
        return 0

    from fastapi_modular.cli.configure_env import main as ghi

    print()
    return ghi(khoi, Path(env_file) if env_file else Path(".env"))
