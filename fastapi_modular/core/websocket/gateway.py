"""Gateway — tương đương @WebSocketGateway + @SubscribeMessage của NestJS.

    @gateway(path="/ws/chat", guards=[WsIdentity], client_rooms=True)
    class ChatGateway:
        def __init__(self, service: ChatService) -> None:
            self._service = service

        async def on_connect(self, socket: Socket) -> None: ...
        async def on_disconnect(self, socket: Socket, code: int) -> None: ...

        @subscribe("message.send")
        async def send(self, socket: Socket, payload: MessageIn) -> dict:
            ...
            return {"ok": True}      # giá trị trả về được gửi lại làm ack

Không phải khai báo ở đâu khác: `app/app.py` quét thư mục và tự gắn.

Mọi thứ khác trong khung — DI, guard, Principal, request scope, DTO pydantic,
cây exception — dùng lại y nguyên của phía HTTP. Một service viết cho REST
gọi được từ gateway mà không sửa gì.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, get_type_hints

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect

from fastapi_modular.core.compat import TimeoutErrors
from fastapi_modular.core.config import Settings, WebSocketSettings, get_settings
from fastapi_modular.core.container import container, injectable, request_scope
from fastapi_modular.core.context import (
    new_request_id,
    reset_request_id,
    reset_user_id,
    set_request_id,
    set_user_id,
)
from fastapi_modular.core.exceptions import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotImplementedYetError,
    TooManyRequestsError,
)
from fastapi_modular.core.guards import Principal
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.metrics import ws_connections, ws_connections_total, ws_messages_in
from fastapi_modular.core.websocket.protocol import (
    EVENT_CONNECTED,
    EVENT_PING,
    EVENT_PONG,
    EVENT_ROOM_JOIN,
    EVENT_ROOM_LEAVE,
    RESERVED_EVENTS,
    CloseCode,
    Frame,
    ProtocolError,
    close_code_for,
    error_frame,
    parse_frame,
)
from fastapi_modular.core.websocket.server import WebSocketServer
from fastapi_modular.core.websocket.socket import Socket

log = get_logger(__name__)

T = TypeVar("T")

_GATEWAY_ATTR = "__gateway_meta__"
_EVENT_ATTR = "__ws_event__"

# Sổ gateway theo thứ tự khai báo (= thứ tự import), giống _CONTROLLERS.
_GATEWAYS: list[type] = []

MAX_ROOM_NAME = 128


@dataclass(slots=True)
class GatewayMeta:
    path: str
    guards: tuple[type, ...]
    client_rooms: bool
    name: str


def gateway(
    *,
    path: str,
    guards: Sequence[type] = (),
    client_rooms: bool = False,
    name: str | None = None,
) -> Callable[[type[T]], type[T]]:
    """Đánh dấu class là gateway WebSocket và đăng ký làm provider.

    - `path`         : đường dẫn kết nối, ví dụ "/ws/chat". KHÔNG nằm dưới
                       tiền tố /api — WebSocket không phải REST.
    - `guards`       : chạy MỘT LẦN lúc bắt tay. Từ chối thì đóng kết nối kèm
                       mã lý do (4401/4403...). Dùng chung lớp Guard với HTTP.
    - `client_rooms` : cho phép client tự gửi `room.join` / `room.leave`.
                       Mặc định TẮT, và đây là chủ ý: mở ra nghĩa là ai cũng
                       vào được phòng bất kỳ, kể cả phòng riêng của người khác.
                       Bật thì nên kèm hook `can_join()` để kiểm tra quyền.
    """

    def decorate(cls: type[T]) -> type[T]:
        if not path.startswith("/"):
            raise RuntimeError(f"{cls.__name__}: path phải bắt đầu bằng '/', đang là {path!r}")
        setattr(
            cls,
            _GATEWAY_ATTR,
            GatewayMeta(
                path=path,
                guards=tuple(guards),
                client_rooms=client_rooms,
                name=name or cls.__name__,
            ),
        )
        _GATEWAYS.append(cls)
        return injectable(cls)

    return decorate


def subscribe(event: str) -> Callable[[Callable], Callable]:
    """Gắn method vào một tên sự kiện (tương đương @SubscribeMessage)."""
    if event in RESERVED_EVENTS:
        raise RuntimeError(
            f"'{event}' là sự kiện của framework, không đăng ký đè được. "
            "Với room.join/room.leave, dùng client_rooms=True và hook can_join()."
        )

    def decorate(fn: Callable) -> Callable:
        setattr(fn, _EVENT_ATTR, event)
        return fn

    return decorate


def gateways_in(package: str) -> list[type]:
    return [
        cls
        for cls in _GATEWAYS
        if cls.__module__ == package or cls.__module__.startswith(f"{package}.")
    ]


# --------------------------------------------------------------------- handler
@dataclass(slots=True)
class _Handler:
    event: str
    fn: Callable
    model: type[BaseModel] | None
    wants_payload: bool


def _collect_handlers(cls: type) -> dict[str, _Handler]:
    """Đọc chữ ký các method @subscribe MỘT LẦN lúc dựng router.

    Làm ở đây chứ không phải mỗi lần có tin: inspect + get_type_hints là việc
    đắt, còn tin nhắn thì có thể tới hàng nghìn lần một giây.

    Duyệt ngược MRO nên handler KẾ THỪA cũng được nhận, và lớp con ghi đè được
    lớp cha. Nhờ vậy một bộ sự kiện dùng chung đóng gói được thành lớp trộn
    (mixin) ở ngoài lõi — đó là cách lớp nhắn tin gắn `event.subscribe` vào mà
    không phải sửa một dòng nào trong file này.
    """
    handlers: dict[str, _Handler] = {}
    owners: dict[str, type] = {}

    for klass, fn in _own_and_inherited(cls):
        event = getattr(fn, _EVENT_ATTR, None)
        if event is None:
            continue
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{klass.__name__}.{fn.__name__} phải là `async def`")

        params = list(inspect.signature(fn).parameters.values())[1:]  # bỏ self
        if not params:
            raise RuntimeError(
                f"{cls.__name__}.{fn.__name__} thiếu tham số socket. "
                f"Chữ ký đúng: async def {fn.__name__}(self, socket: Socket, payload: ...)"
            )
        if len(params) > 2:
            raise RuntimeError(
                f"{cls.__name__}.{fn.__name__} có quá nhiều tham số. "
                "Gateway chỉ nhận (self, socket) hoặc (self, socket, payload)."
            )

        model: type[BaseModel] | None = None
        wants_payload = len(params) == 2
        if wants_payload:
            hints = get_type_hints(fn)
            annotation = hints.get(params[1].name)
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                model = annotation

        # Cùng một class mà hai method cùng đăng ký một sự kiện là lỗi gõ nhầm.
        # Còn lớp con đăng ký đè sự kiện của lớp cha là CHỦ Ý — đó là cách ghi
        # đè hành vi mặc định của một mixin.
        existing = owners.get(event)
        if existing is klass:
            raise RuntimeError(f"{klass.__name__} đăng ký trùng sự kiện '{event}'")
        owners[event] = klass
        handlers[event] = _Handler(event, fn, model, wants_payload)

    return handlers


def _own_and_inherited(cls: type) -> list[tuple[type, Callable]]:
    """Method mang @subscribe của class và của mọi lớp cha — cha trước, con sau.

    Thứ tự đó khiến lớp con ghi đè lớp cha một cách tự nhiên.
    """
    found: list[tuple[type, Callable]] = []
    for klass in reversed(cls.__mro__):
        found.extend(
            (klass, fn) for fn in vars(klass).values() if hasattr(fn, _EVENT_ATTR)
        )
    return found


def _optional_hook(cls: type, name: str) -> Callable | None:
    """Lấy hook tuỳ chọn của gateway (on_connect, on_disconnect, can_*).

    Hook `can_*` trả lời có/không, thường chỉ so vài chuỗi nên viết `def`
    thường cũng được. Hook vòng đời thì bắt buộc `async def` vì gần như chắc
    chắn sẽ có I/O trong đó.
    """
    hook = getattr(cls, name, None)
    if hook is None:
        return None
    if not inspect.iscoroutinefunction(hook) and not name.startswith("can_"):
        raise RuntimeError(f"{cls.__name__}.{name} phải là `async def`")
    return hook


# ------------------------------------------------------------------ kết nối
class _Connection:
    """Vòng đời của MỘT kết nối. Mỗi client được một instance riêng."""

    def __init__(
        self,
        cls: type,
        meta: GatewayMeta,
        handlers: dict[str, _Handler],
        ws: WebSocket,
        config: WebSocketSettings,
        *,
        debug: bool,
    ) -> None:
        self.cls = cls
        self.meta = meta
        self.handlers = handlers
        self.ws = ws
        self.config = config
        self.debug = debug
        self.server: WebSocketServer = container.resolve(WebSocketServer)
        self.namespace = self.server.namespace(meta.path)
        self.socket: Socket | None = None
        self._tokens = float(config.burst_messages)
        self._refilled = time.monotonic()

    # ------------------------------------------------------------- vào cuộc
    async def run(self) -> None:
        if len(self.namespace) >= self.config.max_connections:
            # Chưa accept: từ chối ở tầng bắt tay, rẻ hơn nhiều so với nhận rồi đóng.
            await self.ws.close(CloseCode.TOO_MANY, "server đã đủ kết nối")
            log.warning("ws.rejected_full", namespace=self.meta.path)
            return

        # Accept TRƯỚC khi chạy guard, dù nghe ngược đời. Đóng khi chưa accept
        # thì trình duyệt chỉ thấy "handshake failed" và KHÔNG đọc được mã lý
        # do; accept rồi mới đóng thì client nhận đúng 4401/4403 và biết phải
        # xin token mới thay vì nối lại vô hạn.
        await self.ws.accept()

        identity = await self._authenticate()
        if identity is None:
            return

        user_id, roles = identity
        if not await self._within_user_quota(user_id):
            return

        self.socket = Socket(
            self.ws,
            self.namespace,
            user_id=user_id,
            roles=roles,
            queue_size=self.config.send_queue_size,
            overflow=self.config.overflow,
        )
        self.socket.start_writer()
        self.namespace.add(self.socket)
        ws_connections.inc_gauge(1, namespace=self.meta.path)
        ws_connections_total.inc(namespace=self.meta.path)

        code = CloseCode.NORMAL
        heartbeat: asyncio.Task[None] | None = None
        try:
            if not await self._call_on_connect():
                return
            self.socket.emit(
                EVENT_CONNECTED,
                {
                    "socket_id": self.socket.id,
                    "user_id": user_id,
                    "namespace": self.meta.path,
                    "heartbeat_seconds": self.config.heartbeat_seconds,
                },
            )
            heartbeat = asyncio.create_task(self._heartbeat(), name=f"ws-hb-{self.socket.id}")
            code = await self._receive_loop()
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            await self._teardown(code)

    async def _authenticate(self) -> tuple[str | None, frozenset[str]] | None:
        """Chạy guard trong một request scope, lấy danh tính ra khỏi Principal.

        Danh tính được xác lập ĐÚNG MỘT LẦN lúc bắt tay rồi lưu trên Socket.
        Chạy lại guard cho từng tin sẽ đắt vô ích, mà cũng vô nghĩa: kết nối
        đã mở thì bên kia vẫn là bên kia.
        """
        if not self.meta.guards:
            return None, frozenset()

        try:
            async with request_scope():
                for guard_cls in self.meta.guards:
                    await container.resolve(guard_cls).check(self.ws)
                principal = container.resolve(Principal)
                return principal.id, principal.roles
        except AppError as exc:
            await self._reject(exc)
            return None
        except Exception as exc:
            # Guard lỗi bất ngờ vẫn phải đóng kết nối gọn ghẽ, không để treo.
            log.exception("ws.guard_failed", namespace=self.meta.path, error=str(exc))
            await self._reject(exc)
            return None

    async def _reject(self, exc: BaseException) -> None:
        """Gửi khung lỗi rồi mới đóng, để client biết vì sao."""
        with contextlib.suppress(RuntimeError, ConnectionError):
            await self.ws.send_text(error_frame(exc, debug=self.debug).to_json())
            await self.ws.close(close_code_for(exc), getattr(exc, "message", "")[:120])
        log.info(
            "ws.rejected",
            namespace=self.meta.path,
            reason=getattr(exc, "error_code", type(exc).__name__),
        )

    async def _within_user_quota(self, user_id: str | None) -> bool:
        limit = self.config.max_connections_per_user
        if not user_id or limit <= 0:
            return True
        if len(self.namespace.sockets_of(user_id)) < limit:
            return True
        await self._reject(
            TooManyRequestsError(f"Vượt quá {limit} kết nối đồng thời cho một tài khoản")
        )
        return False

    async def _call_on_connect(self) -> bool:
        hook = _optional_hook(self.cls, "on_connect")
        if hook is None:
            return True
        assert self.socket is not None
        try:
            async with self._message_scope():
                await hook(container.resolve(self.cls), self.socket)
        except AppError as exc:
            await self.socket.close(close_code_for(exc), exc.message[:120])
            return False
        except Exception as exc:
            # Lỗi trong on_connect không được lộ traceback ra client.
            log.exception("ws.on_connect_failed", namespace=self.meta.path, error=str(exc))
            await self.socket.close(CloseCode.INTERNAL, "lỗi khi mở kết nối")
            return False
        return True

    # -------------------------------------------------------------- vòng nhận
    async def _receive_loop(self) -> int:
        assert self.socket is not None
        idle = self.config.idle_timeout_seconds

        while True:
            try:
                message = await asyncio.wait_for(self.ws.receive(), timeout=idle)
            except TimeoutErrors:
                # Không nhận được gì kể cả pong: kết nối đã chết mà TCP chưa
                # biết (half-open). Không dọn thì socket "ma" tích lại mãi.
                log.info("ws.idle_timeout", socket_id=self.socket.id, seconds=idle)
                await self.socket.close(CloseCode.IDLE_TIMEOUT, "im lặng quá lâu")
                return CloseCode.IDLE_TIMEOUT
            except (WebSocketDisconnect, RuntimeError):
                return CloseCode.NORMAL

            if message["type"] == "websocket.disconnect":
                return int(message.get("code", CloseCode.NORMAL))

            raw = message.get("text")
            if raw is None:
                self.socket.send(
                    error_frame(ProtocolError("Chỉ nhận khung dạng text JSON"), debug=self.debug)
                )
                continue

            if len(raw) > self.config.max_message_bytes:
                self.socket.send(
                    error_frame(
                        ProtocolError(
                            f"Khung tin dài {len(raw)} byte, vượt ngưỡng "
                            f"{self.config.max_message_bytes}"
                        ),
                        debug=self.debug,
                    )
                )
                continue

            if not self._take_token():
                self.socket.send(
                    error_frame(
                        TooManyRequestsError(
                            f"Gửi quá nhanh (trần {self.config.max_messages_per_second}/giây)"
                        ),
                        debug=self.debug,
                    )
                )
                continue

            if self.socket.closing:
                return CloseCode.NORMAL

            await self._handle_raw(raw)

    def _take_token(self) -> bool:
        """Gáo token: cho phép bùng ngắn hạn, nhưng chặn client gửi tràn.

        Không có nó thì một client (hoặc một vòng lặp viết sai ở phía client)
        đủ sức làm nghẽn event loop của cả worker.
        """
        rate = self.config.max_messages_per_second
        if rate <= 0:
            return True
        now = time.monotonic()
        self._tokens = min(
            float(self.config.burst_messages), self._tokens + (now - self._refilled) * rate
        )
        self._refilled = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    async def _handle_raw(self, raw: str) -> None:
        assert self.socket is not None
        try:
            frame = parse_frame(raw)
        except ProtocolError as exc:
            self.socket.send(error_frame(exc, debug=self.debug))
            return

        ws_messages_in.inc(namespace=self.meta.path, event=frame.event)

        try:
            async with self._message_scope():
                await self._dispatch(frame)
        except AppError as exc:
            self.socket.send(error_frame(exc, ack=frame.id, debug=self.debug))
        except NotImplementedError as exc:
            # Khung do `make gateway` sinh ra raise NotImplementedError. Trả mã
            # riêng "chưa viết" thay vì internal_error, giống 501 bên HTTP.
            self.socket.send(
                error_frame(
                    NotImplementedYetError(str(exc) or None), ack=frame.id, debug=self.debug
                )
            )
        except Exception as exc:
            # Một tin lỗi không được giết cả kết nối: client vẫn dùng tiếp được.
            log.exception(
                "ws.handler_failed",
                namespace=self.meta.path,
                # KHÔNG đặt tên khoá là `event`: structlog đã dùng tên đó cho
                # chính dòng log, truyền vào sẽ ném TypeError ngay trong tay
                # xử lý lỗi — che mất lỗi thật.
                ws_event=frame.event,
                socket_id=self.socket.id,
                error=str(exc),
            )
            self.socket.send(error_frame(exc, ack=frame.id, debug=self.debug))

    def _message_scope(self):
        """Mỗi tin nhắn là một "request" thu nhỏ: có request_id riêng, có
        request scope riêng, và Principal đã được điền sẵn.

        Nhờ vậy service dùng provider request-scoped (transaction database)
        chạy y hệt như khi được gọi từ HTTP, và mỗi tin commit gọn một lần.
        """
        socket = self.socket
        assert socket is not None

        class _Scope:
            async def __aenter__(_self) -> None:
                _self._request_token = set_request_id(new_request_id())
                _self._user_token = set_user_id(socket.user_id)
                _self._scope = request_scope()
                await _self._scope.__aenter__()
                if socket.user_id:
                    container.resolve(Principal).assume(id=socket.user_id, roles=socket.roles)

            async def __aexit__(_self, *exc: Any) -> bool:
                try:
                    return await _self._scope.__aexit__(*exc)
                finally:
                    reset_user_id(_self._user_token)
                    reset_request_id(_self._request_token)

        return _Scope()

    # ------------------------------------------------------------ điều phối
    async def _dispatch(self, frame: Frame) -> None:
        assert self.socket is not None

        handler = self.handlers.get(frame.event)
        if handler is not None:
            result = await self._invoke(handler, frame)
            # Trả về giá trị khác None và client có gửi `id` thì tự động gửi
            # ack — không phải viết emit thủ công trong từng handler.
            if result is not None and frame.id is not None:
                self.socket.send(Frame(event=frame.event, data=result, ack=frame.id))
            return

        if frame.event == EVENT_PING:
            self.socket.send(Frame(event=EVENT_PONG, data=frame.data, ack=frame.id))
            return

        if frame.event == EVENT_PONG:
            # Client trả lời ping của server. Không cần làm gì: chỉ riêng việc
            # nhận được khung này đã làm mới đồng hồ idle. Phải bắt ở đây, nếu
            # không client làm đúng lại nhận về lỗi "unknown_event".
            return

        if frame.event in (EVENT_ROOM_JOIN, EVENT_ROOM_LEAVE):
            await self._handle_room(frame)
            return

        known = [*self.handlers, EVENT_PING]
        if self.meta.client_rooms:
            known += [EVENT_ROOM_JOIN, EVENT_ROOM_LEAVE]
        raise BadRequestError(
            f"Không có handler cho sự kiện '{frame.event}'",
            error_code="unknown_event",
            details={"known": sorted(known)},
        )

    async def _invoke(self, handler: _Handler, frame: Frame) -> Any:
        assert self.socket is not None
        instance = container.resolve(self.cls)

        if not handler.wants_payload:
            return await handler.fn(instance, self.socket)

        payload: Any = frame.data
        if handler.model is not None:
            try:
                payload = handler.model.model_validate(frame.data or {})
            except ValidationError as exc:
                # Cùng hình dạng lỗi validate với phía HTTP, để client dùng
                # chung một nhánh xử lý.
                raise BadRequestError(
                    "Dữ liệu không hợp lệ",
                    error_code="validation_error",
                    details=[
                        {
                            "field": ".".join(str(p) for p in err["loc"]),
                            "message": err["msg"],
                            "type": err["type"],
                        }
                        for err in exc.errors()
                    ],
                ) from exc
        return await handler.fn(instance, self.socket, payload)

    async def _handle_room(self, frame: Frame) -> None:
        assert self.socket is not None
        if not self.meta.client_rooms:
            raise ForbiddenError(
                "Gateway này không cho client tự vào phòng. Bật bằng "
                "@gateway(..., client_rooms=True) hoặc viết handler riêng."
            )

        room = (frame.data or {}).get("room") if isinstance(frame.data, dict) else None
        if not isinstance(room, str) or not room.strip():
            raise BadRequestError("Thiếu 'room' trong data")
        room = room.strip()
        if len(room) > MAX_ROOM_NAME:
            raise BadRequestError(f"Tên phòng dài quá {MAX_ROOM_NAME} ký tự")

        if frame.event == EVENT_ROOM_LEAVE:
            self.socket.leave(room)
            self.socket.send(
                Frame(EVENT_ROOM_LEAVE, {"room": room, "rooms": sorted(self.socket.rooms)}, ack=frame.id)
            )
            return

        if len(self.socket.rooms) >= self.config.max_rooms_per_socket:
            raise TooManyRequestsError(
                f"Một kết nối chỉ vào tối đa {self.config.max_rooms_per_socket} phòng"
            )

        can_join = _optional_hook(self.cls, "can_join")
        if can_join is not None:
            allowed = can_join(container.resolve(self.cls), self.socket, room)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise ForbiddenError(f"Không được vào phòng '{room}'")

        self.socket.join(room)
        log.debug("ws.room_joined", socket_id=self.socket.id, room=room)
        self.socket.send(
            Frame(
                EVENT_ROOM_JOIN,
                {"room": room, "size": self.namespace.room_size(room), "rooms": sorted(self.socket.rooms)},
                ack=frame.id,
            )
        )

    # ------------------------------------------------------------------ dọn
    async def _teardown(self, code: int) -> None:
        socket = self.socket
        if socket is None:
            return

        hook = _optional_hook(self.cls, "on_disconnect")
        if hook is not None:
            try:
                async with self._message_scope():
                    if len(inspect.signature(hook).parameters) >= 3:
                        await hook(container.resolve(self.cls), socket, code)
                    else:
                        await hook(container.resolve(self.cls), socket)
            except Exception as exc:
                # Hook lỗi cũng không được cản việc gỡ socket khỏi sổ bên dưới.
                log.exception("ws.on_disconnect_failed", socket_id=socket.id, error=str(exc))

        # Chạy phần dọn dẹp mà các lớp NGOÀI đã gắn vào kết nối này (ví dụ lớp
        # nhắn tin gỡ đăng ký sự kiện). Lõi không biết đó là những việc gì.
        await socket.run_close_hooks()

        self.namespace.remove(socket)
        await socket.stop_writer()
        with contextlib.suppress(RuntimeError, ConnectionError):
            await socket.close(code if code >= 4000 else CloseCode.NORMAL)
        ws_connections.inc_gauge(-1, namespace=self.meta.path)

        log.info(
            "ws.disconnected",
            namespace=self.meta.path,
            socket_id=socket.id,
            user_id=socket.user_id,
            code=code,
            seconds=round(time.time() - socket.connected_at, 1),
        )
        self.socket = None

    async def _heartbeat(self) -> None:
        """Đều đặn đẩy `ping` để (a) giữ kết nối qua proxy hay cắt phiên nhàn
        rỗi, (b) buộc client trả lời, nhờ đó phát hiện được client đã chết."""
        assert self.socket is not None
        interval = self.config.heartbeat_seconds
        if interval <= 0:
            return
        while not self.socket.closing:
            await asyncio.sleep(interval)
            if not self.socket.emit(EVENT_PING, {"t": round(time.time(), 3)}):
                return


# ------------------------------------------------------------------- dựng route
def _ws_config() -> tuple[WebSocketSettings, bool]:
    try:
        settings = container.resolve(Settings)
    except RuntimeError:
        settings = get_settings()
    return settings.ws, settings.debug


def build_ws_router(*gateways: type) -> APIRouter:
    """Dựng APIRouter chứa route WebSocket của các gateway đã cho."""
    router = APIRouter()

    for cls in gateways:
        meta: GatewayMeta | None = getattr(cls, _GATEWAY_ATTR, None)
        if meta is None:
            raise RuntimeError(f"{cls.__name__} thiếu @gateway(...)")

        handlers = _collect_handlers(cls)
        if not handlers and _optional_hook(cls, "on_connect") is None:
            log.warning(
                "gateway.no_handlers",
                gateway=cls.__name__,
                hint="thiếu @subscribe trên method?",
            )

        def endpoint_factory(cls: type = cls, meta: GatewayMeta = meta, handlers: dict[str, _Handler] = handlers):
            async def endpoint(websocket: WebSocket) -> None:
                config, debug = _ws_config()
                await _Connection(cls, meta, handlers, websocket, config, debug=debug).run()

            return endpoint

        router.add_api_websocket_route(meta.path, endpoint_factory(), name=meta.name)
        log.debug(
            "gateway.registered",
            gateway=cls.__name__,
            path=meta.path,
            events=sorted(handlers),
        )

    return router
