"""Trạng thái nối/đứt của hạ tầng — `broker_status()` và `@mqtt_on_connect` cùng bạn bè.

    @injectable
    class TrangThaiMqtt:
        @mqtt_on_connect
        async def online(self, info: dict) -> None: ...

        @mqtt_on_disconnect
        async def offline(self, info: dict) -> None: ...

    broker_status("mqtt")["connected"]      # đọc RAM, gọi liên tục cũng được

File này KHÔNG biết hạ tầng nào cả, giống `core/rpc.py`: mỗi package trong
`infrastructure/` tự sinh cặp decorator của nó bằng `connection_decorator(...)`
và tự báo trạng thái qua một `ConnectionEvents`. Nhờ vậy các hạ tầng vẫn không
biết nhau, và luật "khi nào thì coi là đổi trạng thái" chỉ nằm ở MỘT chỗ.

Luật đó, và vì sao:

- **Chỉ báo khi trạng thái ĐỔI.** Vòng nối lại thử hỏng mười lần liền không
  phải mười lần mất kết nối. Hạ tầng cứ gọi `mark_*` thoải mái ở mọi chỗ nó
  thấy dấu hiệu; lọc trùng là việc của lớp này.
- **Chưa từng nối được thì không có "mất kết nối".** Broker chưa lên lúc khởi
  động đã có log `*.starting_degraded`; gọi `on_disconnect` ở đó sẽ khiến
  handler "đánh dấu offline" chạy trước cả handler "đánh dấu online".
- **Tắt app không phải mất kết nối.** `close()` đặt lại trạng thái mà không báo.
- **Handler chạy trong MỘT task riêng, lần lượt.** Gọi thẳng trong vòng nối lại
  thì handler chậm làm nghẽn việc đọc tin; bắn mỗi sự kiện một task thì thứ tự
  nối → đứt có thể đảo, và handler "offline" chạy SAU handler "online".
- **`broker_status()` chỉ đọc trường có sẵn.** Nó bị gọi trong vòng lặp nóng
  (mỗi khung hình camera, mỗi request), nên không được ping, không được await.
  Độ tươi của trạng thái là việc của hạ tầng: MQTT/RabbitMQ biết đứt ngay từ
  thư viện, Redis/Kafka nhờ vòng kiểm tra định kỳ của chính chúng.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.compat import TimeoutErrors
from fastapi_modular.core.container import _REGISTRY, container, request_scope
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)

ConnectionEvent = Literal["connect", "disconnect"]

#: Lúc tắt app, chờ các handler còn trong hàng đợi chạy xong tối đa ngần này
#: giây. Handler treo thì không được giữ tiến trình lại mãi.
CLOSE_TIMEOUT_SECONDS = 5.0

#: Hạ tầng đang chạy trong tiến trình này — `start()` ghi vào, `close()` xoá đi.
_ACTIVE: dict[str, ConnectionEvents] = {}

#: Mọi tên hạ tầng đã sinh decorator, để `broker_status("mqqt")` báo gõ nhầm
#: thay vì lặng lẽ trả "đang tắt".
_KNOWN_KINDS: set[str] = set()


def _attr(kind: str, event: ConnectionEvent) -> str:
    return f"__{kind}_on_{event}__"


def connection_decorator(kind: str, event: ConnectionEvent) -> Callable[..., Any]:
    """Sinh decorator `@<kind>_on_<event>` cho một hạ tầng.

    Viết được cả `@mqtt_on_connect` lẫn `@mqtt_on_connect()`: quên hay thừa cặp
    ngoặc là lỗi gõ rất hay gặp, và nó không đáng để app chết lúc import.
    """
    _KNOWN_KINDS.add(kind)
    name = f"{kind}_on_{event}"

    def decorator(fn: Callable | None = None) -> Any:
        if fn is None:
            return decorator
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"@{name}: {fn.__name__} phải là `async def`")
        setattr(fn, _attr(kind, event), True)
        return fn

    decorator.__name__ = decorator.__qualname__ = name
    decorator.__doc__ = (
        f"Gọi method này mỗi khi {kind} "
        + ("nối được (lần đầu và mỗi lần nối lại)." if event == "connect" else "mất kết nối.")
        + "\n\nChữ ký: `async def f(self)` hoặc `async def f(self, info: dict)`."
        " Class phải mang `@injectable`."
    )
    return decorator


@dataclass(slots=True)
class ConnectionHook:
    cls: type
    fn: Callable
    wants_info: bool

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}"


def discover_connection_hooks(kind: str, event: ConnectionEvent) -> list[ConnectionHook]:
    """Quét mọi class đã đăng ký để tìm method mang `@<kind>_on_<event>`."""
    attr = _attr(kind, event)
    found: list[ConnectionHook] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            if not getattr(fn, attr, False):
                continue
            params = list(inspect.signature(fn).parameters.values())[1:]
            if len(params) > 1:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký của @{kind}_on_{event} phải là "
                    "(self) hoặc (self, info)"
                )
            found.append(ConnectionHook(cls=cls, fn=fn, wants_info=bool(params)))
    return sorted(found, key=lambda h: h.label)


def _disabled_status() -> dict[str, Any]:
    return {
        "enabled": False,
        "connected": False,
        "since": None,
        "last_error": None,
        "disconnects": 0,
    }


def broker_status(name: str | None = None) -> dict[str, Any]:
    """Trạng thái kết nối lúc này, đọc từ RAM — không gửi gì qua mạng, không await.

        broker_status()          {"mqtt": {...}, "redis": {...}}  — chỉ hạ tầng đang bật
        broker_status("mqtt")    {"enabled", "connected", "since", "last_error",
                                  "disconnects", + "url"/"servers"/"client_id"}

    Hỏi một tên đang tắt thì vẫn trả dict (`enabled=False`, `connected=False`)
    để `broker_status("mqtt")["connected"]` không nổ `TypeError`. Tên không có
    thật thì ném `ValueError` — gõ nhầm "mqqt" mà nhận về "đang tắt" là thứ ngồi
    dò cả buổi.

    Dict trả về là BẢN SAO: sửa nó không đụng tới trạng thái thật.
    """
    if name is None:
        return {kind: events.snapshot() for kind, events in sorted(_ACTIVE.items())}
    if name not in _KNOWN_KINDS:
        raise ValueError(
            f"broker_status: không có hạ tầng {name!r}. "
            f"Chọn một trong: {', '.join(sorted(_KNOWN_KINDS)) or '(chưa nạp hạ tầng nào)'}."
        )
    events = _ACTIVE.get(name)
    return events.snapshot() if events is not None else _disabled_status()


class ConnectionEvents:
    """Theo dõi trạng thái nối/đứt của MỘT hạ tầng và gọi handler khi nó đổi."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._on_connect: list[ConnectionHook] = []
        self._on_disconnect: list[ConnectionHook] = []
        self._connected = False
        self._ever_connected = False
        self._lost_at: float | None = None
        self._since: datetime | None = None
        self._last_error: str | None = None
        self._disconnects = 0
        self._target: dict[str, Any] = {}
        self._queue: asyncio.Queue[tuple[list[ConnectionHook], dict[str, Any]]] | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self, **target: Any) -> None:
        """Gọi trong `startup()` của hạ tầng, SAU chốt `enabled`.

        Hai việc: quét handler (lúc này mọi module đã nạp xong), và ghi mình vào
        sổ của `broker_status()`. Ghi ở đây chứ không ở `__init__`: container và
        test dựng client mà không bật nó, và một client chưa chạy không được
        phép đè lên trạng thái của client đang chạy.

        `target` (url, servers...) nhận ngay từ đây để `broker_status()` nói được
        đang nhắm tới đâu cả khi CHƯA từng nối được — đúng lúc người ta cần nó nhất.
        """
        self._on_connect = discover_connection_hooks(self._kind, "connect")
        self._on_disconnect = discover_connection_hooks(self._kind, "disconnect")
        self._target = dict(target)
        _ACTIVE[self._kind] = self
        if self.has_hooks:
            log.info(
                f"{self._kind}.connection_hooks_registered",
                on_connect=[h.label for h in self._on_connect],
                on_disconnect=[h.label for h in self._on_disconnect],
            )

    @property
    def has_hooks(self) -> bool:
        return bool(self._on_connect or self._on_disconnect)

    @property
    def connected(self) -> bool:
        return self._connected

    def snapshot(self) -> dict[str, Any]:
        """Bản sao trạng thái, cho `broker_status()`. Chỉ đọc trường có sẵn."""
        return {
            **self._target,
            "enabled": True,
            "connected": self._connected,
            "since": self._since,
            "last_error": self._last_error,
            "disconnects": self._disconnects,
        }

    def mark_connected(self, **info: Any) -> None:
        self._target.update(info)
        if self._connected:
            return
        self._connected = True
        self._since = utcnow()
        downtime = None if self._lost_at is None else round(time.monotonic() - self._lost_at, 3)
        payload = {**info, "reconnect": self._ever_connected, "downtime_seconds": downtime}
        self._ever_connected = True
        self._lost_at = None
        self._enqueue(self._on_connect, payload)

    def mark_disconnected(self, error: BaseException | str | None = None, **info: Any) -> None:
        self._target.update(info)
        if not self._connected:
            return
        self._connected = False
        self._since = utcnow()
        self._lost_at = time.monotonic()
        self._disconnects += 1
        if isinstance(error, BaseException):
            # `asyncio.TimeoutError` không mang thông điệp: in "TimeoutError: "
            # với dấu hai chấm treo trông như log bị cắt cụt.
            error = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
        self._last_error = error
        self._enqueue(self._on_disconnect, {**info, "error": error})

    def _enqueue(self, hooks: list[ConnectionHook], info: dict[str, Any]) -> None:
        if not hooks:
            return
        if self._queue is None:
            self._queue = asyncio.Queue()
        self._queue.put_nowait((hooks, info))
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._drain(), name=f"{self._kind}-connection-hooks"
            )

    async def _drain(self) -> None:
        queue = self._queue
        assert queue is not None
        while True:
            hooks, info = await queue.get()
            try:
                for hook in hooks:
                    await self._run(hook, info)
            finally:
                queue.task_done()

    async def _run(self, hook: ConnectionHook, info: dict[str, Any]) -> None:
        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                instance = container.resolve(hook.cls)
                if hook.wants_info:
                    await hook.fn(instance, dict(info))
                else:
                    await hook.fn(instance)
        except Exception as exc:
            # Một handler hỏng không được chặn các handler sau, và càng không
            # được làm chết task — chết là mọi sự kiện về sau im luôn.
            log.exception(
                f"{self._kind}.connection_hook_failed",
                handler=hook.label,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            reset_request_id(token)

    async def close(self) -> None:
        """Lúc tắt app: cho handler đang xếp hàng chạy nốt, rồi dọn. Không báo đứt."""
        if _ACTIVE.get(self._kind) is self:
            del _ACTIVE[self._kind]
        self._connected = False
        self._ever_connected = False
        self._lost_at = None
        self._since = None
        self._last_error = None
        self._disconnects = 0
        self._target = {}
        if self._task is not None:
            if self._queue is not None:
                with contextlib.suppress(*TimeoutErrors):
                    await asyncio.wait_for(self._queue.join(), CLOSE_TIMEOUT_SECONDS)
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._queue = None
