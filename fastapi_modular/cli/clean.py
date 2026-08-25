"""`fam clean` — xoá cache và bản dựng, không đụng vào code hay dữ liệu."""

from __future__ import annotations

import shutil
from pathlib import Path

# Cố ý KHÔNG có `data/` hay `*.db`: đó là dữ liệu, không phải rác. Xoá dữ liệu
# phải là việc người ta gõ tay, không bao giờ là tác dụng phụ của "dọn cache".
DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
           ".pytype", "htmlcov", "build", "dist", ".eggs")
FILE_SUFFIX = ("*.pyc", "*.pyo", ".coverage", "coverage.xml")


def clean(root: Path | None = None) -> int:
    root = (root or Path(".")).resolve()
    delete: list[str] = []

    for name in DIRS:
        for d in root.rglob(name):
            if d.is_dir() and ".venv" not in d.parts:
                shutil.rmtree(d, ignore_errors=True)
                delete.append(str(d.relative_to(root)))
    for d in root.rglob("*.egg-info"):
        if ".venv" not in d.parts:
            shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
            delete.append(str(d.relative_to(root)))
    for patterns in FILE_SUFFIX:
        for f in root.rglob(patterns):
            if ".venv" not in f.parts and f.is_file():
                f.unlink()
                delete.append(str(f.relative_to(root)))

    print(f"Đã xoá {len(delete)} thứ." if delete else "Không có gì để xoá.")
    for d in sorted(delete)[:20]:
        print(f"    {d}")
    if len(delete) > 20:
        print(f"    ... và {len(delete) - 20} thứ nữa")
    return 0
