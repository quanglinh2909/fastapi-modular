"""Test cho lệnh `pym` / `pymodular`.

`init` là lệnh nguy hiểm nhất trong ba lệnh: nó ghi vào thư mục người dùng đang
đứng, nơi có thể đã có code. Nên phần lớn test ở đây là về chuyện KHÔNG ghi đè.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pymodular.cli.main import main
from pymodular.cli.new_project import lam_sach_ten


def _da_co(goc: Path) -> set[str]:
    return {str(p.relative_to(goc)) for p in goc.rglob("*") if p.is_file()}


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
    truoc = {f: (tmp_path / f).read_text(encoding="utf-8") for f in _da_co(tmp_path)}

    assert main(["init", "--root", str(tmp_path)]) == 0
    assert "không phải làm gì" in capsys.readouterr().out
    assert {f: (tmp_path / f).read_text(encoding="utf-8") for f in _da_co(tmp_path)} == truoc


def test_new_tao_thu_muc_con(tmp_path: Path):
    assert main(["new", "blog", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "blog" / "src" / "main.py").exists()


def test_new_tu_choi_thu_muc_khong_rong(tmp_path: Path, capsys: pytest.CaptureFixture):
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "co-san.txt").write_text("x", encoding="utf-8")

    assert main(["new", "blog", "--root", str(tmp_path)]) == 1
    assert "pym init" in capsys.readouterr().out, "phải chỉ đường sang init"


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
    ("tho", "mong"),
    [
        ("Dự Án Mới", "du-an-moi"),
        ("shop_online", "shop-online"),
        ("My Project 2", "my-project-2"),
        ("---", "app"),                       # không còn ký tự nào dùng được
    ],
)
def test_lam_sach_ten_thu_muc(tho: str, mong: str):
    assert lam_sach_ten(tho) == mong


# --------------------------------------------------- code sinh ra phải tiếng Anh
# Tài liệu và comment của dự án viết tiếng Việt — đó là chủ ý. Nhưng CODE sinh ra
# cho người dùng thì tên hàm, tên biến và tên sự kiện log phải là tiếng Anh: đó
# là thứ họ đọc trong log production và gõ trong IDE.
_TU_TIENG_VIET = frozenset({
    "ha", "tang", "san", "sang", "dang", "tat", "nap", "hang", "doi", "viec",
    "gui", "nhan", "tin", "xoa", "tao", "lay", "cua", "toi", "khoi", "luc",
    "thu", "muc", "bien", "gia", "tri", "duong", "dan", "loi", "hong",
})


def _ten_trong_code(goc: Path) -> set[str]:
    """Định danh + tên sự kiện log trong code, bỏ qua comment và docstring."""
    import ast

    ten: set[str] = set()
    for f in sorted(goc.rglob("*.py")):
        cay = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(cay):
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                ten.add(n.name)
            elif isinstance(n, ast.Name):
                ten.add(n.id)
            elif isinstance(n, ast.arg):
                ten.add(n.arg)
            elif isinstance(n, ast.alias):
                ten.add(n.asname or n.name.split(".")[-1])
            elif (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "log"
                and n.args
                and isinstance(n.args[0], ast.Constant)
            ):
                ten.add(str(n.args[0].value))
    return ten


def test_code_sinh_ra_khong_co_dinh_danh_tieng_viet(tmp_path: Path):
    assert main(["init", "--root", str(tmp_path)]) == 0
    assert main(["module", "alerts", "--gateway", "--consumer",
                 "--root", str(tmp_path / "src" / "api")]) == 0

    pham: list[str] = []
    for ten in _ten_trong_code(tmp_path):
        # Khớp theo TỪNG ĐOẠN, không phải chuỗi con: "settings" chứa "tin" và
        # "status" chứa "tat" nhưng cả hai đều là tiếng Anh.
        doan = {d for d in ten.replace(".", "_").lower().split("_") if d}
        if doan & _TU_TIENG_VIET:
            pham.append(ten)

    assert not pham, "code sinh ra còn tên tiếng Việt: " + ", ".join(sorted(pham))


def test_gitignore_sinh_ra_che_duoc_thu_can_che(tmp_path: Path):
    """.env lọt vào git là lộ DSN và mật khẩu — đây là dòng quan trọng nhất."""
    assert main(["init", "--root", str(tmp_path)]) == 0
    mau = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()

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
    thieu = [d for d in can_co if d not in mau]
    assert not thieu, f".gitignore sinh ra còn thiếu: {thieu}"

    # Nhưng KHÔNG được che code của người dùng.
    assert "src/" not in mau
    assert "*.py" not in mau


# --------------------------------------------------------- pym install
def test_extras_khop_pyproject():
    """Bảng gói trong CLI phải khớp `[project.optional-dependencies]`.

    `pym install` cài THẲNG các gói phụ thuộc chứ không chạy
    `pip install "pymodular[x]"` — nếu không thì pip đi tìm chính pymodular trên
    PyPI và hỏng với bản cài từ .whl hoặc `pip install -e .`. Cái giá của lựa
    chọn đó là danh sách gói nằm hai nơi; test này giữ chúng bằng nhau.
    """
    import sys

    if sys.version_info < (3, 11):
        pytest.skip("tomllib có từ 3.11; CI đã chạy test này ở các bản mới hơn")
    import tomllib

    from pymodular.cli.install import GOI

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]

    # `all` chỉ gộp các extra khác lại, không tự khai gói nào.
    can_so = {k: v for k, v in extras.items() if k != "all"}
    assert set(GOI) == set(can_so), "thiếu/thừa thành phần so với pyproject"
    for ten, goi in can_so.items():
        assert GOI[ten] == goi, f"danh sách gói của '{ten}' lệch với pyproject"


def test_moi_thanh_phan_deu_biet_ghi_env_hoac_noi_ro_la_khong():
    from pymodular.cli.configure_env import BLOCKS
    from pymodular.cli.install import KHOI_ENV, THANH_PHAN

    for ten, khoi in KHOI_ENV.items():
        assert khoi in BLOCKS, f"'{ten}' trỏ vào khối .env không tồn tại: {khoi}"
    # dev/all là công cụ, không có biến cấu hình — có mặt nhưng không ghi .env.
    assert {"dev", "all"} <= set(THANH_PHAN)
    assert "dev" not in KHOI_ENV and "all" not in KHOI_ENV


def test_install_tu_choi_thanh_phan_la(capsys: pytest.CaptureFixture):
    from pymodular.cli.install import install

    assert install("khong-co-that") == 1
    assert "Không biết thành phần" in capsys.readouterr().out


def test_clean_xoa_cache_nhung_giu_du_lieu(tmp_path: Path):
    from pymodular.cli.clean import clean

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
# gõ được bằng `pym`, không thì tài liệu và trải nghiệm bị chia đôi.
_MAKE_SANG_PYM = {
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
    # đang phát triển pymodular. Người dùng thư viện dùng `pip install pymodular`.
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


def test_moi_target_makefile_deu_co_lenh_pym():
    import re

    makefile = Path("Makefile")
    if not makefile.exists():
        pytest.skip("chạy ngoài repo")

    targets = set(re.findall(r"^([a-z-]+):.*##", makefile.read_text(encoding="utf-8"), re.M))
    thieu = targets - set(_MAKE_SANG_PYM)
    assert not thieu, f"target Makefile chưa có lệnh pym tương ứng: {sorted(thieu)}"

    # Và mọi lệnh trong bảng phải thật sự tồn tại trong CLI.
    lenh = {v.split()[0] for v in _MAKE_SANG_PYM.values() if not v.startswith("-")}
    for ten in sorted(lenh):
        with pytest.raises(SystemExit) as thoat:
            main([ten, "--help"])
        assert thoat.value.code == 0, f"pym {ten} không chạy"


def test_makefile_khong_tu_viet_lai_viec_cua_pym():
    """Mỗi target chỉ gọi lại `pym`, để hai đường không bao giờ lệch nhau."""
    import re

    makefile = Path("Makefile")
    if not makefile.exists():
        pytest.skip("chạy ngoài repo")

    tu_viet: list[str] = []
    tiep_dong = False
    for dong in makefile.read_text(encoding="utf-8").splitlines():
        if not dong.startswith("\t"):
            tiep_dong = False
            continue
        noi_dung, truoc_do = dong.strip(), tiep_dong
        tiep_dong = dong.rstrip().endswith("\\")
        if truoc_do or noi_dung.startswith(("@", "#")):
            continue                       # dòng nối tiếp, hoặc lệnh im lặng
        if "$(PYM)" in dong or "$(PIP)" in dong:
            continue                       # gọi lại pym, hoặc cài chính khung
        if re.match(r"(test|echo) ", noi_dung):
            continue                       # kiểm tham số trước khi gọi
        tu_viet.append(noi_dung)
    assert not tu_viet, "target tự viết lại việc của pym: " + " | ".join(tu_viet)


# ------------------------------------------ README sinh ra phải nói đúng sự thật
def test_readme_sinh_ra_chi_nhac_lenh_co_that(tmp_path: Path):
    """README của dự án là thứ người ta đọc đầu tiên — sai một lệnh là mất niềm tin."""
    import re

    assert main(["init", "--root", str(tmp_path)]) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    from pymodular.cli.main import giai_nghia

    lenh_co_that = sorted(set(_MAKE_SANG_PYM.values()) | {"module", "env", "install"})
    la: list[str] = []
    for m in re.finditer(r"`?pym (\w[\w-]*)", readme):
        tu = m.group(1)
        if tu == "-help":
            continue
        # Giải nghĩa đúng như CLI thật: README được phép dùng viết tắt
        # (`pym mo alerts`), nhưng viết tắt đó phải ra một lệnh CÓ THẬT.
        if giai_nghia(tu, lenh_co_that, "lệnh") not in lenh_co_that:
            la.append(tu)
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
    for ten in ("README.md", ".env"):
        noi_dung = (tmp_path / ten).read_text(encoding="utf-8")
        assert "docs/config.md" not in noi_dung or "http" in noi_dung, (
            f"{ten} trỏ vào docs/ cục bộ, mà dự án sinh ra không có thư mục đó"
        )


def test_vi_du_code_trong_readme_sinh_ra_chay_duoc(tmp_path: Path):
    """Đoạn AppSettings trong README phải khớp file config.py sinh ra kèm."""
    assert main(["init", "--root", str(tmp_path)]) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    config = (tmp_path / "src" / "core" / "config.py").read_text(encoding="utf-8")

    for dong in ('class AppSettings(Settings):',
                 'team_name: str = Field(default="", alias="APP_TEAM_NAME")'):
        assert dong in readme, f"README thiếu: {dong}"
        assert dong in config, f"config.py sinh ra không khớp README: {dong}"


# ------------------------------------------------------------- viết tắt lệnh
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
        ("p", "publish"),
        ("e", "env"),
        ("n", "new"),
        ("module", "module"),          # gõ đầy đủ vẫn phải chạy
    ],
)
def test_viet_tat_lenh(go: str, that_ra_la: str):
    from pymodular.cli.main import _mo_rong_vietat, main

    with pytest.raises(SystemExit) as thoat:
        main([go, "--help"])
    assert thoat.value.code == 0
    assert _mo_rong_vietat([go], ["init", "new", "module", "dev", "run", "info",
                                  "test", "lint", "migrate", "install", "clean",
                                  "build", "publish", "env"]) == [that_ra_la]


@pytest.mark.parametrize(("go", "khop_voi"), [("m", "migrate, module"),
                                              ("i", "info, init, install")])
def test_viet_tat_nhap_nhang_thi_hoi_lai(go: str, khop_voi: str):
    """Đoán bừa ở đây nghĩa là chạy nhầm lệnh — thà báo lỗi."""
    from pymodular.cli.main import main

    with pytest.raises(SystemExit) as thoat:
        main([go])
    assert khop_voi in str(thoat.value)


def test_viet_tat_ca_tham_so_dang_danh_sach(tmp_path: Path):
    from pymodular.cli.main import main

    assert main(["e", "sq", "--file", str(tmp_path / ".env")]) == 0
    assert "APP_DB__DRIVER=sqlite" in (tmp_path / ".env").read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as thoat:
        main(["env", "m", "--file", str(tmp_path / ".env")])
    assert "mongodb, mqtt" in str(thoat.value)


def test_viet_tat_khong_dung_cham_gia_tri_cua_nguoi_dung(tmp_path: Path):
    """Chỉ mở rộng ở vị trí LỆNH và tham số danh sách — tên module thì không."""
    from pymodular.cli.main import main

    assert main(["init", "--root", str(tmp_path)]) == 0
    # "ins" ở đây là TÊN MODULE, không phải viết tắt của "install".
    assert main(["mo", "ins", "--root", str(tmp_path / "src" / "api")]) == 0
    assert (tmp_path / "src" / "api" / "ins" / "in_controller.py").is_file()


# ------------------------------------------- bảng "Rút gọn" phải rút gọn thật
def _lenh_that() -> list[str]:
    """Danh sách lệnh lấy từ chính `pym --help`, không chép tay lại."""
    import contextlib
    import io
    import re

    from pymodular.cli.main import main

    ra = io.StringIO()
    with contextlib.redirect_stdout(ra), pytest.raises(SystemExit):
        main(["--help"])
    khop = re.search(r"\{([a-z,\-]+)\}", ra.getvalue())
    assert khop, "không đọc được danh sách lệnh từ --help"
    return khop.group(1).split(",")


def _cap_rut_gon(readme: str) -> list[tuple[str, str]]:
    """Đọc bảng lệnh: trả về [(lệnh đầy đủ, lệnh rút gọn)] theo từng ô."""
    import re

    cap: list[tuple[str, str]] = []
    for dong in readme.splitlines():
        if not dong.startswith("|"):
            continue
        o = [c.strip() for c in dong.strip("|").split("|")]
        if len(o) != 3 or not o[1]:
            continue
        day_du = re.findall(r"`pym ([^`]+)`", o[0])
        ngan = re.findall(r"`pym ([^`]+)`", o[1])
        if len(day_du) != len(ngan):           # hai ô phải khớp nhau từng cặp
            cap.append((o[0], o[1]))           # để assert bên dưới báo đúng chỗ
            continue
        cap.extend(zip(day_du, ngan, strict=True))
    return cap


@pytest.mark.parametrize("nguon", ["repo", "sinh-ra"])
def test_moi_lenh_rut_gon_trong_readme_deu_giai_ra_dung_lenh_do(nguon: str, tmp_path: Path):
    """Cột "Rút gọn" là lời hứa với người đọc — thêm lệnh mới có thể phá nó.

    Ví dụ: thêm lệnh `down` thì `pym d` hết trỏ về `dev`, README lặng lẽ sai.
    Test này bắt ngay, vì nó giải nghĩa bằng chính hàm CLI dùng lúc chạy thật.
    """
    from pymodular.cli.install import THANH_PHAN
    from pymodular.cli.main import giai_nghia

    if nguon == "repo":
        readme = Path("README.md").read_text(encoding="utf-8")
    else:
        assert main(["init", "--root", str(tmp_path)]) == 0
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    lenh = _lenh_that()
    danh_sach = {"install": THANH_PHAN, "env": THANH_PHAN}

    cap = _cap_rut_gon(readme)
    assert cap, "không đọc được bảng lệnh nào — bảng đổi định dạng?"

    for day_du, ngan in cap:
        tu_day, tu_ngan = day_du.split(), ngan.split()
        assert len(tu_ngan) <= len(tu_day), f"{ngan!r} nhiều từ hơn {day_du!r}"

        assert giai_nghia(tu_ngan[0], lenh, "lệnh") == tu_day[0], (
            f"`pym {ngan}` không ra `pym {day_du}`"
        )
        # Ô đầy đủ có thể ghi chỗ trống (`<tên>`) hay cờ (`--workers 4`); chỉ đối
        # chiếu khi đó là một giá trị có thật trong danh sách chọn.
        if len(tu_ngan) > 1 and tu_day[0] in danh_sach and tu_day[1] in danh_sach[tu_day[0]]:
            assert giai_nghia(tu_ngan[1], danh_sach[tu_day[0]], "thành phần") == tu_day[1], (
                f"`pym {ngan}` không ra `pym {day_du}`"
            )
