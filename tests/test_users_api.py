"""Test HTTP cho module User."""

from __future__ import annotations


def test_tao_va_doc_user(client, user):
    assert user["email"] == "an@example.com"
    assert user["is_active"] is True

    listed = client.get("/api/users", params={"limit": 5}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == user["id"]


def test_email_trung_khong_phan_biet_hoa_thuong(client, user):
    response = client.post(
        "/api/users", json={"email": "AN@Example.com", "full_name": "Trùng"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_email_sai_dinh_dang(client):
    response = client.post("/api/users", json={"email": "sai", "full_name": "X"})
    assert response.status_code == 422
    assert response.json()["details"][0]["field"] == "email"


def test_field_la_bi_tu_choi(client):
    response = client.post(
        "/api/users",
        json={"email": "b@c.co", "full_name": "B", "is_admin": True},
    )
    assert response.status_code == 422
    assert response.json()["details"][0]["type"] == "extra_forbidden"


def test_limit_vuot_nguong(client):
    response = client.get("/api/users", params={"limit": 999})
    assert response.status_code == 422


def test_khong_tim_thay(client):
    response = client.get("/api/users/khong-co")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert response.json()["request_id"]


def test_cap_nhat_mot_phan(client, user):
    response = client.patch(f"/api/users/{user['id']}", json={"full_name": "An 2"})
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "An 2"
    assert body["email"] == user["email"]          # field không gửi thì giữ nguyên
    assert body["updated_at"] != user["updated_at"]


def test_xoa_user_khong_co_thiet_bi(client, user):
    assert client.delete(f"/api/users/{user['id']}").status_code == 204
    assert client.get(f"/api/users/{user['id']}").status_code == 404


def test_updated_at_tu_dong_doi_khi_ghi(client, user):
    """Service không còn tự gán updated_at — repository đóng dấu."""
    patched = client.patch(f"/api/users/{user['id']}", json={"full_name": "An 3"}).json()
    assert patched["updated_at"] > user["updated_at"]
    assert patched["created_at"] == user["created_at"], "created_at không được đổi"


def test_patch_rong_khong_xoa_gi(client, user):
    """exclude_unset: không gửi field nào thì không field nào bị ghi đè."""
    patched = client.patch(f"/api/users/{user['id']}", json={}).json()
    assert patched["email"] == user["email"]
    assert patched["full_name"] == user["full_name"]
    assert patched["is_active"] == user["is_active"]


def test_patch_giu_nguyen_rang_buoc_cua_create(client, user):
    """UserUpdate sinh từ UserBase nên pattern/min_length không lệch bản create."""
    assert client.patch(f"/api/users/{user['id']}", json={"email": "sai"}).status_code == 422
    assert client.patch(f"/api/users/{user['id']}", json={"full_name": ""}).status_code == 422
    assert client.patch(f"/api/users/{user['id']}", json={"la_gi": 1}).status_code == 422


def test_cat_khoang_trang_thua(client):
    """InputSchema đặt str_strip_whitespace nên "An " và "An" không thành hai người."""
    body = client.post(
        "/api/users", json={"email": "  b@c.co  ", "full_name": "  Bích  "}
    ).json()
    assert body["email"] == "b@c.co"
    assert body["full_name"] == "Bích"


def test_khong_dat_duoc_is_active_luc_tao(client):
    """is_active chỉ có ở UserUpdate, không có ở UserCreate."""
    response = client.post(
        "/api/users",
        json={"email": "c@d.co", "full_name": "C", "is_active": False},
    )
    assert response.status_code == 422
