"""HTTP layer của module Device.

Hai controller: một cho /devices, một cho các route nằm dưới /users nhưng trả
về thiết bị. Route thứ hai thuộc module này vì dữ liệu nó trả về là thiết bị —
khai báo bên module User sẽ tạo vòng tròn import. Đường dẫn URL và quyền sở
hữu module là hai chuyện khác nhau.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query, status

from pymodular.core.controller import controller, delete, get, patch, post
from pymodular.core.schemas import Page
from src.api.devices.device_service import DeviceService
from src.api.devices.dto.device_dto import DeviceCreate, DeviceOut, DeviceUpdate

DeviceId = Annotated[str, Path(description="ID của thiết bị")]


def _page(devices, total, limit, offset) -> Page[DeviceOut]:
    return Page(
        items=[DeviceOut.model_validate(d) for d in devices],
        total=total,
        limit=limit,
        offset=offset,
    )


@controller(prefix="/devices", tags=["devices"])
class DeviceController:
    def __init__(self, service: DeviceService) -> None:
        self._service = service

    @get("", response_model=Page[DeviceOut], summary="Danh sách thiết bị")
    async def list_devices(
        self,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        owner_id: Annotated[str | None, Query(description="Lọc theo chủ sở hữu")] = None,
    ) -> Page[DeviceOut]:
        devices, total = await self._service.list_devices(
            limit=limit, offset=offset, owner_id=owner_id
        )
        return _page(devices, total, limit, offset)

    @get("/{device_id}", response_model=DeviceOut, summary="Chi tiết thiết bị")
    async def get_device(self, device_id: DeviceId) -> DeviceOut:
        return DeviceOut.model_validate(await self._service.get_device(device_id))

    @post(
        "",
        response_model=DeviceOut,
        status_code=status.HTTP_201_CREATED,
        summary="Đăng ký thiết bị",
    )
    async def create_device(self, payload: DeviceCreate) -> DeviceOut:
        return DeviceOut.model_validate(await self._service.create_device(payload))

    @patch("/{device_id}", response_model=DeviceOut, summary="Cập nhật thiết bị")
    async def update_device(self, device_id: DeviceId, payload: DeviceUpdate) -> DeviceOut:
        return DeviceOut.model_validate(
            await self._service.update_device(device_id, payload)
        )

    @delete(
        "/{device_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Gỡ thiết bị",
    )
    async def delete_device(self, device_id: DeviceId) -> None:
        await self._service.delete_device(device_id)


@controller(prefix="/users", tags=["devices"])
class DeviceByOwnerController:
    def __init__(self, service: DeviceService) -> None:
        self._service = service

    @get(
        "/{owner_id}/devices",
        response_model=Page[DeviceOut],
        summary="Thiết bị của một user",
    )
    async def list_devices_of_user(
        self,
        owner_id: Annotated[str, Path(description="ID của user")],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Page[DeviceOut]:
        devices, total = await self._service.list_devices(
            limit=limit, offset=offset, owner_id=owner_id
        )
        return _page(devices, total, limit, offset)


