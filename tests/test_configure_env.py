"""Test bộ sinh khối .env của `make install-*`.

Ràng buộc quan trọng nhất: mọi biến sinh ra phải CÒN TỒN TẠI trong model
Settings tương ứng. Nhờ vậy xoá một trường khỏi code mà quên xoá ở đây thì test
đỏ ngay, thay vì để người dùng điền một biến không còn ai đọc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastapi_modular.cli.configure_env import (
    BLOCKS,
    Bien,
    _mac_dinh,
    begin_marker,
    end_marker,
    main,
    render,
)


def _bien(khoi) -> list[Bien]:
    return [item for item in khoi.items if isinstance(item, Bien)]


@pytest.mark.parametrize("ten", sorted(BLOCKS))
def test_moi_bien_deu_con_trong_model(ten: str):
    khoi = BLOCKS[ten]
    for bien in _bien(khoi):
        _mac_dinh(khoi, bien.key)          # ném KeyError nếu trường đã bị xoá


@pytest.mark.parametrize("ten", sorted(BLOCKS))
def test_moi_bien_deu_co_giai_thich_va_mac_dinh(ten: str):
    khoi = BLOCKS[ten]
    noi_dung = render(khoi)

    for bien in _bien(khoi):
        assert len(bien.mo_ta) >= 20, f"{bien.key} giải thích quá sơ sài"
        assert bien.mo_ta.endswith("."), f"{bien.key} thiếu dấu chấm cuối câu"
        assert f"{bien.key}={bien.value}" in noi_dung

    # Phía trên mỗi dòng gán biến phải có một dòng nói rõ tuỳ chọn hay bắt buộc
    # (lời giải thích có thể dài, xuống dòng nhiều lần).
    dong = noi_dung.splitlines()
    for i, line in enumerate(dong):
        if not line.startswith("APP_"):
            continue
        khoi_chu_thich = []
        for truoc in reversed(dong[:i]):
            if not truoc.startswith("#"):
                break
            khoi_chu_thich.append(truoc)
        assert any(
            d.startswith(("# tuỳ chọn · mặc định:", "# BẮT BUỘC")) for d in khoi_chu_thich
        ), f"{line} thiếu dòng ghi mặc định/bắt buộc"


@pytest.mark.parametrize("ten", sorted(BLOCKS))
def test_chi_danh_dau_bat_buoc_thu_that_su_quan_trong(ten: str):
    """Bắt buộc = xoá đi thì app chạy sai im lặng. Chỉ có driver và địa chỉ."""
    for bien in _bien(BLOCKS[ten]):
        if bien.bat_buoc:
            assert bien.key.endswith(("__DSN", "__URL", "__DRIVER", "__BOOTSTRAP_SERVERS")), bien.key


def test_ghi_va_thay_khoi(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("APP_PORT=8000\n", encoding="utf-8")

    assert main("sqlite", env) == 0
    noi_dung = env.read_text(encoding="utf-8")
    assert "APP_PORT=8000" in noi_dung, "biến ngoài khối phải được giữ nguyên"
    assert "APP_DB__DRIVER=sqlite" in noi_dung

    assert main("postgres", env) == 0
    noi_dung = env.read_text(encoding="utf-8")
    assert "APP_DB__DRIVER=postgres" in noi_dung
    assert "sqlite" not in noi_dung, "khối cũ phải bị THAY, không chồng thêm"
    assert noi_dung.count(begin_marker("database")) == 1


def test_cac_khoi_doc_lap_nhau(tmp_path: Path):
    env = tmp_path / ".env"
    assert main("rabbitmq", env) == 0
    assert main("sqlite", env) == 0          # đổi database không đụng rabbitmq

    noi_dung = env.read_text(encoding="utf-8")
    assert "APP_RABBITMQ__URL" in noi_dung
    assert "APP_DB__DRIVER=sqlite" in noi_dung
    for section in ("database", "rabbitmq"):
        assert noi_dung.count(begin_marker(section)) == 1
        assert noi_dung.count(end_marker(section)) == 1


def test_ten_thanh_phan_sai(tmp_path: Path, capsys):
    assert main("khong-co-that", tmp_path / ".env") == 1
    assert "không hợp lệ" in capsys.readouterr().out


# --------------------------------------------------------------- tài liệu
def test_bien_nhac_trong_docs_deu_con_that(  ):
    """Tài liệu không được nhắc tới biến đã bị xoá khỏi code.

    Quét mọi `APP_...` xuất hiện trong docs/ và README rồi đối chiếu với model
    Settings. Đây là cách bắt tài liệu cũ mà không phải đọc lại từng dòng.

    Đối chiếu với `AppSettings` chứ không phải `Settings`: tài liệu có nhắc cả
    biến của ứng dụng mẫu (`APP_JWT__*`) làm ví dụ cho phần "thêm biến của riêng
    bạn", và chúng cũng phải là biến CÓ THẬT.
    """
    import re

    from fastapi_modular.core.config import (
        CorsSettings,
        DatabaseSettings,
        KafkaSettings,
        LogSettings,
        MqttSettings,
        RabbitSettings,
        RedisSettings,
        WebSocketSettings,
    )
    from src.core.config import AppSettings, JwtSettings

    nhom = {
        "APP_DB__": DatabaseSettings,
        "APP_WS__": WebSocketSettings,
        "APP_RABBITMQ__": RabbitSettings,
        "APP_REDIS__": RedisSettings,
        "APP_MQTT__": MqttSettings,
        "APP_KAFKA__": KafkaSettings,
        "APP_LOG__": LogSettings,
        "APP_CORS__": CorsSettings,
        "APP_JWT__": JwtSettings,
    }
    cap_app = {f.alias for f in AppSettings.model_fields.values() if f.alias}

    files = [*Path("docs").glob("*.md"), Path("README.md")]
    thieu: list[str] = []
    for path in files:
        for key in set(re.findall(r"APP_[A-Z0-9_]+", path.read_text(encoding="utf-8"))):
            if key.endswith("__"):
                continue                       # chỉ là tiền tố nhắc trong câu văn
            if key in cap_app or key.startswith("APP_MQ__"):
                continue                       # APP_MQ__ chỉ nhắc để hướng dẫn đổi tên
            for prefix, model in nhom.items():
                if key.startswith(prefix):
                    if key.removeprefix(prefix).lower() not in model.model_fields:
                        thieu.append(f"{path}: {key}")
                    break
            else:
                thieu.append(f"{path}: {key} (không thuộc nhóm nào)")

    assert not thieu, "tài liệu nhắc tới biến không còn tồn tại:\n" + "\n".join(sorted(thieu))


def test_thay_duoc_khoi_sinh_boi_moc_cu(tmp_path: Path):
    """`.env` viết thời `make install-*` phải được THAY, không chồng thêm khối mới.

    Không nhận ra mốc cũ thì người dùng có hai `APP_DB__DRIVER` trong .env, và
    cái nằm dưới lặng lẽ thắng — kiểu lỗi mất cả buổi mới lần ra.
    """
    env = tmp_path / ".env"
    env.write_text(
        "APP_PORT=8002\n"
        "# >>> database (sinh bởi make install-*) >>>\n"
        "APP_DB__DRIVER=sqlite\n"
        "APP_DB__DSN=sqlite+aiosqlite:///./cu.db\n"
        "# <<< database <<<\n"
        "CUA_TOI=1\n",
        encoding="utf-8",
    )

    assert main("postgres", env) == 0
    noi_dung = env.read_text(encoding="utf-8")

    assert noi_dung.count("APP_DB__DRIVER=") == 1, "khối cũ phải bị thay, không nhân đôi"
    assert "APP_DB__DRIVER=postgres" in noi_dung
    assert "cu.db" not in noi_dung
    assert "(sinh bởi make install-*)" not in noi_dung, "mốc phải được nâng lên mốc mới"
    # Dòng ngoài khối vẫn nguyên vẹn.
    assert "APP_PORT=8002" in noi_dung
    assert "CUA_TOI=1" in noi_dung
