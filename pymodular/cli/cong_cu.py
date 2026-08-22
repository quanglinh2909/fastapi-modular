"""`pym test`, `pym lint`, `pym migrate`, `pym build`, `pym publish` — gọi hộ công cụ.

Ba lệnh này chỉ là lối tắt cho `pytest`, `ruff`, `alembic`. Giá trị của chúng
không nằm ở việc tiết kiệm chữ, mà ở chỗ khi thiếu công cụ thì nói thẳng phải
cài gì, thay vì ném ra `ModuleNotFoundError` trần.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

_CAI_GI = {
    "pytest": "pym install dev",
    "ruff": "pym install dev",
    "build": "pip install build",
    "twine": "pip install twine",
    "alembic": 'pip install "pymodular[sqlite]"   # hoặc [postgres]',
}


def _co(ten: str) -> bool:
    return importlib.util.find_spec(ten) is not None


def _thieu(ten: str) -> int:
    print(f"Chưa cài {ten}. Chạy: {_CAI_GI[ten]}")
    return 1


def _goi(module: str, *args: str) -> int:
    """Chạy công cụ như một tiến trình con, giữ nguyên mã thoát của nó."""
    return subprocess.call([sys.executable, "-m", module, *args])


def chay_cong_cu(args: Namespace) -> int:
    if args.lenh == "test":
        if not _co("pytest"):
            return _thieu("pytest")
        return _goi("pytest", "-q", *args.them)

    if args.lenh == "lint":
        if not _co("ruff"):
            return _thieu("ruff")
        co_dinh = ["check", *(["--fix"] if args.fix else [])]
        return _goi("ruff", *co_dinh, *args.duong_dan)

    if args.lenh == "build":
        return _build(args)

    if args.lenh == "publish":
        return _publish(args)

    return _migrate(args)


def _build(args: Namespace) -> int:
    if not _co("build"):
        return _thieu("build")
    if not Path("pyproject.toml").exists():
        print("Không thấy pyproject.toml ở thư mục này — không có gì để dựng.")
        return 1
    if not args.no_clean:
        # dist/ cũ còn sót lại là cách phổ biến nhất để đẩy nhầm một bản cũ lên
        # PyPI: twine upload dist/* lấy TẤT CẢ file trong đó.
        shutil.rmtree("dist", ignore_errors=True)
    return _goi("build")


def _publish(args: Namespace) -> int:
    if not _co("twine"):
        return _thieu("twine")
    goi = sorted(Path("dist").glob("*")) if Path("dist").exists() else []
    if not goi:
        print("dist/ trống. Chạy `pym build` trước.")
        return 1

    print("Sắp đẩy lên", "TestPyPI" if args.test else "PyPI", "các file:")
    for f in goi:
        print(f"    {f.name}")
    kho = ["--repository", "testpypi"] if args.test else []
    return _goi("twine", "upload", *kho, *[str(f) for f in goi])


def _migrate(args: Namespace) -> int:
    if not _co("alembic"):
        return _thieu("alembic")
    if not Path("alembic.ini").exists():
        print(
            "Không thấy alembic.ini ở thư mục này.\n"
            "Migration cần một lần dựng: `alembic init migrations` rồi trỏ env.py vào\n"
            "cấu hình của bạn — xem docs/migrations.md."
        )
        return 1

    if args.viec == "create":
        if not args.message:
            print('Thiếu mô tả. Dùng: pym migrate create -m "them cot phone"')
            return 1
        return _goi("alembic", "revision", "--autogenerate", "-m", args.message)

    return _goi("alembic", *{
        "up": ("upgrade", "head"),
        "down": ("downgrade", "-1"),
        "history": ("history", "--indicate-current"),
        "sql": ("upgrade", "head", "--sql"),
    }[args.viec])
