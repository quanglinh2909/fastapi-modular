"""`fam install` phải GHI NHỚ thành phần, không chỉ cài vào venv của máy này.

Vì sao có file này: `fam install sqlite` cài sqlalchemy + aiosqlite + alembic
vào venv đang chạy. Đồng nghiệp clone repo về thì không có gì nói cho họ biết
dự án cần những gói đó — `pip install -r requirements.txt` sẽ thiếu, app chết
lúc khởi động với `ComponentNotEnabledError`, và không ai lần ra vì sao. Đây
đúng là việc `package.json` làm cho `npm i`.

Cách ghi là MỘT dòng dùng extras (`fastapi-modular[redis,sqlite]>=...`), không
phải danh sách gói phẳng — khoảng phiên bản của sqlalchemy/motor là chuyện của
fastapi-modular và nó đổi theo bản.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastapi_modular import __version__
from fastapi_modular.cli.install import _ALIAS, PACKAGE
from fastapi_modular.cli.requirements import extras_of, record


def ghi(root: Path, *names: str) -> str:
    note = ""
    for name in names:
        note = record(name, root, extras_of(name, _ALIAS, PACKAGE), __version__)
    return note


def dong_thuc(path: Path) -> list[str]:
    """Các dòng KHÔNG phải chú thích."""
    return [d for d in path.read_text(encoding="utf-8").splitlines() if d and not d.startswith("#")]


# ------------------------------------------------------------------ ghi mới
def test_chua_co_gi_thi_tao_requirements(tmp_path: Path):
    ghi(tmp_path, "sqlite")
    assert dong_thuc(tmp_path / "requirements.txt") == [f"fastapi-modular[sqlite]>={__version__}"]
    assert "pip install -r requirements.txt" in (tmp_path / "requirements.txt").read_text(
        encoding="utf-8"
    ), "phải nói luôn cho người clone về biết gõ gì"


def test_cai_nhieu_thanh_phan_thi_cong_don_mot_dong(tmp_path: Path):
    """Ba lần cài = một dòng ba extras, không phải ba dòng fastapi-modular."""
    ghi(tmp_path, "sqlite", "redis", "rabbitmq")
    assert dong_thuc(tmp_path / "requirements.txt") == [
        f"fastapi-modular[rabbitmq,redis,sqlite]>={__version__}"
    ]


def test_ghi_extras_chu_khong_phai_tung_goi_con(tmp_path: Path):
    """Không được chép sqlalchemy/aiosqlite ra đây — bản chụp đó sẽ lạc hậu."""
    ghi(tmp_path, "sqlite")
    text = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    for goi in ("sqlalchemy", "aiosqlite", "alembic"):
        assert goi not in text.split("#")[-1], f"{goi} không nên nằm trong requirements.txt"


@pytest.mark.parametrize(
    ("cai", "mong_doi"),
    [
        ("ws-redis", "redis"),          # dùng chung thư viện với redis
        ("mongo", "mongodb"),           # tên gõ tắt
        ("postgresql", "postgres"),
    ],
)
def test_ten_goi_tat_quy_ve_dung_extra(tmp_path: Path, cai: str, mong_doi: str):
    ghi(tmp_path, cai)
    assert dong_thuc(tmp_path / "requirements.txt") == [
        f"fastapi-modular[{mong_doi}]>={__version__}"
    ]


def test_all_ghi_moi_thanh_phan_tru_dev(tmp_path: Path):
    ghi(tmp_path, "all")
    (dong,) = dong_thuc(tmp_path / "requirements.txt")
    assert "dev" not in dong, "pytest/ruff không phải thứ production cần"
    assert "sqlite" in dong and "kafka" in dong


def test_dev_di_file_rieng(tmp_path: Path):
    """Trộn pytest vào requirements.txt là bắt server production cài pytest."""
    ghi(tmp_path, "sqlite", "dev")
    assert dong_thuc(tmp_path / "requirements.txt") == [f"fastapi-modular[sqlite]>={__version__}"]
    assert dong_thuc(tmp_path / "requirements-dev.txt") == [
        f"fastapi-modular[dev]>={__version__}"
    ]


# --------------------------------------------------- không phá file đang có
def test_giu_nguyen_cac_dong_khac_cua_nguoi_dung(tmp_path: Path):
    """File của người dùng: chỉ đụng đúng dòng fastapi-modular, không dòng nào khác.

    Riêng sàn `>=` thì được nâng lên bản đang dùng — xem
    `test_nang_san_len_ban_dang_dung`.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("# của tôi\nrequests==2.31.0\nfastapi-modular>=0.1.0\npandas\n", encoding="utf-8")

    ghi(tmp_path, "sqlite")

    assert req.read_text(encoding="utf-8").splitlines() == [
        "# của tôi",
        "requests==2.31.0",
        f"fastapi-modular[sqlite]>={__version__}",
        "pandas",
    ]


def test_ten_viet_gach_duoi_va_ghim_phien_ban_van_nhan_ra(tmp_path: Path):
    """pip nhận cả `fastapi_modular`, nên chỗ này cũng phải nhận — nếu không sẽ
    ghi thêm dòng thứ hai và người ta có hai ràng buộc đá nhau."""
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi_modular==0.2.1\n", encoding="utf-8")

    ghi(tmp_path, "mqtt")

    assert dong_thuc(req) == ["fastapi-modular[mqtt]==0.2.1"]


def test_cai_lai_thanh_phan_da_co_thi_khong_doi_gi(tmp_path: Path):
    ghi(tmp_path, "sqlite")
    truoc = (tmp_path / "requirements.txt").read_text(encoding="utf-8")

    note = ghi(tmp_path, "sqlite")

    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == truoc
    assert "đã ghi sẵn" in note


def test_dong_bi_chu_thich_khong_bi_tinh_la_khai_bao(tmp_path: Path):
    """`# fastapi-modular[redis]` là ghi chú, không phải phụ thuộc."""
    req = tmp_path / "requirements.txt"
    req.write_text("# fastapi-modular[redis]>=0.1.0\nhttpx\n", encoding="utf-8")

    ghi(tmp_path, "sqlite")

    assert dong_thuc(req) == ["httpx", f"fastapi-modular[sqlite]>={__version__}"]


# ------------------------------------------------------- nâng sàn phiên bản
def test_nang_san_len_ban_dang_dung(tmp_path: Path):
    """Sàn phải nói đúng bản THẬT SỰ đang chạy.

    Cài thêm redis bằng fastapi-modular 0.3.0 nhưng requirements vẫn ghi
    `>=0.2.1` thì đồng nghiệp được phép cài 0.2.1 — bản có thể chưa có extra đó,
    hoặc chưa có API bạn vừa dùng.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi-modular[sqlite]>=0.2.1\n", encoding="utf-8")

    record("redis", tmp_path, {"redis"}, "0.3.0")

    assert dong_thuc(req) == ["fastapi-modular[redis,sqlite]>=0.3.0"]


def test_khong_ha_san_dang_cao_hon(tmp_path: Path):
    """Chỉ NÂNG. Người dùng đòi bản mới hơn bản đang cài là chuyện của họ."""
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi-modular>=0.9.0\n", encoding="utf-8")

    record("redis", tmp_path, {"redis"}, "0.3.0")

    assert dong_thuc(req) == ["fastapi-modular[redis]>=0.9.0"]


@pytest.mark.parametrize(
    "rang_buoc",
    ["==0.2.1", "~=0.2", ""],
)
def test_chi_dung_toi_dau_bang_lon_hon(tmp_path: Path, rang_buoc: str):
    """`==` và `~=` là quyết định của người dùng — tự nâng là ghi đè ý định họ."""
    req = tmp_path / "requirements.txt"
    req.write_text(f"fastapi-modular{rang_buoc}\n", encoding="utf-8")

    record("redis", tmp_path, {"redis"}, "0.3.0")

    assert dong_thuc(req) == [f"fastapi-modular[redis]{rang_buoc}"]


def test_co_tran_thi_chi_nang_san(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi-modular>=0.2.1,<1.0\n", encoding="utf-8")

    record("redis", tmp_path, {"redis"}, "0.3.0")

    assert dong_thuc(req) == ["fastapi-modular[redis]>=0.3.0,<1.0"]


def test_so_sanh_bang_SO_chu_khong_phai_chuoi(tmp_path: Path):
    """So chuỗi thì `"0.10.0" < "0.9.0"` — và sàn bị HẠ xuống trong im lặng."""
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi-modular>=0.10.0\n", encoding="utf-8")

    record("redis", tmp_path, {"redis"}, "0.9.0")

    assert dong_thuc(req) == ["fastapi-modular[redis]>=0.10.0"]


# ------------------------------------------------------------- pyproject.toml
def test_pyproject_co_nhac_thi_sua_ngay_trong_do(tmp_path: Path):
    """Dự án dùng pyproject thì đừng đẻ thêm requirements.txt bên cạnh."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n    "fastapi-modular>=0.2.1",\n    "httpx",\n]\n',
        encoding="utf-8",
    )

    ghi(tmp_path, "postgres")

    assert (
        f'    "fastapi-modular[postgres]>={__version__}",'
        in pyproject.read_text(encoding="utf-8")
    ), "giữ nguyên thụt lề, nháy và dấu phẩy; sàn nâng theo bản đang dùng"
    assert not (tmp_path / "requirements.txt").exists()


def test_pyproject_khong_nhac_thi_tao_requirements(tmp_path: Path):
    """Không đoán mò chỗ chèn vào file TOML của người ta."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["httpx"]\n', encoding="utf-8"
    )

    ghi(tmp_path, "kafka")

    assert dong_thuc(tmp_path / "requirements.txt") == [f"fastapi-modular[kafka]>={__version__}"]


# ------------------------------------------------------------ nối vào fam init
def test_fam_init_sinh_san_requirements(tmp_path: Path):
    from fastapi_modular.cli.main import main

    assert main(["init", "--root", str(tmp_path)]) == 0
    assert dong_thuc(tmp_path / "requirements.txt") == [f"fastapi-modular>={__version__}"]


def test_fam_install_ghi_ca_env_lan_requirements(tmp_path: Path, monkeypatch):
    """Chạy `fam install` thật, chỉ thay mỗi lời gọi pip."""
    import fastapi_modular.cli.install as install_module

    monkeypatch.setattr(install_module.subprocess, "call", lambda *a, **k: 0)

    assert install_module.install("sqlite", env_file=tmp_path / ".env") == 0

    assert "APP_DB__DRIVER=sqlite" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert dong_thuc(tmp_path / "requirements.txt") == [f"fastapi-modular[sqlite]>={__version__}"]


def test_pip_hong_thi_khong_ghi_gi(tmp_path: Path, monkeypatch):
    """Cài thất bại mà vẫn ghi vào requirements là nói dối người clone về sau."""
    import fastapi_modular.cli.install as install_module

    monkeypatch.setattr(install_module.subprocess, "call", lambda *a, **k: 1)

    assert install_module.install("sqlite", env_file=tmp_path / ".env") == 1
    assert not (tmp_path / "requirements.txt").exists()
