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

VALID_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def pascal(name: str) -> str:
    return "".join(parts.capitalize() for parts in re.split(r"[-_]", name) if parts)


def _capabilities_in(path: Path) -> list[tuple[str, list[str]]]:
    """Đọc capabilities.py -> [(tên lớp, [tên method abstract])].

    Đọc bằng `ast` chứ không import: file có thể đang viết dở, và sinh code
    không nên chạy code của người dùng.
    """
    if not path.exists():
        return []

    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: list[tuple[str, list[str]]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(getattr(b, "id", "") == "ABC" for b in node.bases):
            continue
        methods = [
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(getattr(d, "id", "") == "abstractmethod" for d in child.decorator_list)
        ]
        result.append((node.name, methods))
    return result


def _signature(path: Path, cls_name: str, method: str) -> tuple[str, bool]:
    """Lấy lại nguyên chữ ký của method abstract, để stub khớp 100%."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method:
                    args = ast.unparse(child.args)
                    kind = f" -> {ast.unparse(child.returns)}" if child.returns else ""
                    return f"({args}){kind}", isinstance(child, ast.AsyncFunctionDef)
    return "(self)", True


def render_family(family: str) -> dict[str, str]:
    """Hai file khung của một họ mới."""
    capability_names = f"{pascal(family)}Basic"

    init = f'''"""Họ provider **{family}** — các bản hiện thực cắm được.

Thả một file `<tên>.py` mang `@provider("<tên>")` vào thư mục này là xong:
không sửa service, không sửa main.py, không có danh sách import nào phải nhớ.

Service khai ĐÚNG năng lực nó cần:

    def __init__(self, x: Providers[{capability_names}]) -> None: ...
"""
'''

    caps = f'''"""Năng lực của họ **{family}** — provider CÓ THỂ hiện thực cái nào tuỳ nó.

Tách nhỏ theo năng lực thay vì gộp một interface to: bản hiện thực nào không
làm được việc gì thì đơn giản là không kế thừa interface đó, và
`require(tên, NăngLực)` sẽ trả lỗi 501 nói rõ thiếu gì — thay vì bắt nó viết
method rỗng chỉ để thoả ABC.
"""

from abc import ABC, abstractmethod


class {capability_names}(ABC):
    """Việc mà MỌI provider {family} đều phải làm được."""

    @abstractmethod
    async def ping(self) -> bool:
        """Kiểm tra dịch vụ có sống không."""


# Thêm năng lực tuỳ chọn ở đây, ví dụ:
#
# class {pascal(family)}Advanced(ABC):
#     @abstractmethod
#     async def lam_viec_kho(self, tham_so: str) -> str: ...
#
# Từ 3 năng lực trở lên thì tách ra, mỗi năng lực một file. Bộ quét tìm năng lực
# ở MỌI module trong thư mục họ nên tách kiểu gì cũng chạy, không phải khai gì
# thêm và KHÔNG bắt buộc viết re-export — xem docs/providers.md.
'''
    return {"__init__.py": init, "capabilities.py": caps}


def render_provider(family: str, name: str, caps: list[tuple[str, list[str]]], root: Path) -> str:
    cls_name = f"{pascal(name)}{pascal(family)}"
    capabilities_file = root / family / "capabilities.py"

    if not caps:
        return f'''"""Provider `{name}` của họ {family}."""

from fastapi_modular import provider


@provider("{name}")
class {cls_name}:
    """Chưa kế thừa năng lực nào — thêm interface từ capabilities.py vào đây."""
'''

    capability_names_ = [name for name, _ in caps]
    body: list[str] = []
    for capability_name, methods in caps:
        body.append(f"    # ---- {capability_name} " + "-" * max(0, 60 - len(capability_name)))
        for method in methods:
            signature_text, is_async = _signature(capabilities_file, capability_name, method)
            keyword = "async def" if is_async else "def"
            body.append(
                f"    {keyword} {method}{signature_text}:\n"
                f'        raise NotImplementedError("{cls_name}.{method} chưa được viết")\n'
            )

    return f'''"""Provider `{name}` của họ {family}.

Mỗi method dưới đây tương ứng một năng lực khai trong `capabilities.py`. Không
làm được việc nào thì **bỏ interface đó khỏi danh sách kế thừa** rồi xoá method
đi — đừng để lại thân rỗng.
"""

from fastapi_modular import provider

from {str(root).replace("/", ".")}.{family}.capabilities import (
    {", ".join(capability_names_)},
)


@provider("{name}")
class {cls_name}({", ".join(capability_names_)}):
    """Bản hiện thực {name}."""

{chr(10).join(body)}'''


def _write(target: Path, files: dict[str, str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fam provider")
    parser.add_argument("family", help="tên họ, dạng số ít viết thường: payment, sms, device")
    parser.add_argument("name", help="tên provider: vnpay, viettel, dahua")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)

    family = args.family.strip().lower().replace("-", "_")
    name = args.name.strip().lower()

    for label_, value in (("họ", family), ("provider", name)):
        if not VALID_NAME.match(value):
            print(f"Tên {label_} không hợp lệ: {value!r}. Chữ thường, số, gạch ngang hoặc _.")
            return 1

    root: Path = args.root
    directory = root / family
    is_new_family = not directory.exists()

    provider_file = directory / f"{name}.py"
    if provider_file.exists():
        print(f"Đã có {provider_file} rồi. Xoá đi hoặc chọn tên khác.")
        return 1

    if not (root / "__init__.py").exists():
        _write(root, {"__init__.py": '"""Các provider cắm được, nhóm theo họ."""\n'})

    if is_new_family:
        _write(directory, render_family(family))

    caps = _capabilities_in(directory / "capabilities.py")
    _write(directory, {f"{name}.py": render_provider(family, name, caps, root)})

    if is_new_family:
        print(f"Đã tạo họ '{family}' và provider '{name}':")
        for filename in ("__init__.py", "capabilities.py", f"{name}.py"):
            print(f"    {directory / filename}")
        print()
        print("Việc tiếp theo:")
        print(f"  1. Sửa {directory / 'capabilities.py'} cho đúng nghiệp vụ của bạn")
        print(f"  2. Viết thân các method trong {provider_file}")
        print(f"  3. Service nhận sổ: def __init__(self, x: Providers[{pascal(family)}Basic])")
    else:
        print(f"Đã thêm provider '{name}' vào họ '{family}':")
        print(f"    {provider_file}")
        if caps:
            print(f"  (sinh sẵn stub cho {', '.join(t for t, _ in caps)})")
        print()
        print("Việc tiếp theo: viết thân các method, bỏ interface nào nó không làm được.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
