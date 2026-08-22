"""Test hai chiều phụ thuộc giữa module Device và module User."""

from __future__ import annotations

import pytest


@pytest.fixture
def device(client, user) -> dict:
    response = client.post(
        "/api/devices",
        json={"name": "Cảm biến 1", "serial": "SN-1", "owner_id": user["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_tao_thiet_bi_kiem_tra_chu_so_huu(client):
    """device -> user: phụ thuộc service trực tiếp."""
    response = client.post(
        "/api/devices",
        json={"name": "X", "serial": "SN-9", "owner_id": "khong-co"},
    )
    assert response.status_code == 404
    assert "khong-co" in response.json()["message"]


def test_serial_trung(client, device, user):
    response = client.post(
        "/api/devices",
        json={"name": "Y", "serial": "SN-1", "owner_id": user["id"]},
    )
    assert response.status_code == 409


def test_route_cua_module_device_nam_duoi_prefix_users(client, device, user):
    """/api/users/{id}/devices do module Device sở hữu, không phải module User."""
    body = client.get(f"/api/users/{user['id']}/devices").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == device["id"]


def test_xoa_user_bi_chan_khi_con_thiet_bi(client, device, user):
    """user -> device: đi qua Lazy proxy."""
    response = client.delete(f"/api/users/{user['id']}")
    assert response.status_code == 409
    assert response.json()["details"] == {"devices": 1}


def test_cascade_xoa_luon_thiet_bi(client, device, user):
    assert client.delete(f"/api/users/{user['id']}", params={"cascade": True}).status_code == 204
    assert client.get("/api/devices").json()["total"] == 0
    assert client.get(f"/api/users/{user['id']}").status_code == 404


def test_status_sai_enum(client, device):
    response = client.patch(f"/api/devices/{device['id']}", json={"status": "bay-mau"})
    assert response.status_code == 422


def test_khong_doi_duoc_serial_va_owner(client, device):
    """serial/owner_id chỉ đặt lúc tạo — DeviceUpdate không nhận."""
    assert client.patch(f"/api/devices/{device['id']}", json={"serial": "SN-X"}).status_code == 422
    assert client.patch(f"/api/devices/{device['id']}", json={"owner_id": "ai-do"}).status_code == 422
