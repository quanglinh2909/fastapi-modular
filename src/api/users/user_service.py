"""Nghiệp vụ của module User (@Service).

Chỉ tầng này được phép chứa business rule. Nó ném exception nghiệp vụ
(NotFoundError/ConflictError) chứ không biết gì về HTTP status code — việc
dịch sang HTTP do error_handlers lo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymodular.core.container import Lazy, injectable
from pymodular.core.exceptions import ConflictError, NotFoundError
from pymodular.core.logging import get_logger
from pymodular.core.schemas import apply_changes
from pymodular.infrastructure.database import Repository
from src.api.users.dto.user_dto import UserCreate, UserUpdate
from src.api.users.entities.user_model import User

if TYPE_CHECKING:
    # Chỉ để IDE/mypy hiểu kiểu. Lúc chạy thật khối này không thực thi, nên
    # không có import chéo => không có vòng tròn.
    from src.api.devices.device_service import DeviceService

log = get_logger(__name__)


@injectable
class UserService:
    def __init__(self, repo: Repository[User], devices: Lazy[DeviceService]) -> None:
        self._repo = repo
        # Lúc chạy đây là proxy — DeviceService chỉ thực sự được khởi tạo ở lần
        # gọi method đầu tiên (chỗ cắt vòng, tương đương forwardRef). Annotation
        # ghi kiểu thật để IDE và mypy gợi ý đúng method.
        self._devices: DeviceService = devices

    async def list_users(self, *, limit: int, offset: int) -> tuple[list[User], int]:
        return (
            await self._repo.find(limit=limit, offset=offset),
            await self._repo.count(),
        )

    async def get_user(self, user_id: str) -> User:
        user = await self._repo.get(user_id)
        if user is None:
            raise NotFoundError(f"Không tìm thấy user {user_id}")
        return user

    async def create_user(self, payload: UserCreate) -> User:
        if await self._repo.find_one(email=payload.email):
            raise ConflictError(f"Email {payload.email} đã được sử dụng")

        # model_dump() thay cho việc liệt kê từng field: thêm field vào
        # UserCreate là tự chảy xuống entity, không phải sửa chỗ này.
        user = await self._repo.save(User(id="", **payload.model_dump()))
        log.info("user.created", user_id=user.id)
        return user

    async def update_user(self, user_id: str, payload: UserUpdate) -> User:
        user = await self.get_user(user_id)

        # DTO đã hạ chữ thường nên so sánh thẳng được.
        new_email = payload.email
        if new_email and new_email != user.email:
            existing = await self._repo.find_one(email=new_email)
            if existing and existing.id != user_id:
                raise ConflictError(f"Email {new_email} đã được sử dụng")

        changed = apply_changes(user, payload)   # updated_at do repository lo
        log.info("user.updated", user_id=user_id, fields=changed)
        return await self._repo.save(user)

    async def delete_user(self, user_id: str, *, cascade: bool = False) -> int:
        """Xoá user; trả về số thiết bị đã bị xoá theo.

        Mặc định chặn: còn thiết bị gắn với user thì buộc người gọi nói rõ ý
        định bằng cascade=True. Quy tắc thuộc nghiệp vụ User nên nằm ở đây,
        dù dữ liệu nằm ở module Device.
        """
        await self.get_user(user_id)  # ném NotFoundError nếu user không tồn tại

        linked = await self._devices.count_by_owner(user_id)
        if linked and not cascade:
            raise ConflictError(
                f"User {user_id} còn {linked} thiết bị; xoá kèm bằng ?cascade=true",
                details={"devices": linked},
            )

        removed = await self._devices.delete_by_owner(user_id) if linked else 0
        await self._repo.delete(user_id)

        log.info("user.deleted", user_id=user_id, devices_removed=removed)
        return removed
