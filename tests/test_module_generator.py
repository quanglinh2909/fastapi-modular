"""Test lệnh `make module` — khung sinh ra phải chạy được ngay."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pymodular.cli.new_module import (
    main,
    pascal,
    render,
    render_consumer,
    render_gateway,
    singular,
)


@pytest.mark.parametrize(
    ("plural", "expected"),
    [
        ("users", "user"),
        ("devices", "device"),
        ("categories", "category"),
        ("boxes", "box"),
        ("policies", "policy"),
        ("device_groups", "device_group"),
        ("alerts", "alert"),
        ("status", "status"),          # đuôi "us" không phải dấu hiệu số nhiều
        ("classes", "class"),
        ("analysis", "analysis"),
        ("data", "data"),              # không đoán được thì giữ nguyên
    ],
)
def test_doan_so_it(plural, expected):
    assert singular(plural) == expected


def test_pascal():
    assert pascal("device_group") == "DeviceGroup"
    assert pascal("alert") == "Alert"


def test_sinh_du_file(tmp_path: Path):
    assert main(["alerts", "--root", str(tmp_path)]) == 0
    module = tmp_path / "alerts"
    for relative in (
        "__init__.py",
        "alert_controller.py",
        "alert_service.py",
        "dto/__init__.py",
        "dto/alert_dto.py",
        "entities/__init__.py",
        "entities/alert_model.py",
    ):
        assert (module / relative).is_file(), relative


def test_khong_ghi_de_module_da_co(tmp_path: Path, capsys):
    assert main(["alerts", "--root", str(tmp_path)]) == 0
    assert main(["alerts", "--root", str(tmp_path)]) == 1
    assert "Đã có" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["Sai-Ten!", "1alerts", "có dấu", ""])
def test_tu_choi_ten_khong_hop_le(tmp_path: Path, name):
    assert main([name, "--root", str(tmp_path)]) == 1


def test_dat_ten_entity_bang_tay(tmp_path: Path):
    assert main(["people", "--entity", "person", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "people" / "person_controller.py").is_file()
    assert "class PersonController" in (
        tmp_path / "people" / "person_controller.py"
    ).read_text(encoding="utf-8")


def test_code_sinh_ra_hop_le_cu_phap():
    """Không compile được thì cả app sẽ không khởi động nổi."""
    import ast

    for name, content in render("alerts", "alert").items():
        if name.endswith(".py"):
            ast.parse(content, filename=name)


def test_code_sinh_ra_sach_lint(tmp_path: Path):
    if not (Path(sys.prefix) / "bin" / "ruff").exists():
        pytest.skip("chưa cài ruff")

    assert main(["alerts", "--root", str(tmp_path), "--gateway", "--consumer"]) == 0
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(tmp_path), "--config", "ruff.toml"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_ham_chua_viet_thi_bao_ro(tmp_path: Path):
    """Thân hàm để trống phải ném NotImplementedError, không phải im lặng."""
    service = render("alerts", "alert")["alert_service.py"]
    for method in ("list_alerts", "get_alert", "create_alert", "update_alert", "delete_alert"):
        assert f"async def {method}" in service
    assert service.count("raise NotImplementedError") == 5


def test_khung_co_du_5_route():
    router = render("alerts", "alert")["alert_controller.py"]
    for decorator in ('@get("",', '@get("/{alert_id}"', "@post(", "@patch(", "@delete("):
        assert decorator in router


def test_entity_co_san_ba_truong_bat_buoc():
    model = render("alerts", "alert")["entities/alert_model.py"]
    assert "id: str" in model
    assert "created_at: datetime" in model
    assert "updated_at: datetime" in model
    assert "@entity(" in model
    assert "TODO" in model, "phải có chỗ đánh dấu để người dùng thêm trường"


# ------------------------------------------------------------ gateway WebSocket
def test_sinh_kem_gateway(tmp_path: Path):
    assert main(["alerts", "--root", str(tmp_path), "--gateway"]) == 0
    assert (tmp_path / "alerts" / "alert_gateway.py").exists()
    assert (tmp_path / "alerts" / "dto" / "alert_ws_dto.py").exists()


def test_khong_sinh_gateway_neu_khong_yeu_cau(tmp_path: Path):
    assert main(["alerts", "--root", str(tmp_path)]) == 0
    assert not (tmp_path / "alerts" / "alert_gateway.py").exists()


def test_them_gateway_vao_module_da_co(tmp_path: Path):
    assert main(["alerts", "--root", str(tmp_path)]) == 0
    assert main(["alerts", "--root", str(tmp_path), "--gateway-only"]) == 0
    assert (tmp_path / "alerts" / "alert_gateway.py").exists()

    # Chạy lần hai không được ghi đè file người dùng đã viết.
    assert main(["alerts", "--root", str(tmp_path), "--gateway-only"]) == 1


def test_gateway_only_doi_module_phai_ton_tai(tmp_path: Path, capsys):
    assert main(["chua_co", "--root", str(tmp_path), "--gateway-only"]) == 1
    assert "pym module" in capsys.readouterr().out


def test_gateway_co_du_moc_can_thiet():
    gw = render_gateway("alerts", "alert")["alert_gateway.py"]
    assert '@gateway(' in gw
    assert 'path="/ws/alerts"' in gw
    assert "async def on_connect" in gw
    assert "async def on_disconnect" in gw
    assert "can_join" in gw, "phải nhắc tới chốt chặn quyền vào phòng"
    assert gw.count("raise NotImplementedError") == 2
    assert "TODO" in gw


def test_gateway_sinh_ra_hop_le_cu_phap():
    import ast

    for name, content in render_gateway("alerts", "alert").items():
        ast.parse(content, filename=name)


# ---------------------------------------------------------- consumer RabbitMQ
def test_sinh_kem_consumer(tmp_path: Path):
    assert main(["alerts", "--root", str(tmp_path), "--consumer"]) == 0
    assert (tmp_path / "alerts" / "alert_consumer.py").exists()


def test_them_consumer_vao_module_da_co(tmp_path: Path):
    assert main(["alerts", "--root", str(tmp_path)]) == 0
    assert not (tmp_path / "alerts" / "alert_consumer.py").exists()
    assert main(["alerts", "--root", str(tmp_path), "--consumer-only"]) == 0
    assert (tmp_path / "alerts" / "alert_consumer.py").exists()
    assert main(["alerts", "--root", str(tmp_path), "--consumer-only"]) == 1   # không ghi đè


def test_consumer_co_du_moc_can_thiet():
    code = render_consumer("alerts", "alert")["alert_consumer.py"]
    assert '@rabbitmq_subscriber("events", "alert.#", queue="alerts-worker")' in code
    assert "raise NotImplementedError" in code
    assert "dlq" in code, "phải nhắc tới hàng đợi chết"
    assert "TODO" in code


def test_consumer_sinh_ra_hop_le_cu_phap():
    import ast

    for name, content in render_consumer("alerts", "alert").items():
        ast.parse(content, filename=name)
