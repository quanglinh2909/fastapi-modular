"""`pym clean` — xoá cache và bản dựng, không đụng vào code hay dữ liệu."""

from __future__ import annotations

import shutil
from pathlib import Path

# Cố ý KHÔNG có `data/` hay `*.db`: đó là dữ liệu, không phải rác. Xoá dữ liệu
# phải là việc người ta gõ tay, không bao giờ là tác dụng phụ của "dọn cache".
THU_MUC = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
           ".pytype", "htmlcov", "build", "dist", ".eggs")
DUOI_FILE = ("*.pyc", "*.pyo", ".coverage", "coverage.xml")


def clean(root: Path | None = None) -> int:
    goc = (root or Path(".")).resolve()
    xoa: list[str] = []

    for ten in THU_MUC:
        for d in goc.rglob(ten):
            if d.is_dir() and ".venv" not in d.parts:
                shutil.rmtree(d, ignore_errors=True)
                xoa.append(str(d.relative_to(goc)))
    for d in goc.rglob("*.egg-info"):
        if ".venv" not in d.parts:
            shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
            xoa.append(str(d.relative_to(goc)))
    for mau in DUOI_FILE:
        for f in goc.rglob(mau):
            if ".venv" not in f.parts and f.is_file():
                f.unlink()
                xoa.append(str(f.relative_to(goc)))

    print(f"Đã xoá {len(xoa)} thứ." if xoa else "Không có gì để xoá.")
    for d in sorted(xoa)[:20]:
        print(f"    {d}")
    if len(xoa) > 20:
        print(f"    ... và {len(xoa) - 20} thứ nữa")
    return 0
