"""Test lớp nền DTO ở core/schemas.py."""

from __future__ import annotations

import pytest
from pydantic import Field, ValidationError, field_validator

from pymodular.core.schemas import InputSchema, OutputSchema, apply_changes, partial_of


class Sample(InputSchema):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    age: int = Field(ge=0, le=150)
    name: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.lower()


SamplePatch = partial_of(Sample, name="SamplePatch")


def test_input_schema_cam_field_la():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Sample(email="a@b.co", age=1, name="A", thua=True)


def test_input_schema_cat_khoang_trang():
    assert Sample(email="a@b.co", age=1, name="  A  ").name == "A"


def test_partial_moi_field_thanh_optional():
    assert SamplePatch().model_dump(exclude_unset=True) == {}
    assert set(SamplePatch.model_fields) == set(Sample.model_fields)


def test_partial_giu_nguyen_rang_buoc():
    """Đây là lý do sinh tự động thay vì chép tay: hai bản không thể lệch nhau."""
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        SamplePatch(email="sai")
    with pytest.raises(ValidationError, match="less_than_equal"):
        SamplePatch(age=999)
    with pytest.raises(ValidationError, match="too_short"):
        SamplePatch(name="")


def test_partial_van_cam_field_la():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SamplePatch(thua=1)


def test_apply_changes_chi_chep_field_duoc_gui():
    class Entity:
        def __init__(self) -> None:
            self.email = "cu@x.co"
            self.age = 30
            self.name = "Cũ"

    entity = Entity()
    changed = apply_changes(entity, SamplePatch(name="Mới"))

    assert changed == ["name"]
    assert entity.name == "Mới"
    assert entity.email == "cu@x.co", "field không gửi thì không được đụng tới"
    assert entity.age == 30


def test_apply_changes_phan_biet_null_voi_khong_gui():
    class Entity:
        def __init__(self) -> None:
            self.name = "Cũ"

    entity = Entity()
    apply_changes(entity, SamplePatch(name=None))
    assert entity.name is None, "gửi null là cố ý xoá, khác với không gửi"


def test_output_schema_doc_duoc_tu_object():
    class Row:
        id = "abc"
        label = "xyz"

    class Out(OutputSchema):
        id: str
        label: str

    assert Out.model_validate(Row()).label == "xyz"


def test_partial_khong_can_truyen_name():
    """`name` là tuỳ chọn; không truyền thì lấy tên mặc định."""
    generated = partial_of(Sample)
    assert generated.__name__ == "SamplePartial"
    assert set(generated.model_fields) == set(Sample.model_fields)


# Model phải ở cấp module: `from __future__ import annotations` biến annotation
# của endpoint thành chuỗi, mà FastAPI giải chuỗi đó trong globals của module —
# class định nghĩa bên trong hàm sẽ không tìm thấy.
class WithoutName(partial_of(Sample)):
    extra_flag: bool | None = None


class WithName(partial_of(Sample, name="DatTenKhac")):
    extra_flag: bool | None = None


Direct = partial_of(Sample, name="SampleFilter")


def _schema_names(app) -> set[str]:
    return set(app.openapi()["components"]["schemas"])


def test_name_khong_anh_huong_openapi_khi_ke_thua():
    """Kế thừa thì tên schema lấy từ lớp con, nên `name` chỉ là trang trí."""
    from fastapi import FastAPI

    app = FastAPI()

    # Annotation phải là tên có thật trong globals của module này — không dùng
    # được biến trung gian, vì `from __future__ import annotations` biến nó
    # thành chuỗi và FastAPI giải chuỗi đó trong globals.
    @app.patch("/a")
    async def _a(payload: WithoutName) -> dict:
        return {}

    @app.patch("/b")
    async def _b(payload: WithName) -> dict:
        return {}

    names = _schema_names(app)
    assert "WithoutName" in names
    assert "WithName" in names
    assert "DatTenKhac" not in names, "tên truyền vào không lọt ra OpenAPI"


def test_name_co_tac_dung_khi_dung_thang():
    """Dùng thẳng kết quả thì tên sinh ra CHÍNH LÀ tên schema client thấy."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.patch("/c")
    async def _c(payload: Direct) -> dict:
        return {}

    assert "SampleFilter" in _schema_names(app)


def test_partial_giu_validator_cua_lop_goc():
    """Bản PATCH phải chạy cùng validator với bản create, nếu không hai đường
    ghi sẽ chuẩn hoá dữ liệu khác nhau."""
    assert SamplePatch(email="A@B.CO").email == "a@b.co"
