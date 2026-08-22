"""Lớp nền cho DTO và vài tiện ích dùng chung cho mọi module.

Ba thứ ở đây thay cho việc lặp `model_config` trong từng file DTO:

- `InputSchema`  — nền cho DTO đi vào (body request)
- `OutputSchema` — nền cho DTO đi ra (response)
- `partial_of()` — sinh biến thể "mọi field optional" cho PATCH, giữ nguyên
  ràng buộc validate, thay cho việc chép tay lần thứ hai
"""

from __future__ import annotations

import copy
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, create_model

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=BaseModel)


class InputSchema(BaseModel):
    """Nền cho mọi DTO nhận từ client.

    - `extra="forbid"`: gửi thừa field sẽ bị 422 thay vì âm thầm bỏ qua, nhờ
      vậy client gõ sai tên field biết ngay.
    - `str_strip_whitespace`: cắt khoảng trắng thừa hai đầu chuỗi, tránh cảnh
      `"An "` và `"An"` thành hai giá trị khác nhau trong database.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OutputSchema(BaseModel):
    """Nền cho mọi DTO trả về client.

    `from_attributes` cho phép `model_validate()` đọc thẳng từ dataclass entity
    hay object ORM, không phải chuyển sang dict trước.
    """

    model_config = ConfigDict(from_attributes=True)


def partial_of(model: type[ModelT], *, name: str | None = None) -> type[BaseModel]:
    """Sinh bản sao của `model` với mọi field thành optional (mặc định None).

    Dùng cho PATCH: chỉ field nào client gửi lên mới bị ghi đè. Ràng buộc
    validate (pattern, min_length, ge/le...) được giữ nguyên, nên không phải
    chép lại lần thứ hai và không sợ hai bản lệch nhau khi sửa.

    Cách dùng thường gặp — kế thừa để thêm field chỉ có ở PATCH:

        class UserUpdate(partial_of(UserBase)):
            is_active: bool | None = None

    `name` là TUỲ CHỌN và hầu như không cần. Nó chỉ đặt tên cho class được sinh
    ra, mà class đó là LỚP CHA ẩn khi bạn kế thừa — tên hiện trong OpenAPI là
    tên lớp con của bạn. Chỉ truyền `name` khi dùng thẳng kết quả, vì lúc đó
    tên sinh ra chính là tên schema client nhìn thấy:

        DeviceFilter = partial_of(DeviceBase, name="DeviceFilter")
        # không có name thì schema sẽ mang tên "DeviceBasePartial"

    Đừng đặt `name` trùng tên lớp con — sẽ có hai class cùng tên, và nếu cả hai
    cùng xuất hiện trong OpenAPI thì FastAPI phải tự thêm tiền tố để phân biệt.
    """
    fields: dict[str, Any] = {}

    for field_name, field in model.model_fields.items():
        optional = copy.deepcopy(field)
        optional.default = None
        optional.default_factory = None
        fields[field_name] = (field.annotation | None, optional)

    # Kế thừa chính `model` chứ không phải lớp cha của nó: nhờ vậy field
    # validator, model validator và model_config tuỳ chỉnh đều được giữ. Field
    # bắt buộc bị ghi đè thành optional ngay bên dưới nên không mâu thuẫn.
    return create_model(  # type: ignore[call-overload]
        name or f"{model.__name__}Partial",
        __base__=model,
        __doc__=f"Bản PATCH của {model.__name__}: mọi field optional.",
        **fields,
    )


def apply_changes(entity: Any, payload: BaseModel) -> list[str]:
    """Chép các field client THỰC SỰ gửi lên vào entity. Trả về tên field đã đổi.

    `exclude_unset=True` là mấu chốt: nó phân biệt "không gửi field này" với
    "gửi field này = null". Thiếu nó thì PATCH một field sẽ xoá trắng các field
    còn lại.
    """
    changes = payload.model_dump(exclude_unset=True)
    for field_name, value in changes.items():
        setattr(entity, field_name, value)
    return sorted(changes)


class Page(BaseModel, Generic[T]):
    """Bao ngoài cho danh sách có phân trang."""

    items: list[T]
    total: int
    limit: int
    offset: int
