"""Một kết nối của một client.

`Socket` bọc quanh `WebSocket` của Starlette và thêm ba thứ mà bản trần không
có, nhưng thiếu chúng thì không chạy nổi ngoài đời:

1. **Hàng đợi gửi + một task ghi duy nhất.** Starlette KHÔNG cho hai task cùng
   `send_text()` trên một kết nối — làm vậy sẽ hỏng khung tin hoặc ném
   RuntimeError. Mà broadcast thì bản chất là nhiều nơi cùng gửi. Nên mọi lời
   gọi `emit()` chỉ bỏ tin vào hàng đợi; đúng một task lấy ra và ghi.

2. **Chống nghẽn (backpressure).** Hàng đợi có TRẦN. Client đọc chậm (mạng 3G,
   tab bị treo) mà server cứ đẩy thì bộ nhớ server phình tới lúc sập — một
   client hỏng kéo sập cả server. Đầy hàng đợi thì xử theo chính sách đã chọn:
   ngắt kết nối (mặc định) hoặc bỏ tin cũ nhất.

3. **Danh tính và phòng.** `user_id` (một người có thể mở nhiều tab = nhiều
   socket) và tập phòng đang tham gia.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocket, WebSocketState

from pymodular.core.compat import TimeoutErrors
from pymodular.core.logging import get_logger
from pymodular.core.metrics import ws_messages_out, ws_send_dropped
from pymodular.core.websocket.protocol import CloseCode, Frame

if TYPE_CHECKING:
    from pymodular.core.websocket.namespace import Namespace

log = get_logger(__name__)

# Bỏ vào hàng đợi để báo task ghi dừng lại.
_STOP = object()


class Socket:
    """Một client đang kết nối. Do framework tạo, code nghiệp vụ chỉ dùng."""

    __slots__ = (
        "_close_hooks",
        "_closer",
        "_closing",
        "_overflow",
        "_queue",
        "_writer",
        "connected_at",
        "data",
        "id",
        "namespace",
        "roles",
        "rooms",
        "user_id",
        "ws",
    )

    def __init__(
        self,
        ws: WebSocket,
        namespace: Namespace,
        *,
        user_id: str | None = None,
        roles: frozenset[str] = frozenset(),
        queue_size: int = 100,
        overflow: str = "close",
    ) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.ws = ws
        self.namespace = namespace
        self.user_id = user_id
        self.roles = roles
        self.rooms: set[str] = set()
        # Chỗ để handler ghi trạng thái riêng của kết nối này (tên hiển thị,
        # bộ lọc đang chọn...). Không đụng tới framework.
        self.data: dict[str, Any] = {}
        self.connected_at = time.time()

        self._queue: asyncio.Queue[Frame | object] = asyncio.Queue(maxsize=queue_size)
        self._overflow = overflow
        self._writer: asyncio.Task[None] | None = None
        self._closer: asyncio.Task[None] | None = None
        self._closing = False
        self._close_hooks: list[Callable[[Socket], Awaitable[None]]] = []

    # ------------------------------------------------------------------ gửi
    def emit(
        self,
        event: str,
        data: Any = None,
        *,
        ack: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        """Xếp một tin vào hàng đợi gửi. Trả về False nếu tin bị bỏ.

        KHÔNG phải coroutine: gọi được từ bất cứ đâu (kể cả handler HTTP) mà
        không phải await, và không bao giờ chặn người gọi vì client chậm.
        """
        return self.send(Frame(event=event, data=data, ack=ack, meta=meta))

    def send(self, frame: Frame) -> bool:
        if self._closing:
            return False

        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            return self._on_full(frame)

        ws_messages_out.inc(namespace=self.namespace.path, event=frame.event)
        return True

    def _on_full(self, frame: Frame) -> bool:
        """Hàng đợi đầy: client không theo kịp tốc độ server đẩy."""
        ws_send_dropped.inc(namespace=self.namespace.path)

        if self._overflow == "drop_oldest":
            # Hợp với dữ liệu "chỉ cần bản mới nhất" (vị trí, nhiệt độ, tiến độ).
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(frame)
                return True
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                return False

        # Mặc định: ngắt kết nối. Client tự nối lại và tải lại trạng thái, như
        # vậy trung thực hơn là âm thầm nuốt tin khiến client tưởng mình vẫn
        # đang đồng bộ.
        log.warning(
            "ws.slow_consumer",
            socket_id=self.id,
            user_id=self.user_id,
            namespace=self.namespace.path,
            queue_size=self._queue.maxsize,
        )
        self.close_soon(CloseCode.TRY_AGAIN_LATER, "client đọc quá chậm")
        return False

    # ----------------------------------------------------------------- vòng ghi
    def start_writer(self) -> None:
        self._writer = asyncio.create_task(self._writer_loop(), name=f"ws-writer-{self.id}")

    async def _writer_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            try:
                await self.ws.send_text(item.to_json())  # type: ignore[union-attr]
            except (RuntimeError, ConnectionError, asyncio.CancelledError):
                # Kết nối đã đứt giữa chừng; vòng nhận sẽ dọn dẹp.
                return
            except Exception as exc:  # noqa: BLE001 - lỗi khi ghi socket không được giết vòng nhận
                log.warning("ws.send_failed", socket_id=self.id, error=str(exc))
                return

    async def stop_writer(self) -> None:
        if self._writer is None:
            return
        # Đợi task ghi xả nốt hàng đợi, đừng cắt ngang giữa lúc đang ghi.
        try:
            self._queue.put_nowait(_STOP)
        except asyncio.QueueFull:
            self._writer.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._writer), timeout=2.0)
        except (*TimeoutErrors, asyncio.CancelledError):
            self._writer.cancel()
        self._writer = None

    # ------------------------------------------------------- dọn khi đóng
    def on_close(self, hook: Callable[[Socket], Awaitable[None]]) -> None:
        """Đăng ký việc cần làm khi kết nối này đóng.

        Đây là chỗ để những lớp NGOÀI framework gắn phần dọn dẹp của mình mà
        không phải sửa vào lõi WebSocket — ví dụ lớp nhắn tin gỡ các đăng ký
        sự kiện của kết nối. Lõi chỉ biết "có việc phải gọi", không biết đó là
        việc gì.
        """
        self._close_hooks.append(hook)

    async def run_close_hooks(self) -> None:
        for hook in self._close_hooks:
            try:
                await hook(self)
            except Exception as exc:  # noqa: BLE001 - một hook hỏng không được cản phần dọn còn lại
                log.warning("ws.close_hook_failed", socket_id=self.id, error=str(exc))
        self._close_hooks.clear()

    # ------------------------------------------------------------------ phòng
    def join(self, room: str) -> None:
        """Vào phòng. Gọi lại nhiều lần không sao."""
        self.namespace.join(self, room)

    def leave(self, room: str) -> None:
        self.namespace.leave(self, room)

    def in_room(self, room: str) -> bool:
        return room in self.rooms

    # ------------------------------------------------------------------- đóng
    def close_soon(self, code: int = CloseCode.NORMAL, reason: str = "") -> None:
        """Yêu cầu đóng mà không cần await — dùng được trong hàm đồng bộ."""
        if self._closing:
            return
        self._closing = True
        # Giữ tham chiếu: task không có ai nắm có thể bị GC dọn giữa chừng.
        self._closer = asyncio.create_task(self._close(code, reason), name=f"ws-close-{self.id}")

    async def close(self, code: int = CloseCode.NORMAL, reason: str = "") -> None:
        if self._closing:
            return
        self._closing = True
        await self._close(code, reason)

    async def _close(self, code: int, reason: str) -> None:
        try:
            if self.ws.client_state is WebSocketState.CONNECTED:
                await self.ws.close(code=code, reason=reason)
        except (RuntimeError, ConnectionError):
            pass  # đối phương đã đóng trước

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def pending(self) -> int:
        """Số tin còn nằm trong hàng đợi — soi được client nào đang chậm."""
        return self._queue.qsize()

    def __repr__(self) -> str:
        return f"<Socket {self.id} user={self.user_id} rooms={sorted(self.rooms)}>"
