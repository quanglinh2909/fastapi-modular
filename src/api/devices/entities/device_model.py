"""Entity của module Device."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.compat import StrEnum
from fastapi_modular.core.container import entity


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


@entity(
    # "serial" là cột đơn: duy nhất trên toàn bảng.
    # ("owner_id", "name") là CỤM: một chủ sở hữu không được đặt trùng tên hai
    # thiết bị, nhưng hai chủ khác nhau vẫn được trùng tên với nhau.
    unique=["serial", ("owner_id", "name")],
    indexes=[
        # CỤM: cho truy vấn "thiết bị của chủ X đang ở trạng thái Y".
        # Theo quy tắc TIỀN TỐ TRÁI, cụm này cũng phục vụ được truy vấn chỉ lọc
        # theo owner_id — nên KHÔNG cần thêm index riêng cho owner_id.
        ("owner_id", "status"),
        # ĐƠN: cho truy vấn "mọi thiết bị đang offline" của trang quản trị.
        # Cụm ở trên KHÔNG dùng được cho truy vấn này, vì status nằm sau
        # owner_id mà truy vấn lại không có điều kiện trên owner_id.
        "status",
    ],
)
@dataclass(slots=True)
class Device:
    id: str
    name: str
    serial: str
    owner_id: str
    status: DeviceStatus = DeviceStatus.OFFLINE
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
