"""`fam test`, `fam lint`, `fam migrate`, `fam build`, `fam publish` — gọi hộ công cụ.

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

_WHAT = {
    "pytest": "fam install dev",
    "ruff": "fam install dev",
    "build": "pip install build",
    "twine": "pip install twine",
    "alembic": 'pip install "fastapi-modular[sqlite]"   # hoặc [postgres]',
}


def _has(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _missing(name: str) -> int:
    print(f"Chưa cài {name}. Chạy: {_WHAT[name]}")
    return 1


def _call(module: str, *args: str) -> int:
    """Chạy công cụ như một tiến trình con, giữ nguyên mã thoát của nó."""
    return subprocess.call([sys.executable, "-m", module, *args])


def run_tool(args: Namespace) -> int:
    if args.command == "test":
        if not _has("pytest"):
            return _missing("pytest")
        return _call("pytest", "-q", *args.them)

    if args.command == "lint":
        if not _has("ruff"):
            return _missing("ruff")
        ruff_args = ["check", *(["--fix"] if args.fix else [])]
        return _call("ruff", *ruff_args, *args.path)

    if args.command == "build":
        return _build(args)

    if args.command == "publish":
        return _publish(args)

    return _migrate(args)


def _build(args: Namespace) -> int:
    if not _has("build"):
        return _missing("build")
    if not Path("pyproject.toml").exists():
        print("Không thấy pyproject.toml ở thư mục này — không có gì để dựng.")
        return 1
    if not args.no_clean:
        # dist/ cũ còn sót lại là cách phổ biến nhất để đẩy nhầm một bản cũ lên
        # PyPI: twine upload dist/* lấy TẤT CẢ file trong đó.
        shutil.rmtree("dist", ignore_errors=True)
    return _call("build")


def _publish(args: Namespace) -> int:
    if not _has("twine"):
        return _missing("twine")
    packet = sorted(Path("dist").glob("*")) if Path("dist").exists() else []
    if not packet:
        print("dist/ trống. Chạy `fam build` trước.")
        return 1

    print("Sắp đẩy lên", "TestPyPI" if args.test else "PyPI", "các file:")
    for f in packet:
        print(f"    {f.name}")
    store = ["--repository", "testpypi"] if args.test else []
    return _call("twine", "upload", *store, *[str(f) for f in packet])


def _migrate(args: Namespace) -> int:
    if not _has("alembic"):
        return _missing("alembic")
    if not Path("alembic.ini").exists():
        print(
            "Không thấy alembic.ini ở thư mục này.\n"
            "Migration cần một lần dựng: `alembic init migrations` rồi trỏ env.py vào\n"
            "cấu hình của bạn — xem docs/migrations.md."
        )
        return 1

    if args.viec == "create":
        if not args.message:
            print('Thiếu mô tả. Dùng: fam migrate create -m "them cot phone"')
            return 1
        return _call("alembic", "revision", "--autogenerate", "-m", args.message)

    return _call("alembic", *{
        "up": ("upgrade", "head"),
        "down": ("downgrade", "-1"),
        "history": ("history", "--indicate-current"),
        "sql": ("upgrade", "head", "--sql"),
    }[args.viec])
