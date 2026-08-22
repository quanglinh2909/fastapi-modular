"""Gateway Chat — bản mẫu dùng đủ các tính năng của lớp WebSocket.

Kết nối:  ws://localhost:8000/ws/chat?client_id=an

Có ở đây: guard lúc bắt tay, hook vòng đời, phòng do client tự vào (kèm kiểm
tra quyền), gửi cho phòng, gửi thẳng cho một người, và ack cho từng lệnh.
"""

from __future__ import annotations

from typing import Any

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.exceptions import NotFoundError
from fastapi_modular.core.guards import RequireHeader
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.websocket import Socket, WebSocketServer, gateway, subscribe
from src.api.chat.dto.chat_dto import DirectMessage, RoomMessage, RoomQuery

log = get_logger(__name__)

LOBBY = "lobby"


@gateway(path="/ws/chat", guards=[RequireHeader], client_rooms=True)
class ChatGateway:
    def __init__(self, server: WebSocketServer) -> None:
        # WebSocketServer là provider bình thường: gateway, service, controller
        # HTTP hay tác vụ nền đều lấy được cùng một instance.
        self._server = server

    # ------------------------------------------------------------ vòng đời
    async def on_connect(self, socket: Socket) -> None:
        """Chạy sau khi guard cho qua, trước khi client nhận khung `connected`."""
        socket.join(LOBBY)
        await self._server.to_room(
            LOBBY,
            "presence.join",
            {"user_id": socket.user_id, "at": utcnow()},
            namespace=socket.namespace.path,
            exclude=[socket.id],   # người vừa vào không cần tự báo cho mình
        )

    async def on_disconnect(self, socket: Socket, code: int) -> None:
        """Chạy khi kết nối đứt, dù vì lý do gì. Sổ phòng đã tự dọn."""
        await self._server.to_room(
            LOBBY,
            "presence.leave",
            {"user_id": socket.user_id, "code": code},
            namespace=socket.namespace.path,
            exclude=[socket.id],
        )

    def can_join(self, socket: Socket, room: str) -> bool:
        """Chốt chặn cho `room.join` do client gửi lên.

        `client_rooms=True` mà không có hook này thì ai cũng vào được phòng bất
        kỳ — kể cả phòng riêng của người khác. Ở đây chỉ cho vào phòng công
        khai `room:*` và phòng riêng của chính mình.
        """
        return room == LOBBY or room.startswith("room:") or room == f"user:{socket.user_id}"

    # -------------------------------------------------------------- sự kiện
    @subscribe("message.send")
    async def send_to_room(self, socket: Socket, payload: RoomMessage) -> dict[str, Any]:
        """Gửi tin cho cả phòng.

        Giá trị trả về được gửi lại làm ack (khi client có kèm `id`), nên
        không phải tự emit thêm.
        """
        if not socket.in_room(payload.room):
            raise NotFoundError(f"Bạn chưa vào phòng '{payload.room}'")

        delivered = await self._server.to_room(
            payload.room,
            "message.new",
            {
                "room": payload.room,
                "from": socket.user_id,
                "text": payload.text,
                "at": utcnow(),
            },
            namespace=socket.namespace.path,
        )
        return {"delivered": delivered, "room": payload.room}

    @subscribe("message.direct")
    async def send_to_user(self, socket: Socket, payload: DirectMessage) -> dict[str, Any]:
        """Gửi thẳng cho MỘT người, không qua phòng nào.

        Đi tới mọi kết nối của người đó — họ mở ba tab thì cả ba đều nhận.
        """
        delivered = await self._server.to_user(
            payload.to_user,
            "message.direct",
            {"from": socket.user_id, "text": payload.text, "at": utcnow()},
            namespace=socket.namespace.path,
        )
        # delivered == 0 nghĩa là người đó đang offline (ở worker này). Tin
        # realtime không có hộp thư chờ — muốn giữ lại thì tự lưu database.
        return {"delivered": delivered, "online": delivered > 0}

    @subscribe("presence.list")
    async def list_room(self, socket: Socket, payload: RoomQuery) -> dict[str, Any]:
        members = sorted(
            {s.user_id for s in socket.namespace.sockets_in(payload.room) if s.user_id}
        )
        return {"room": payload.room, "members": members, "size": len(members)}

    @subscribe("whoami")
    async def whoami(self, socket: Socket) -> dict[str, Any]:
        """Handler không cần payload thì bỏ luôn tham số thứ hai."""
        return {
            "socket_id": socket.id,
            "user_id": socket.user_id,
            "roles": sorted(socket.roles),
            "rooms": sorted(socket.rooms),
        }
