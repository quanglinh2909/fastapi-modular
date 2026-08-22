"""HTTP layer của module User (@Controller).

Controller chỉ làm ba việc: khai báo đường dẫn, validate input qua schema, và
chuyển entity thành DTO. Không có business rule nào ở đây.

Service được khai báo một lần ở __init__ — mọi handler dùng chung self._service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query, status

from fastapi_modular.core.controller import controller, delete, get, patch, post
from fastapi_modular.core.schemas import Page
from src.api.users.dto.user_dto import UserCreate, UserOut, UserUpdate
from src.api.users.user_service import UserService

UserId = Annotated[str, Path(description="ID của user")]


@controller(prefix="/users", tags=["users"])
class UserController:
    def __init__(self, service: UserService) -> None:
        self._service = service

    @get("", response_model=Page[UserOut], summary="Danh sách user")
    async def list_users(
        self,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Page[UserOut]:
        users, total = await self._service.list_users(limit=limit, offset=offset)
        return Page(
            items=[UserOut.model_validate(u) for u in users],
            total=total,
            limit=limit,
            offset=offset,
        )

    @get("/{user_id}", response_model=UserOut, summary="Chi tiết user")
    async def get_user(self, user_id: UserId) -> UserOut:
        return UserOut.model_validate(await self._service.get_user(user_id))

    @post(
        "",
        response_model=UserOut,
        status_code=status.HTTP_201_CREATED,
        summary="Tạo user",
    )
    async def create_user(self, payload: UserCreate) -> UserOut:
        return UserOut.model_validate(await self._service.create_user(payload))

    @patch("/{user_id}", response_model=UserOut, summary="Cập nhật user")
    async def update_user(self, user_id: UserId, payload: UserUpdate) -> UserOut:
        return UserOut.model_validate(await self._service.update_user(user_id, payload))

    @delete(
        "/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Xoá user",
        description="Mặc định trả 409 nếu user còn thiết bị. Dùng cascade=true để xoá kèm.",
    )
    async def delete_user(
        self,
        user_id: UserId,
        cascade: Annotated[bool, Query(description="Xoá luôn thiết bị của user")] = False,
    ) -> None:
        await self._service.delete_user(user_id, cascade=cascade)


