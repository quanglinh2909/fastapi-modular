"""Ghi thành phần vừa cài vào requirements.txt, để người khác pull về là có đủ.

Vấn đề: `fam install sqlite` cài gói vào venv của MÁY BẠN. Đồng nghiệp clone
repo về thì không có gì nói cho họ biết dự án cần sqlalchemy + aiosqlite +
alembic — `pip install -r requirements.txt` sẽ thiếu, và app chết lúc khởi động
với `ComponentNotEnabledError`. Đây đúng là việc `package.json` làm cho `npm i`.

Cách ghi: **một dòng duy nhất, dùng extras**, thay vì liệt kê từng gói con:

    fastapi-modular[redis,sqlite]>=0.4.0

Vì sao extras chứ không phải danh sách gói phẳng: khoảng phiên bản của
sqlalchemy/motor/... là chuyện của fastapi-modular, và nó đổi theo từng bản.
Chép phẳng ra requirements.txt là đóng băng một bản chụp sẽ lạc hậu trong im
lặng — y như `package.json` ghi `"express": "^4"` chứ không ghi cả cây phụ
thuộc. Cài thì vẫn cài thẳng từng gói (xem `install.py`), chỉ phần GHI NHỚ mới
dùng extras.

Chỗ ghi, theo thứ tự ưu tiên:

1. `requirements.txt` đang có -> sửa đúng dòng fastapi-modular trong đó.
2. Không có, nhưng `pyproject.toml` có nhắc fastapi-modular -> sửa dòng đó.
3. Không có gì cả -> tạo `requirements.txt`.

Thành phần `dev` (pytest, ruff...) đi vào `requirements-dev.txt`: nó không phải
thứ production cần, và trộn chung là bắt server cài cả pytest.
"""

from __future__ import annotations

import re
from pathlib import Path

DIST = "fastapi-modular"

# Bắt cả `fastapi_modular` (gạch dưới) vì pip chấp nhận cả hai cách viết, và
# người ta hay gõ theo tên import.
_LINE = re.compile(r"fastapi[-_]modular(?:\s*\[(?P<extras>[^\]]*)\])?", re.IGNORECASE)

# Chỉ bắt `>=`. `==`, `~=`, `<` là ràng buộc người dùng cố ý đặt — đừng đụng.
_FLOOR = re.compile(r">=\s*(?P<version>[0-9][0-9A-Za-z.+!-]*)")

HEADER = """\
# Thư viện của dự án. Người khác clone về chỉ cần:
#     pip install -r {file}
#
# `fam install <thành-phần>` tự cập nhật dòng dưới — cài thêm redis thì nó
# thành fastapi-modular[redis,sqlite]>=... Đừng liệt kê tay sqlalchemy hay
# motor: khoảng phiên bản của chúng do fastapi-modular giữ.
"""


def extras_of(component: str, alias: dict[str, str], packages: dict[str, list[str]]) -> set[str]:
    """Thành phần trên dòng lệnh -> tên extra thật trong pyproject.

    `ws-redis` cài cùng thư viện với `redis` nên ghi nhớ cũng là `redis`;
    `all` là mọi extra chạy thật, KHÔNG gồm `dev`.
    """
    if component == "all":
        return {name for name in packages if name != "dev"}
    return {alias.get(component, component)}


def _numbers(version: str) -> tuple[int, ...]:
    """Phần số của một phiên bản, để so được `0.2.1` với `0.10.0`.

    So chuỗi thì `"0.10.0" < "0.2.1"` — sai. Chỉ lấy phần số đầu mỗi đoạn nên
    `1.0.0rc1` thành `(1, 0, 0)`, tức bản rc coi như ngang bản chính thức: chấp
    nhận được ở đây, vì việc này chỉ NÂNG sàn chứ không hạ.
    """
    out = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _raise_floor(text: str, version: str) -> str:
    """Nâng `>=` lên phiên bản đang dùng, nếu nó cao hơn.

    Vì sao cần: cài `fam install redis` bằng fastapi-modular 0.4.0 nhưng
    requirements.txt vẫn ghi `>=0.2.1` thì đồng nghiệp được phép cài 0.2.1 —
    bản có thể chưa có extra đó, hoặc chưa có API bạn vừa dùng. Sàn phải nói
    đúng bản THẬT SỰ đang chạy.

    Chỉ đụng `>=`. `==0.2.1` hay `~=0.2` là quyết định của người dùng; tự nâng
    những cái đó là ghi đè ý định của họ.
    """
    match = _FLOOR.search(text)
    if match is None:
        return text
    if _numbers(version) <= _numbers(match.group("version")):
        return text
    return text[: match.start()] + f">={version}" + text[match.end() :]


def _merge(line: str, extras: set[str], version: str) -> str:
    """Gộp extras vào đúng dòng và nâng sàn `>=`, giữ nguyên mọi thứ khác."""
    match = _LINE.search(line)
    if match is None:
        return line
    current = {e.strip() for e in (match.group("extras") or "").split(",") if e.strip()}
    merged = sorted(current | extras)
    new = DIST + (f"[{','.join(merged)}]" if merged else "")
    return line[: match.start()] + new + _raise_floor(line[match.end() :], version)


def _update(path: Path, extras: set[str], version: str) -> tuple[bool, bool]:
    """Sửa file tại chỗ. Trả về (có đổi gì không, có phải vừa tạo mới không)."""
    fresh = not path.exists()
    text = "" if fresh else path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") or not _LINE.search(line):
            continue
        merged = _merge(line, extras, version)
        if merged == line:
            return False, False
        lines[i] = merged
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True, False

    # Chưa có dòng nào nhắc fastapi-modular: thêm mới.
    body = HEADER.format(file=path.name) if fresh else text.rstrip("\n") + "\n"
    body += f"{_merge(DIST, extras, version)}>={version}\n"
    path.write_text(body, encoding="utf-8")
    return True, fresh


def record(component: str, root: Path, extras: set[str], version: str) -> str:
    """Ghi nhớ thành phần, trả về một dòng nói đã làm gì (rỗng = không làm gì)."""
    if not extras:
        return ""

    if component == "dev":
        target = root / "requirements-dev.txt"
    elif (root / "requirements.txt").exists():
        target = root / "requirements.txt"
    elif _mentions(root / "pyproject.toml"):
        target = root / "pyproject.toml"
    else:
        target = root / "requirements.txt"

    changed, fresh = _update(target, extras, version)
    if not changed:
        return f"{target.name} đã ghi sẵn '{component}' rồi."
    verb = "Đã tạo" if fresh else "Đã cập nhật"
    return (
        f"{verb} {target.name} — người khác clone về chỉ cần "
        f"`pip install -r {target.name}`."
    )


def _mentions(path: Path) -> bool:
    if not path.exists():
        return False
    return bool(_LINE.search(path.read_text(encoding="utf-8")))
