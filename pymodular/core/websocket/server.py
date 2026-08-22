"""`WebSocketServer` — điểm vào duy nhất để đẩy tin, dùng được từ mọi nơi.

Tương đương `@WebSocketServer() server: Server` của NestJS, nhưng lấy qua DI
như mọi provider khác:

    @injectable
    class AlertService:
        def __init__(self, ws: WebSocketServer) -> None:
            self._ws = ws

        async def raise_alert(self, alert):
            await self._ws.to_room("alerts", "alert.created", {"id": alert.id})

Gửi được từ controller HTTP, từ service, từ tác vụ nền — không cần đang ở
trong một kết nối WebSocket nào. Server tự lo hai việc: gửi cho kết nối trong
tiến trình này, và đăng tin lên adapter cho các worker khác.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pymodular.core.config import Settings
from pymodular.core.container import injectable
from pymodular.core.logging import get_logger
from pymodular.core.metrics import ws_connections
from pymodular.core.websocket.adapter import (
    BroadcastAdapter,
    build_adapter,
    envelope,
    new_origin_id,
)
from pymodular.core.websocket.namespace import Namespace
from pymodular.core.websocket.protocol import CloseCode

log = get_logger(__name__)


@injectable
class WebSocketServer:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.ws
        self._namespaces: dict[str, Namespace] = {}
        self._origin = new_origin_id()
        self._adapter: BroadcastAdapter = build_adapter(
            self._config.adapter,
            url=self._config.redis_url,
            channel=self._config.channel,
            origin=self._origin,
        )
        self._started = False

    # ------------------------------------------------------------- namespace
    def namespace(self, path: str) -> Namespace:
        """Lấy (hoặc tạo) namespace của một đường dẫn WebSocket."""
        found = self._namespaces.get(path)
        if found is None:
            found = self._namespaces[path] = Namespace(path)
        return found

    @property
    def namespaces(self) -> dict[str, Namespace]:
        return dict(self._namespaces)

    def _resolve(self, path: str | None) -> Namespace:
        if path is not None:
            return self.namespace(path)
        if len(self._namespaces) == 1:
            return next(iter(self._namespaces.values()))
        raise RuntimeError(
            "Có nhiều gateway nên phải nói rõ gửi vào namespace nào: "
            f"emit(..., namespace='/ws/chat'). Đang có: {sorted(self._namespaces)}"
        )

    # ------------------------------------------------------------------ gửi
    async def emit(
        self,
        event: str,
        data: Any = None,
        *,
        namespace: str | None = None,
        room: str | None = None,
        user: str | None = None,
        socket: str | None = None,
        exclude: Iterable[str] = (),
        local_only: bool = False,
    ) -> int:
        """Đẩy một tin. Trả về số kết nối TẠI TIẾN TRÌNH NÀY đã nhận.

        Số trả về cố ý không tính các worker khác: adapter là fire-and-forget,
        biết chắc bên kia gửi được bao nhiêu thì phải chờ phản hồi, mà chờ thì
        mất hết ý nghĩa "gửi không chặn".
        """
        target = self._resolve(namespace)
        delivered = target.deliver(
            event, data, room=room, user=user, socket=socket, exclude=exclude
        )

        if not local_only and self._adapter.name != "local":
            await self._adapter.publish(
                envelope(
                    self._origin,
                    target.path,
                    event,
                    data,
                    room=room,
                    user=user,
                    socket=socket,
                    exclude=exclude,
                )
            )
        return delivered

    async def to_room(self, room: str, event: str, data: Any = None, **kwargs: Any) -> int:
        return await self.emit(event, data, room=room, **kwargs)

    async def to_user(self, user_id: str, event: str, data: Any = None, **kwargs: Any) -> int:
        """Gửi cho MỘT người — tới mọi kết nối của họ (nhiều tab, nhiều thiết bị)."""
        return await self.emit(event, data, user=user_id, **kwargs)

    async def to_socket(self, socket_id: str, event: str, data: Any = None, **kwargs: Any) -> int:
        return await self.emit(event, data, socket=socket_id, **kwargs)

    async def broadcast(self, event: str, data: Any = None, **kwargs: Any) -> int:
        """Gửi cho mọi kết nối trong namespace."""
        return await self.emit(event, data, **kwargs)

    # ------------------------------------------------------- tin từ worker khác
    def _on_remote(self, payload: dict[str, Any]) -> None:
        path = payload.get("ns")
        if not path or path not in self._namespaces:
            return   # worker này không phục vụ namespace đó
        self._namespaces[path].deliver(
            payload.get("event", ""),
            payload.get("data"),
            room=payload.get("room"),
            user=payload.get("user"),
            socket=payload.get("socket"),
            exclude=payload.get("exclude") or (),
        )

    # ------------------------------------------------------------ vòng đời
    async def startup(self) -> None:
        if self._started:
            return
        await self._adapter.start(self._on_remote)
        self._started = True

    async def shutdown(self) -> None:
        """Đóng mọi kết nối tử tế trước khi tiến trình thoát.

        Mã 1001 (going away) báo cho client biết đây là restart chứ không phải
        lỗi, để phía client nối lại ngay thay vì chờ hết backoff.
        """
        for namespace in self._namespaces.values():
            for socket in namespace.sockets:
                await socket.close(CloseCode.GOING_AWAY, "server đang tắt")
            ws_connections.set(0, namespace=namespace.path)
        await self._adapter.stop()
        self._namespaces.clear()
        self._started = False

    # ------------------------------------------------------------ thống kê
    @property
    def adapter_name(self) -> str:
        return self._adapter.name

    def stats(self) -> dict[str, Any]:
        return {
            "adapter": self._adapter.name,
            "origin": self._origin,
            "namespaces": [ns.stats() for ns in self._namespaces.values()],
            "connections": sum(len(ns) for ns in self._namespaces.values()),
        }
