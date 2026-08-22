"""Namespace — sổ đăng ký các kết nối của MỘT gateway.

Mỗi gateway (mỗi đường dẫn WebSocket) có namespace riêng, nên phòng "alerts"
của `/ws/chat` và phòng "alerts" của `/ws/telemetry` là hai phòng khác nhau.
Giống namespace của socket.io mà NestJS dùng.

Ba chỉ mục được giữ song song, tất cả đều là O(1):

    socket_id -> Socket        gửi thẳng cho một kết nối
    room      -> {socket_id}   gửi cho một phòng
    user_id   -> {socket_id}   gửi cho một người, kể cả khi họ mở nhiều tab

Chỉ mục thứ ba là thứ hay bị bỏ sót. Không có nó thì "gửi thông báo cho user
X" phải quét toàn bộ kết nối; với vài nghìn kết nối, mỗi lần gửi là một vòng
lặp toàn bộ.

Mọi thao tác ở đây là ĐỒNG BỘ và chỉ chạy trong event loop của tiến trình này
nên không cần khoá. Nhiều worker thì mỗi worker có sổ riêng — xem adapter.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from pymodular.core.logging import get_logger

if TYPE_CHECKING:
    from pymodular.core.websocket.socket import Socket

log = get_logger(__name__)


class Namespace:
    __slots__ = ("_rooms", "_sockets", "_users", "path")

    def __init__(self, path: str) -> None:
        self.path = path
        self._sockets: dict[str, Socket] = {}
        self._rooms: dict[str, set[str]] = {}
        self._users: dict[str, set[str]] = {}

    # --------------------------------------------------------- vào/ra sổ
    def add(self, socket: Socket) -> None:
        self._sockets[socket.id] = socket
        if socket.user_id:
            self._users.setdefault(socket.user_id, set()).add(socket.id)

    def remove(self, socket: Socket) -> None:
        self._sockets.pop(socket.id, None)

        for room in list(socket.rooms):
            self.leave(socket, room)

        if socket.user_id:
            ids = self._users.get(socket.user_id)
            if ids is not None:
                ids.discard(socket.id)
                # Xoá khoá rỗng, nếu không dict phình theo số user từng vào.
                if not ids:
                    del self._users[socket.user_id]

    # ------------------------------------------------------------- phòng
    def join(self, socket: Socket, room: str) -> None:
        self._rooms.setdefault(room, set()).add(socket.id)
        socket.rooms.add(room)

    def leave(self, socket: Socket, room: str) -> None:
        members = self._rooms.get(room)
        if members is not None:
            members.discard(socket.id)
            if not members:
                del self._rooms[room]
        socket.rooms.discard(room)

    def room_size(self, room: str) -> int:
        return len(self._rooms.get(room, ()))

    def sockets_in(self, room: str) -> list[Socket]:
        return [s for sid in self._rooms.get(room, ()) if (s := self._sockets.get(sid))]

    def sockets_of(self, user_id: str) -> list[Socket]:
        return [s for sid in self._users.get(user_id, ()) if (s := self._sockets.get(sid))]

    def get(self, socket_id: str) -> Socket | None:
        return self._sockets.get(socket_id)

    # -------------------------------------------------------------- gửi
    def deliver(
        self,
        event: str,
        data: Any = None,
        *,
        room: str | None = None,
        user: str | None = None,
        socket: str | None = None,
        exclude: Iterable[str] = (),
    ) -> int:
        """Gửi cho các kết nối ĐANG Ở TIẾN TRÌNH NÀY. Trả về số tin đã xếp hàng.

        Không có tiêu chí nào (`room`/`user`/`socket` đều None) nghĩa là gửi
        cho toàn bộ namespace.
        """
        skip = set(exclude)
        count = 0
        for target in self._targets(room=room, user=user, socket=socket):
            if target.id in skip or target.closing:
                continue
            if target.emit(event, data):
                count += 1
        return count

    def _targets(
        self, *, room: str | None, user: str | None, socket: str | None
    ) -> Iterator[Socket]:
        if socket is not None:
            found = self._sockets.get(socket)
            if found is not None:
                yield found
            return
        if user is not None:
            yield from self.sockets_of(user)
            return
        if room is not None:
            yield from self.sockets_in(room)
            return
        yield from list(self._sockets.values())

    # ------------------------------------------------------------ thống kê
    def __len__(self) -> int:
        return len(self._sockets)

    @property
    def sockets(self) -> list[Socket]:
        return list(self._sockets.values())

    @property
    def rooms(self) -> dict[str, int]:
        return {room: len(ids) for room, ids in self._rooms.items()}

    def stats(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sockets": len(self._sockets),
            "users": len(self._users),
            "rooms": len(self._rooms),
            "pending": sum(s.pending for s in self._sockets.values()),
        }
