"""DTO vào/ra của module User (tương đương DTO class trong NestJS).

Ba DTO cùng một entity nên để chung một file: chúng kế thừa lẫn nhau, tách ra
ba file chỉ tạo thêm import chéo mà không rõ ràng hơn. Module có nhiều entity
thì tách theo entity (`user_dto.py`, `profile_dto.py`), không tách theo
thao tác.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from fastapi_modular.core.schemas import InputSchema, OutputSchema, partial_of

# Dùng regex thay cho EmailStr để không phải thêm dependency `email-validator`.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserBase(InputSchema):
    """Field client được phép ghi, khai báo đúng MỘT lần.

    `extra="forbid"` và cắt khoảng trắng thừa kế thừa từ InputSchema.
    """

    email: str = Field(pattern=EMAIL_PATTERN, examples=["an@example.com"])
    full_name: str = Field(min_length=1, max_length=100, examples=["Nguyễn Văn An"])

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        """Hạ chữ thường ngay ở cửa vào.

        Nhờ vậy unique index trên cột `email` (vốn phân biệt hoa thường) đủ để
        chặn trùng, và truy vấn tìm theo email dùng được index thay vì phải
        quét toàn bảng bằng predicate Python.
        """
        return value.lower()


class UserCreate(UserBase):
    """POST: mọi field trong UserBase đều bắt buộc."""


class UserUpdate(partial_of(UserBase)):
    """PATCH: mọi field của UserBase thành optional, cộng thêm is_active.

    `is_active` chỉ sửa được chứ không đặt lúc tạo, nên nó nằm ở đây chứ không
    ở UserBase.
    """

    is_active: bool | None = None


class UserOut(OutputSchema):
    """Cố ý liệt kê tường minh thay vì sinh từ entity: entity về sau có thể
    thêm trường nội bộ (mật khẩu băm, cờ hệ thống) mà không được lộ ra API."""

    id: str
    email: str
    full_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
