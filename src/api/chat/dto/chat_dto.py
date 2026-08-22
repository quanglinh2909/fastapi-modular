"""DTO của module Chat.

Dùng chung `InputSchema` với phía HTTP: payload WebSocket cũng được validate
bằng pydantic, sai thì client nhận khung `error` mang code `validation_error`
với đúng hình dạng như lỗi 422 của REST.
"""

from __future__ import annotations

from pydantic import Field

from fastapi_modular.core.schemas import InputSchema, OutputSchema


class RoomMessage(InputSchema):
    room: str = Field(min_length=1, max_length=128, description="Phòng nhận tin")
    text: str = Field(min_length=1, max_length=2000)


class DirectMessage(InputSchema):
    to_user: str = Field(min_length=1, description="user_id người nhận")
    text: str = Field(min_length=1, max_length=2000)


class RoomQuery(InputSchema):
    room: str = Field(min_length=1, max_length=128)


class BroadcastIn(InputSchema):
    """Body của endpoint HTTP đẩy tin xuống WebSocket."""

    room: str = Field(min_length=1, max_length=128)
    event: str = Field(default="notice", min_length=1, max_length=64)
    data: dict = Field(default_factory=dict)


class PublishIn(InputSchema):
    """Body của endpoint đăng tin lên RabbitMQ."""

    routing_key: str = Field(min_length=1, max_length=255, examples=["alert.created.hanoi"])
    data: dict = Field(default_factory=dict)
    exchange: str = Field(default="events", min_length=1, max_length=64)


class PublishOut(OutputSchema):
    published: bool
    exchange: str
    routing_key: str


class BroadcastOut(OutputSchema):
    delivered: int
    """Số kết nối TRONG WORKER NÀY đã nhận. Chạy nhiều worker thì các worker
    khác nhận qua adapter và không được tính vào đây."""
