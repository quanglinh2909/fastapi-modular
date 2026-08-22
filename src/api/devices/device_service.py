"""Nghiệp vụ của module Device.

Module này phụ thuộc UserService (kiểm tra chủ sở hữu) và ngược lại UserService
cũng phụ thuộc nó (xoá user thì dọn thiết bị). Vòng tròn được cắt ở phía User
bằng Lazy[...]; phía này khai báo phụ thuộc bình thường.
"""

from __future__ import annotations

from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import ConflictError, NotFoundError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.schemas import apply_changes
from fastapi_modular.infrastructure.database import Repository
from src.api.devices.dto.device_dto import DeviceCreate, DeviceUpdate
from src.api.devices.entities.device_model import Device
from src.api.users.user_service import UserService

log = get_logger(__name__)


@injectable
class DeviceService:
    def __init__(self, repo: Repository[Device], users: UserService) -> None:
        self._repo = repo
        self._users = users

    async def list_devices(
        self, *, limit: int, offset: int, owner_id: str | None = None
    ) -> tuple[list[Device], int]:
        devices = await self._repo.find(limit=limit, offset=offset, owner_id=owner_id)
        return devices, await self._repo.count(owner_id=owner_id)

    async def get_device(self, device_id: str) -> Device:
        device = await self._repo.get(device_id)
        if device is None:
            raise NotFoundError(f"Không tìm thấy thiết bị {device_id}")
        return device

    async def create_device(self, payload: DeviceCreate) -> Device:
        # Ném NotFoundError nếu owner không tồn tại — kiểm tra qua service của
        # module User, không truy vấn thẳng bảng users.
        await self._users.get_user(payload.owner_id)

        if await self._repo.find_one(serial=payload.serial):
            raise ConflictError(f"Serial {payload.serial} đã tồn tại")

        device = await self._repo.save(Device(id="", **payload.model_dump()))
        log.info("device.created", device_id=device.id, owner_id=device.owner_id)
        return device

    async def update_device(self, device_id: str, payload: DeviceUpdate) -> Device:
        device = await self.get_device(device_id)
        changed = apply_changes(device, payload)   # updated_at do repository lo
        log.info("device.updated", device_id=device_id, fields=changed)
        return await self._repo.save(device)

    async def count_by_owner(self, owner_id: str) -> int:
        return await self._repo.count(owner_id=owner_id)

    async def delete_by_owner(self, owner_id: str) -> int:
        """Xoá mọi thiết bị của một user. UserService gọi khi cascade delete."""
        removed = await self._repo.delete_where(owner_id=owner_id)
        if removed:
            log.info("device.deleted_by_owner", owner_id=owner_id, count=removed)
        return removed

    async def delete_device(self, device_id: str) -> None:
        if not await self._repo.delete(device_id):
            raise NotFoundError(f"Không tìm thấy thiết bị {device_id}")
        log.info("device.deleted", device_id=device_id)
