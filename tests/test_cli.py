"""Test cho lệnh `fam` / `fastapi-modular`.

`init` là lệnh nguy hiểm nhất trong ba lệnh: nó ghi vào thư mục người dùng đang
đứng, nơi có thể đã có code. Nên phần lớn test ở đây là về chuyện KHÔNG ghi đè.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastapi_modular.cli.main import main
from fastapi_modular.cli.new_project import clean_name


def _da_co(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_init_do_file_vao_thu_muc_hien_tai(tmp_path: Path):
    """Không tạo thêm một cấp thư mục nào."""
    assert main(["init", "--root", str(tmp_path)]) == 0

    files = _da_co(tmp_path)
    assert "src/main.py" in files
    assert "src/api/health/health_controller.py" in files
    assert ".env" in files
    # Không có thư mục con mang tên dự án.
    assert not (tmp_path / tmp_path.name).exists()


def test_init_lay_ten_du_an_theo_thu_muc(tmp_path: Path):
    duan = tmp_path / "Dự Án Mới"
    duan.mkdir()
    assert main(["init", "--root", str(duan)]) == 0
    assert "APP_NAME=du-an-moi" in (duan / ".env").read_text(encoding="utf-8")


def test_init_khong_ghi_de_file_dang_co(tmp_path: Path):
    """Chạy trong thư mục đang có code phải an toàn."""
    (tmp_path / ".env").write_text("CUA_TOI=1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# đừng đụng vào\n", encoding="utf-8")

    assert main(["init", "--root", str(tmp_path)]) == 0

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "CUA_TOI=1\n"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# đừng đụng vào\n"
    assert (tmp_path / "src" / "main.py").exists()      # phần còn thiếu vẫn được thêm


def test_init_chay_lai_lan_hai_khong_lam_gi(tmp_path: Path, capsys: pytest.CaptureFixture):
    main(["init", "--root", str(tmp_path)])
    before = {f: (tmp_path / f).read_text(encoding="utf-8") for f in _da_co(tmp_path)}

    assert main(["init", "--root", str(tmp_path)]) == 0
    assert "không phải làm gì" in capsys.readouterr().out
    assert {f: (tmp_path / f).read_text(encoding="utf-8") for f in _da_co(tmp_path)} == before


def test_new_tao_thu_muc_con(tmp_path: Path):
    assert main(["new", "blog", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "blog" / "src" / "main.py").exists()


def test_new_tu_choi_thu_muc_khong_rong(tmp_path: Path, capsys: pytest.CaptureFixture):
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "co-san.txt").write_text("x", encoding="utf-8")

    assert main(["new", "blog", "--root", str(tmp_path)]) == 1
    assert "fam init" in capsys.readouterr().out, "phải chỉ đường sang init"


def test_module_sinh_dung_ten_file(tmp_path: Path):
    assert main(["init", "--root", str(tmp_path)]) == 0
    assert main(["module", "alerts", "--root", str(tmp_path / "src" / "api")]) == 0

    module = tmp_path / "src" / "api" / "alerts"
    assert (module / "alert_controller.py").is_file()
    assert (module / "dto" / "alert_dto.py").is_file()


def test_env_ghi_bien_vao_dung_file(tmp_path: Path):
    env = tmp_path / ".env"
    assert main(["env", "sqlite", "--file", str(env)]) == 0
    assert "APP_DB__DRIVER=sqlite" in env.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("raw_", "expected"),
    [
        ("Dự Án Mới", "du-an-moi"),
        ("shop_online", "shop-online"),
        ("My Project 2", "my-project-2"),
        ("---", "app"),                       # không còn ký tự nào dùng được
    ],
)
def test_lam_sach_ten_thu_muc(raw_: str, expected: str):
    assert clean_name(raw_) == expected


# --------------------------------------------------- code sinh ra phải tiếng Anh
# Tài liệu và comment của dự án viết tiếng Việt — đó là chủ ý. Nhưng CODE sinh ra
# cho người dùng thì tên hàm, tên biến và tên sự kiện log phải là tiếng Anh: đó
# là thứ họ đọc trong log production và gõ trong IDE.
_TU_TIENG_VIET = frozenset({
    "ha", "tang", "san", "sang", "dang", "tat", "nap", "hang", "doi", "viec",
    "gui", "nhan", "tin", "xoa", "tao", "lay", "cua", "toi", "khoi", "luc",
    "thu", "muc", "bien", "gia", "tri", "duong", "dan", "loi", "hong",
})


def _ten_trong_code(root: Path) -> set[str]:
    """Định danh + tên sự kiện log trong code, bỏ qua comment và docstring."""
    import ast

    label: set[str] = set()
    for f in sorted(root.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                label.add(n.name)
            elif isinstance(n, ast.Name):
                label.add(n.id)
            elif isinstance(n, ast.arg):
                label.add(n.arg)
            elif isinstance(n, ast.alias):
                label.add(n.asname or n.name.split(".")[-1])
            elif (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "log"
                and n.args
                and isinstance(n.args[0], ast.Constant)
            ):
                label.add(str(n.args[0].value))
    return label


def test_code_sinh_ra_khong_co_dinh_danh_tieng_viet(tmp_path: Path):
    assert main(["init", "--root", str(tmp_path)]) == 0
    assert main(["module", "alerts", "--gateway", "--consumer",
                 "--root", str(tmp_path / "src" / "api")]) == 0

    pham: list[str] = []
    for label in _ten_trong_code(tmp_path):
        # Khớp theo TỪNG ĐOẠN, không phải chuỗi con: "settings" chứa "tin" và
        # "status" chứa "tat" nhưng cả hai đều là tiếng Anh.
        doan = {d for d in label.replace(".", "_").lower().split("_") if d}
        if doan & _TU_TIENG_VIET:
            pham.append(label)

    assert not pham, "code sinh ra còn tên tiếng Việt: " + ", ".join(sorted(pham))


def test_gitignore_sinh_ra_che_duoc_thu_can_che(tmp_path: Path):
    """.env lọt vào git là lộ DSN và mật khẩu — đây là dòng quan trọng nhất."""
    assert main(["init", "--root", str(tmp_path)]) == 0
    patterns = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()

    can_co = [
        ".env",             # bí mật
        "data/",            # file .db do app sinh ra
        "*.db",
        "__pycache__/",
        ".venv/",
        ".pytest_cache/",
        ".ruff_cache/",
        ".mypy_cache/",
        "dist/",
        "*.egg-info",
        ".idea/",           # JetBrains
        ".vscode/",         # VS Code
        ".DS_Store",        # macOS
        "Thumbs.db",        # Windows
    ]
    thieu = [d for d in can_co if d not in patterns]
    assert not thieu, f".gitignore sinh ra còn thiếu: {thieu}"

    # Nhưng KHÔNG được che code của người dùng.
    assert "src/" not in patterns
    assert "*.py" not in patterns


# --------------------------------------------------------- fam install
def test_extras_khop_pyproject():
    """Bảng gói trong CLI phải khớp `[project.optional-dependencies]`.

    `fam install` cài THẲNG các gói phụ thuộc chứ không chạy
    `pip install "fastapi_modular[x]"` — nếu không thì pip đi tìm chính fastapi_modular trên
    PyPI và hỏng với bản cài từ .whl hoặc `pip install -e .`. Cái giá của lựa
    chọn đó là danh sách gói nằm hai nơi; test này giữ chúng bằng nhau.
    """
    import sys

    if sys.version_info < (3, 11):
        pytest.skip("tomllib có từ 3.11; CI đã chạy test này ở các bản mới hơn")
    import tomllib

    from fastapi_modular.cli.install import PACKAGE

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]

    # `all` chỉ gộp các extra khác lại, không tự khai gói nào.
    can_so = {k: v for k, v in extras.items() if k != "all"}
    assert set(PACKAGE) == set(can_so), "thiếu/thừa thành phần so với pyproject"
    for label, packet in can_so.items():
        assert PACKAGE[label] == packet, f"danh sách gói của '{label}' lệch với pyproject"


def test_moi_thanh_phan_deu_biet_ghi_env_hoac_noi_ro_la_khong():
    from fastapi_modular.cli.configure_env import BLOCKS
    from fastapi_modular.cli.install import COMPONENTS, ENV_BLOCK

    for label, block in ENV_BLOCK.items():
        assert block in BLOCKS, f"'{label}' trỏ vào khối .env không tồn tại: {block}"
    # dev/all là công cụ, không có biến cấu hình — có mặt nhưng không ghi .env.
    assert {"dev", "all"} <= set(COMPONENTS)
    assert "dev" not in ENV_BLOCK and "all" not in ENV_BLOCK


def test_install_tu_choi_thanh_phan_la(capsys: pytest.CaptureFixture):
    from fastapi_modular.cli.install import install

    assert install("khong-co-that") == 1
    assert "Không biết thành phần" in capsys.readouterr().out


def test_clean_xoa_cache_nhung_giu_du_lieu(tmp_path: Path):
    from fastapi_modular.cli.clean import clean

    (tmp_path / "src" / "__pycache__").mkdir(parents=True)
    (tmp_path / "src" / "__pycache__" / "a.pyc").write_text("x")
    (tmp_path / ".ruff_cache").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "app.db").write_text("dữ liệu thật")
    (tmp_path / "src" / "main.py").write_text("code thật")

    assert clean(tmp_path) == 0

    assert not (tmp_path / "src" / "__pycache__").exists()
    assert not (tmp_path / ".ruff_cache").exists()
    assert not (tmp_path / "dist").exists()
    # Dọn cache KHÔNG được xoá dữ liệu hay code.
    assert (tmp_path / "data" / "app.db").read_text() == "dữ liệu thật"
    assert (tmp_path / "src" / "main.py").read_text() == "code thật"


# ------------------------------------------------- Makefile chỉ là lối tắt
# Người dùng thư viện KHÔNG có Makefile. Nên mọi việc `make` làm được đều phải
# gõ được bằng `fam`, không thì tài liệu và trải nghiệm bị chia đôi.
_MAKE_SANG_FAM = {
    "help": "--help",
    "dev": "dev",
    "run": "run",
    "module": "module",
    "gateway": "module",              # --gateway-only
    "consumer": "module",             # --consumer-only
    "migrate": "migrate",
    "migrate-create": "migrate",
    "migrate-down": "migrate",
    "migrate-history": "migrate",
    "migrate-sql": "migrate",
    "lint": "lint",
    "lint-fix": "lint",               # --fix
    "test": "test",
    # `make install` cài chính khung ở chế độ chỉnh sửa — chỉ có nghĩa khi bạn
    # đang phát triển fastapi_modular. Người dùng thư viện dùng `pip install fastapi-modular`.
    "install": "install",
    "install-dev": "install",
    "install-sqlite": "install",
    "install-postgres": "install",
    "install-mongo": "install",
    "install-redis": "install",
    "install-ws-redis": "install",
    "install-rabbitmq": "install",
    "install-mqtt": "install",
    "install-kafka": "install",
    "info": "info",
    "build": "build",
    "publish": "publish",
    "publish-test": "publish",        # --test
    "clean": "clean",
}


def test_moi_target_makefile_deu_co_lenh_fam():
    import re

    makefile = Path("Makefile")
    if not makefile.exists():
        pytest.skip("chạy ngoài repo")

    targets = set(re.findall(r"^([a-z-]+):.*##", makefile.read_text(encoding="utf-8"), re.M))
    thieu = targets - set(_MAKE_SANG_FAM)
    assert not thieu, f"target Makefile chưa có lệnh fam tương ứng: {sorted(thieu)}"

    # Và mọi lệnh trong bảng phải thật sự tồn tại trong CLI.
    command = {v.split()[0] for v in _MAKE_SANG_FAM.values() if not v.startswith("-")}
    for label in sorted(command):
        with pytest.raises(SystemExit) as thoat:
            main([label, "--help"])
        assert thoat.value.code == 0, f"fam {label} không chạy"


def test_makefile_khong_tu_viet_lai_viec_cua_fam():
    """Mỗi target chỉ gọi lại `fam`, để hai đường không bao giờ lệch nhau."""
    import re

    makefile = Path("Makefile")
    if not makefile.exists():
        pytest.skip("chạy ngoài repo")

    tu_viet: list[str] = []
    tiep_dong = False
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if not line.startswith("\t"):
            tiep_dong = False
            continue
        content, truoc_do = line.strip(), tiep_dong
        tiep_dong = line.rstrip().endswith("\\")
        if truoc_do or content.startswith(("@", "#")):
            continue                       # dòng nối tiếp, hoặc lệnh im lặng
        if "$(FAM)" in line or "$(PIP)" in line:
            continue                       # gọi lại fam, hoặc cài chính khung
        if re.match(r"(test|echo) ", content):
            continue                       # kiểm tham số trước khi gọi
        tu_viet.append(content)
    assert not tu_viet, "target tự viết lại việc của fam: " + " | ".join(tu_viet)


# ------------------------------------------ README sinh ra phải nói đúng sự thật
def test_readme_sinh_ra_chi_nhac_lenh_co_that(tmp_path: Path):
    """README của dự án là thứ người ta đọc đầu tiên — sai một lệnh là mất niềm tin."""
    import re

    assert main(["init", "--root", str(tmp_path)]) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    from fastapi_modular.cli.main import explain

    lenh_co_that = sorted(set(_MAKE_SANG_FAM.values()) | {"module", "env", "install"})
    la: list[str] = []
    for m in re.finditer(r"`?fam (\w[\w-]*)", readme):
        from_ = m.group(1)
        if from_ == "-help":
            continue
        # Giải nghĩa đúng như CLI thật: README được phép dùng viết tắt
        # (`fam mo alerts`), nhưng viết tắt đó phải ra một lệnh CÓ THẬT.
        if explain(from_, lenh_co_that, "lệnh") not in lenh_co_that:
            la.append(from_)
    assert not la, f"README nhắc lệnh không có: {sorted(set(la))}"


def test_readme_sinh_ra_chi_tro_vao_file_co_that(tmp_path: Path):
    import re

    assert main(["init", "--root", str(tmp_path)]) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    thieu = [
        d
        for d in re.findall(r"src/[\w/]+\.py", readme)
        if not (tmp_path / d).exists()
    ]
    assert not thieu, f"README trỏ vào file không tồn tại: {thieu}"


def test_readme_sinh_ra_khong_tro_vao_docs_cuc_bo(tmp_path: Path):
    """Dự án người dùng không có thư mục docs/ — link phải trỏ ra bản trên mạng."""
    assert main(["init", "--root", str(tmp_path)]) == 0
    for label in ("README.md", ".env"):
        content = (tmp_path / label).read_text(encoding="utf-8")
        assert "docs/config.md" not in content or "http" in content, (
            f"{label} trỏ vào docs/ cục bộ, mà dự án sinh ra không có thư mục đó"
        )


def test_vi_du_code_trong_readme_sinh_ra_chay_duoc(tmp_path: Path):
    """Đoạn AppSettings trong README phải khớp file config.py sinh ra kèm."""
    assert main(["init", "--root", str(tmp_path)]) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    config = (tmp_path / "src" / "core" / "config.py").read_text(encoding="utf-8")

    for line in ('class AppSettings(Settings):',
                 'team_name: str = Field(default="", alias="APP_TEAM_NAME")'):
        assert line in readme, f"README thiếu: {line}"
        assert line in config, f"config.py sinh ra không khớp README: {line}"


# ------------------------------------------------------------- viết tắt lệnh
def _moi_lenh() -> list[str]:
    """Tên mọi lệnh, đọc thẳng từ parser thật."""
    import argparse

    from fastapi_modular.cli import main as M

    class _Tom(Exception):
        def __init__(self, p): self.p = p

    root = argparse.ArgumentParser.parse_args
    argparse.ArgumentParser.parse_args = lambda self, *a, **k: (_ for _ in ()).throw(_Tom(self))
    try:
        M._main([])
    except _Tom as on:
        parser = on.p
    finally:
        argparse.ArgumentParser.parse_args = root

    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return list(sub.choices)


@pytest.mark.parametrize(
    ("go", "that_ra_la"),
    [
        ("mo", "module"),
        ("modu", "module"),
        ("mi", "migrate"),
        ("ini", "init"),
        ("ins", "install"),
        ("inf", "info"),
        ("d", "dev"),
        ("r", "run"),
        ("t", "test"),
        ("l", "lint"),
        ("c", "clean"),
        ("b", "build"),
        # "p" giờ nhập nhằng giữa publish và provider — cố ý, `fam` hỏi lại.
        ("pu", "publish"),
        ("pr", "provider"),
        ("e", "env"),
        ("n", "new"),
        ("module", "module"),          # gõ đầy đủ vẫn phải chạy
    ],
)
def test_viet_tat_lenh(go: str, that_ra_la: str):
    from fastapi_modular.cli.main import _expand_abbrev, main

    with pytest.raises(SystemExit) as thoat:
        main([go, "--help"])
    assert thoat.value.code == 0
    # Lấy danh sách lệnh từ chính parser, đừng chép tay: chép tay thì thêm
    # lệnh mới mà quên sửa ở đây là test vẫn xanh trong khi thực tế đã đổi.
    assert _expand_abbrev([go], _moi_lenh()) == [that_ra_la]


@pytest.mark.parametrize(("go", "khop_voi"), [("m", "migrate, module"),
                                              ("i", "info, init, install")])
def test_viet_tat_nhap_nhang_thi_hoi_lai(go: str, khop_voi: str):
    """Đoán bừa ở đây nghĩa là chạy nhầm lệnh — thà báo lỗi."""
    from fastapi_modular.cli.main import main

    with pytest.raises(SystemExit) as thoat:
        main([go])
    assert khop_voi in str(thoat.value)


def test_viet_tat_ca_tham_so_dang_danh_sach(tmp_path: Path):
    from fastapi_modular.cli.main import main

    assert main(["e", "sq", "--file", str(tmp_path / ".env")]) == 0
    assert "APP_DB__DRIVER=sqlite" in (tmp_path / ".env").read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as thoat:
        main(["env", "m", "--file", str(tmp_path / ".env")])
    assert "mongodb, mqtt" in str(thoat.value)


def test_viet_tat_khong_dung_cham_gia_tri_cua_nguoi_dung(tmp_path: Path):
    """Chỉ mở rộng ở vị trí LỆNH và tham số danh sách — tên module thì không."""
    from fastapi_modular.cli.main import main

    assert main(["init", "--root", str(tmp_path)]) == 0
    # "ins" ở đây là TÊN MODULE, không phải viết tắt của "install".
    assert main(["mo", "ins", "--root", str(tmp_path / "src" / "api")]) == 0
    assert (tmp_path / "src" / "api" / "ins" / "in_controller.py").is_file()


# ------------------------------------------- bảng "Rút gọn" phải rút gọn thật
def _lenh_that() -> list[str]:
    """Danh sách lệnh lấy từ chính `fam --help`, không chép tay lại."""
    import contextlib
    import io
    import re

    from fastapi_modular.cli.main import main

    ra = io.StringIO()
    with contextlib.redirect_stdout(ra), pytest.raises(SystemExit):
        main(["--help"])
    matched = re.search(r"\{([a-z,\-]+)\}", ra.getvalue())
    assert matched, "không đọc được danh sách lệnh từ --help"
    return matched.group(1).split(",")


def _cap_rut_gon(readme: str) -> list[tuple[str, str]]:
    """Đọc bảng lệnh: trả về [(lệnh đầy đủ, lệnh rút gọn)] theo từng ô."""
    import re

    level: list[tuple[str, str]] = []
    for line in readme.splitlines():
        if not line.startswith("|"):
            continue
        o = [c.strip() for c in line.strip("|").split("|")]
        if len(o) != 3 or not o[1]:
            continue
        day_du = re.findall(r"`fam ([^`]+)`", o[0])
        ngan = re.findall(r"`fam ([^`]+)`", o[1])
        if len(day_du) != len(ngan):           # hai ô phải khớp nhau từng cặp
            level.append((o[0], o[1]))           # để assert bên dưới báo đúng chỗ
            continue
        level.extend(zip(day_du, ngan, strict=True))
    return level


@pytest.mark.parametrize("source", ["repo", "sinh-ra"])
def test_moi_lenh_rut_gon_trong_readme_deu_giai_ra_dung_lenh_do(source: str, tmp_path: Path):
    """Cột "Rút gọn" là lời hứa với người đọc — thêm lệnh mới có thể phá nó.

    Ví dụ: thêm lệnh `down` thì `fam d` hết trỏ về `dev`, README lặng lẽ sai.
    Test này bắt ngay, vì nó giải nghĩa bằng chính hàm CLI dùng lúc chạy thật.
    """
    from fastapi_modular.cli.install import COMPONENTS
    from fastapi_modular.cli.main import explain

    if source == "repo":
        readme = Path("README.md").read_text(encoding="utf-8")
    else:
        assert main(["init", "--root", str(tmp_path)]) == 0
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    command = _lenh_that()
    danh_sach = {"install": COMPONENTS, "env": COMPONENTS}

    level = _cap_rut_gon(readme)
    assert level, "không đọc được bảng lệnh nào — bảng đổi định dạng?"

    for day_du, ngan in level:
        tu_day, tu_ngan = day_du.split(), ngan.split()
        assert len(tu_ngan) <= len(tu_day), f"{ngan!r} nhiều từ hơn {day_du!r}"

        assert explain(tu_ngan[0], command, "lệnh") == tu_day[0], (
            f"`fam {ngan}` không ra `fam {day_du}`"
        )
        # Ô đầy đủ có thể ghi chỗ trống (`<tên>`) hay cờ (`--workers 4`); chỉ đối
        # chiếu khi đó là một giá trị có thật trong danh sách chọn.
        if len(tu_ngan) > 1 and tu_day[0] in danh_sach and tu_day[1] in danh_sach[tu_day[0]]:
            assert explain(tu_ngan[1], danh_sach[tu_day[0]], "thành phần") == tu_day[1], (
                f"`fam {ngan}` không ra `fam {day_du}`"
            )
