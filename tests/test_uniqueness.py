"""Ràng buộc duy nhất phải nằm DƯỚI database, không chỉ trong service.

Kiểm tra rồi mới ghi là một cuộc đua: hai request đồng thời đều thấy "chưa có"
rồi cùng ghi. Trước khi có unique index, 20 request đồng thời cùng email tạo ra
15 bản ghi trùng.
"""

from __future__ import annotations

import pytest

from fastapi_modular.infrastructure.database.base import DuplicateKeyViolation, mapping_for


def test_entity_khai_bao_unique_va_index():
    from src.api.devices.entities.device_model import Device
    from src.api.users.entities.user_model import User

    # cột đơn cũng được chuẩn hoá thành cụm một cột
    assert mapping_for(User).unique == (("email",),)
    assert mapping_for(Device).unique == (("serial",), ("owner_id", "name"))
    assert mapping_for(Device).indexes == (("owner_id", "status"), ("status",))


def test_ten_index_theo_cum_cot():
    from src.api.devices.entities.device_model import Device

    specs = {name: (cols, uq) for name, cols, uq in mapping_for(Device).index_specs()}
    assert specs["uq_devices_serial"] == (("serial",), True)
    assert specs["uq_devices_owner_id_name"] == (("owner_id", "name"), True)
    assert specs["ix_devices_owner_id_status"] == (("owner_id", "status"), False)
    # Cùng một bảng có cả cụm index lẫn index đơn: cụm (owner_id, status) phục
    # vụ truy vấn lọc theo owner_id hoặc owner_id+status, nhưng KHÔNG phục vụ
    # được truy vấn chỉ lọc theo status — nên cần thêm index đơn.
    assert specs["ix_devices_status"] == (("status",), False)


def test_ten_index_qua_dai_thi_rut_gon():
    """PostgreSQL cắt định danh ở 63 ký tự — tên phải tự rút gọn mà vẫn ổn định."""
    from fastapi_modular.infrastructure.database.base import MAX_INDEX_NAME, index_name

    columns = ("cot_thu_nhat", "cot_thu_hai", "cot_thu_ba", "cot_thu_tu", "cot_thu_nam")
    name = index_name("ix", "mot_cai_bang_ten_rat_dai_de_thu", columns)
    assert len(name) <= MAX_INDEX_NAME
    assert name == index_name("ix", "mot_cai_bang_ten_rat_dai_de_thu", columns)


def test_cum_unique_chan_trung_theo_CA_CUM(client, user):
    """Cùng chủ + cùng tên -> chặn. Khác chủ + cùng tên -> cho phép."""
    different = client.post(
        "/api/users", json={"email": "chu2@example.com", "full_name": "Chủ 2"}
    ).json()

    def tao(owner_id: str, serial: str, name: str = "Cảm biến A"):
        return client.post(
            "/api/devices",
            json={"name": name, "serial": serial, "owner_id": owner_id},
        )

    assert tao(user["id"], "SN-A").status_code == 201
    assert tao(user["id"], "SN-B").status_code == 409, "cùng chủ, trùng tên"
    assert tao(different["id"], "SN-C").status_code == 201, "khác chủ thì trùng tên vẫn được"
    assert tao(user["id"], "SN-D", name="Cảm biến B").status_code == 201


def test_email_ha_chu_thuong_ngay_o_dto():
    """Unique index phân biệt hoa thường, nên phải chuẩn hoá ở cửa vào."""
    from src.api.users.dto.user_dto import UserCreate, UserUpdate

    assert UserCreate(email="AN@Example.COM", full_name="A").email == "an@example.com"
    assert UserUpdate(email="AN@Example.COM").email == "an@example.com"


def test_backend_chan_trung_ke_ca_khi_service_bo_qua(client, user):
    """Đi thẳng xuống repository, bỏ qua lớp kiểm tra của service."""
    import anyio

    from fastapi_modular.core.container import container
    from fastapi_modular.infrastructure.database.repository import Database, Repository
    from src.api.users.entities.user_model import User

    repo = Repository(User, container.resolve(Database))

    async def _write_duplicate() -> None:
        await repo.save(User(id="", email=user["email"], full_name="Kẻ trùng"))

    with pytest.raises(DuplicateKeyViolation):
        anyio.run(_write_duplicate)


def test_trung_email_tra_409_khong_phai_500(client, user):
    response = client.post(
        "/api/users", json={"email": user["email"], "full_name": "Trùng"}
    )
    assert response.status_code == 409


def test_trung_email_khac_hoa_thuong_cung_bi_chan(client, user):
    response = client.post(
        "/api/users", json={"email": user["email"].upper(), "full_name": "Trùng"}
    )
    assert response.status_code == 409


def test_doi_email_sang_email_da_ton_tai(client, user):
    other = client.post(
        "/api/users", json={"email": "khac@example.com", "full_name": "Khác"}
    ).json()
    response = client.patch(f"/api/users/{other['id']}", json={"email": user["email"]})
    assert response.status_code == 409


def test_giu_nguyen_email_cua_chinh_minh_thi_khong_sao(client, user):
    """Cập nhật mà không đổi email không được coi là trùng với chính nó."""
    response = client.patch(
        f"/api/users/{user['id']}", json={"email": user["email"], "full_name": "An 2"}
    )
    assert response.status_code == 200
