"""`fam provider <họ> <tên>` — sinh khung cho một provider cắm được.

Lần đầu gọi cho một họ thì dựng luôn cả họ: `__init__.py` (lớp token DI) và
`capabilities.py` (interface năng lực mẫu). Lần sau chỉ thêm file provider, và
**đọc `capabilities.py` để sinh sẵn stub đúng các method cần viết** — nên
provider thứ hai trở đi gần như chỉ còn việc điền thân hàm.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

DEFAULT_ROOT = Path("src/providers")

TEN_HOP_LE = re.compile(r"^[a-z][a-z0-9_-]*$")


def pascal(name: str) -> str:
    return "".join(phan.capitalize() for phan in re.split(r"[-_]", name) if phan)


def _capabilities_trong(duong_dan: Path) -> list[tuple[str, list[str]]]:
    """Đọc capabilities.py -> [(tên lớp, [tên method abstract])].

    Đọc bằng `ast` chứ không import: file có thể đang viết dở, và sinh code
    không nên chạy code của người dùng.
    """
    if not duong_dan.exists():
        return []

    cay = ast.parse(duong_dan.read_text(encoding="utf-8"))
    ket_qua: list[tuple[str, list[str]]] = []
    for node in cay.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(getattr(b, "id", "") == "ABC" for b in node.bases):
            continue
        methods = [
            con.name
            for con in node.body
            if isinstance(con, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(getattr(d, "id", "") == "abstractmethod" for d in con.decorator_list)
        ]
        ket_qua.append((node.name, methods))
    return ket_qua


def _chu_ky(duong_dan: Path, lop: str, method: str) -> tuple[str, bool]:
    """Lấy lại nguyên chữ ký của method abstract, để stub khớp 100%."""
    cay = ast.parse(duong_dan.read_text(encoding="utf-8"))
    for node in cay.body:
        if isinstance(node, ast.ClassDef) and node.name == lop:
            for con in node.body:
                if isinstance(con, (ast.FunctionDef, ast.AsyncFunctionDef)) and con.name == method:
                    args = ast.unparse(con.args)
                    kieu = f" -> {ast.unparse(con.returns)}" if con.returns else ""
                    return f"({args}){kieu}", isinstance(con, ast.AsyncFunctionDef)
    return "(self)", True


def render_family(family: str) -> dict[str, str]:
    """Hai file khung của một họ mới."""
    nang_luc = f"{pascal(family)}Basic"

    init = f'''"""Họ provider **{family}** — các bản hiện thực cắm được.

Thả một file `<tên>.py` mang `@provider("<tên>")` vào thư mục này là xong:
không sửa service, không sửa main.py, không có danh sách import nào phải nhớ.

Service khai ĐÚNG năng lực nó cần:

    def __init__(self, x: Providers[{nang_luc}]) -> None: ...
"""
'''

    caps = f'''"""Năng lực của họ **{family}** — provider CÓ THỂ hiện thực cái nào tuỳ nó.

Tách nhỏ theo năng lực thay vì gộp một interface to: bản hiện thực nào không
làm được việc gì thì đơn giản là không kế thừa interface đó, và
`require(tên, NăngLực)` sẽ trả lỗi 501 nói rõ thiếu gì — thay vì bắt nó viết
method rỗng chỉ để thoả ABC.
"""

from abc import ABC, abstractmethod


class {nang_luc}(ABC):
    """Việc mà MỌI provider {family} đều phải làm được."""

    @abstractmethod
    async def ping(self) -> bool:
        """Kiểm tra dịch vụ có sống không."""


# Thêm năng lực tuỳ chọn ở đây, ví dụ:
#
# class {pascal(family)}Advanced(ABC):
#     @abstractmethod
#     async def lam_viec_kho(self, tham_so: str) -> str: ...
'''
    return {"__init__.py": init, "capabilities.py": caps}


def render_provider(family: str, name: str, caps: list[tuple[str, list[str]]], goc: Path) -> str:
    lop = f"{pascal(name)}{pascal(family)}"
    tep_caps = goc / family / "capabilities.py"

    if not caps:
        return f'''"""Provider `{name}` của họ {family}."""

from fastapi_modular import provider


@provider("{name}")
class {lop}:
    """Chưa kế thừa năng lực nào — thêm interface từ capabilities.py vào đây."""
'''

    ten_caps = [ten for ten, _ in caps]
    than: list[str] = []
    for ten_cap, methods in caps:
        than.append(f"    # ---- {ten_cap} " + "-" * max(0, 60 - len(ten_cap)))
        for method in methods:
            chu_ky, la_async = _chu_ky(tep_caps, ten_cap, method)
            tu_khoa = "async def" if la_async else "def"
            than.append(
                f"    {tu_khoa} {method}{chu_ky}:\n"
                f'        raise NotImplementedError("{lop}.{method} chưa được viết")\n'
            )

    return f'''"""Provider `{name}` của họ {family}.

Mỗi method dưới đây tương ứng một năng lực khai trong `capabilities.py`. Không
làm được việc nào thì **bỏ interface đó khỏi danh sách kế thừa** rồi xoá method
đi — đừng để lại thân rỗng.
"""

from fastapi_modular import provider

from {str(goc).replace("/", ".")}.{family}.capabilities import (
    {", ".join(ten_caps)},
)


@provider("{name}")
class {lop}({", ".join(ten_caps)}):
    """Bản hiện thực {name}."""

{chr(10).join(than)}'''


def _write(target: Path, files: dict[str, str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        duong_dan = target / relative
        duong_dan.parent.mkdir(parents=True, exist_ok=True)
        duong_dan.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fam provider")
    parser.add_argument("family", help="tên họ, dạng số ít viết thường: payment, sms, device")
    parser.add_argument("name", help="tên provider: vnpay, viettel, dahua")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)

    family = args.family.strip().lower().replace("-", "_")
    name = args.name.strip().lower()

    for nhan, gia_tri in (("họ", family), ("provider", name)):
        if not TEN_HOP_LE.match(gia_tri):
            print(f"Tên {nhan} không hợp lệ: {gia_tri!r}. Chữ thường, số, gạch ngang hoặc _.")
            return 1

    goc: Path = args.root
    thu_muc = goc / family
    ho_moi = not thu_muc.exists()

    tep_provider = thu_muc / f"{name}.py"
    if tep_provider.exists():
        print(f"Đã có {tep_provider} rồi. Xoá đi hoặc chọn tên khác.")
        return 1

    if not (goc / "__init__.py").exists():
        _write(goc, {"__init__.py": '"""Các provider cắm được, nhóm theo họ."""\n'})

    if ho_moi:
        _write(thu_muc, render_family(family))

    caps = _capabilities_trong(thu_muc / "capabilities.py")
    _write(thu_muc, {f"{name}.py": render_provider(family, name, caps, goc)})

    if ho_moi:
        print(f"Đã tạo họ '{family}' và provider '{name}':")
        for ten in ("__init__.py", "capabilities.py", f"{name}.py"):
            print(f"    {thu_muc / ten}")
        print()
        print("Việc tiếp theo:")
        print(f"  1. Sửa {thu_muc / 'capabilities.py'} cho đúng nghiệp vụ của bạn")
        print(f"  2. Viết thân các method trong {tep_provider}")
        print(f"  3. Service nhận sổ: def __init__(self, x: Providers[{pascal(family)}Basic])")
    else:
        print(f"Đã thêm provider '{name}' vào họ '{family}':")
        print(f"    {tep_provider}")
        if caps:
            print(f"  (sinh sẵn stub cho {', '.join(t for t, _ in caps)})")
        print()
        print("Việc tiếp theo: viết thân các method, bỏ interface nào nó không làm được.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
