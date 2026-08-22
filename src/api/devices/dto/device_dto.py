"""DTO vào/ra của module Device."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from fastapi_modular.core.schemas import InputSchema, OutputSchema, partial_of
from src.api.devices.entities.device_model import DeviceStatus


class DeviceBase(InputSchema):
    """Field vừa đặt được lúc tạo, vừa sửa được sau đó."""

    name: str = Field(min_length=1, max_length=100, examples=["Cảm biến tầng 1"])


class DeviceCreate(DeviceBase):
    """POST. `serial` và `owner_id` chỉ đặt lúc tạo, sau đó bất biến — nên
    chúng nằm ở đây chứ không ở DeviceBase, và PATCH sẽ không nhận."""

    serial: str = Field(min_length=3, max_length=64, examples=["SN-0001"])
    owner_id: str = Field(description="ID của user sở hữu thiết bị")


class DeviceUpdate(partial_of(DeviceBase)):
    """PATCH: đổi tên và trạng thái."""

    status: DeviceStatus | None = None


class DeviceOut(OutputSchema):
    id: str
    name: str
    serial: str
    owner_id: str
    status: DeviceStatus
    created_at: datetime
    updated_at: datetime
