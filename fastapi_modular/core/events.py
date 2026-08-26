"""`@on_event` + `EventBus` — MỘT việc xong, BÁO cho nhiều nơi cùng lúc.

Chỗ trống mà `@job` không lấp được. `@job` là **một tên việc — một handler —
chạy tuần tự**: gửi vào hàng đợi rồi chờ tới lượt. Còn đây là **fanout**: một
sự kiện, N nơi nghe, chạy song song, ai xong trước thì xong.

    @injectable
    class OrderService:
        def __init__(self, events: EventBus) -> None:
            self._events = events

        async def pay(self, order_id: str) -> None:
            await self._repo.mark_paid(order_id)
            await self._events.emit("order.paid", {"id": order_id})

    @injectable
    class MailService:
        @on_event("order.paid")
        async def send_receipt(self, data: dict) -> None: ...

    @injectable
    class StatsService:
        @on_event("order.*")                  # nghe mọi sự kiện của đơn hàng
        async def count(self, data: dict) -> None: ...

`OrderService` **không biết** hai bên kia tồn tại. Thêm một nơi nghe là thêm một
method mang `@on_event`, không phải sửa chỗ phát. Đó là toàn bộ giá trị của
kiểu này, và cũng là lý do đừng dùng nó khi bên phát CẦN biết kết quả.

## So với những thứ đã có

| Cần gì | Dùng |
|---|---|
| một việc, chạy tuần tự, có hàng đợi | `@job` |
| một sự kiện, nhiều nơi nghe, song song | **`@on_event`** |
| vòng lặp sống mãi | `@worker` |
| tới giờ thì chạy | `@interval` / `@cron` |
| fanout ra tiến trình KHÁC, qua mạng | RabbitMQ `fanout` / Redis / MQTT |

Dòng cuối là ranh giới quan trọng nhất: cái này **chỉ trong một tiến trình**.
`fam run --workers 4` là bốn tiến trình, và sự kiện phát ở tiến trình 1 KHÔNG
tới tiến trình 2. Cần xuyên tiến trình thì đó là việc của broker.

## Hai cách phát, chọn đúng cái

    await bus.emit("order.paid", data)     # CHỜ mọi handler xong, trả về số handler thành công
    bus.dispatch("order.paid", data)       # trả về NGAY, handler chạy nền

`emit` dùng khi bên phát cần biết mọi thứ đã xử lý xong — ví dụ trước khi trả
lời HTTP. `dispatch` dùng khi không cần, và đây mới là cái hay dùng: một
request không nên chậm đi chỉ vì có thêm người đăng ký nghe.

## Một handler hỏng thì sao

Ghi log rồi thôi; **những handler khác vẫn chạy**. Đó là điều bắt buộc ở fanout:
gửi mail hỏng mà kéo theo không cập nhật được thống kê là vô lý.

Không có thử lại. Cần thử lại, cần bền vững, cần chạy nốt sau khi app khởi động
lại — thì handler đó nên đẩy việc sang `@job` (mất được) hoặc RabbitMQ (không
mất được), chứ không phải chờ lớp này lo.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import _REGISTRY, container, injectable, request_scope
from fastapi_modular.core.context import (
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.metrics import Counter, Gauge, Histogram, registry
from fastapi_modular.core.workers import (
    WorkerContext,
    call_handler,
    check_thread_mode,
    context_param_of,
    warn_if_endless,
)

log = get_logger(__name__)

_SPEC_ATTR = "__on_event__"
_SEPARATOR = "."

events_emitted = registry.register(Counter("events_emitted_total", "Số sự kiện đã phát"))
events_handled = registry.register(
    Counter("event_handlers_done_total", "Số lượt handler chạy xong")
)
events_failed = registry.register(
    Counter("event_handlers_failed_total", "Số lượt handler ném lỗi")
)
events_pending = registry.register(
    Gauge("event_handlers_pending", "Số lượt handler nền đang chạy")
)
events_duration = registry.register(
    Histogram("event_handler_duration_seconds", "Thời gian một lượt handler")
)


# ------------------------------------------------------------------ khớp mẫu
def validate_event(name: str) -> str:
    """Tên sự kiện dùng để PHÁT — không được chứa ký tự đại diện."""
    name = name.strip()
    if not name:
        raise BadRequestError("Tên sự kiện không được để trống")
    if "*" in name or "#" in name:
        raise BadRequestError(
            f"Tên sự kiện {name!r} chứa ký tự đại diện — chúng chỉ dùng được khi "
            "ĐĂNG KÝ NGHE (`@on_event`), không dùng để phát."
        )
    return name


def validate_pattern(pattern: str) -> str:
    pattern = pattern.strip()
    if not pattern:
        raise BadRequestError("Mẫu của `@on_event` không được để trống")
    parts = pattern.split(_SEPARATOR)
    for i, part in enumerate(parts):
        if part == "#" and i != len(parts) - 1:
            raise BadRequestError(
                f"Mẫu {pattern!r}: `#` nuốt mọi tầng còn lại nên phải nằm CUỐI. "
                f"Ý bạn là {_SEPARATOR.join([*parts[:i], '*', *parts[i + 1 :]])!r}?"
            )
        if not part:
            raise BadRequestError(f"Mẫu {pattern!r} có tầng rỗng — hai dấu chấm liền nhau")
    return pattern


def matches(pattern: str, event: str) -> bool:
    """Sự kiện có khớp mẫu không. `*` = ĐÚNG một tầng, `#` = mọi tầng còn lại.

        "order.*"        khớp order.paid, KHÔNG khớp order.item.added
        "order.#"        khớp cả hai
        "camera.*.motion" khớp camera.12.motion
    """
    wanted = pattern.split(_SEPARATOR)
    actual = event.split(_SEPARATOR)
    for i, level in enumerate(wanted):
        if level == "#":
            return True
        if i >= len(actual):
            return False
        if level != "*" and level != actual[i]:
            return False
    return len(wanted) == len(actual)


# ------------------------------------------------------------------ khai báo
@dataclass(slots=True)
class EventSpec:
    pattern: str
    thread: bool = False
    max_seconds: float = 0.0
    context_param: str = ""
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_data: bool = False

    @property
    def label(self) -> str:
        if self.cls and self.fn:
            return f"{self.cls.__name__}.{self.fn.__name__}"
        return self.pattern


def on_event(
    pattern: str, *, thread: bool = False, max_seconds: float = 0.0
) -> Callable[[Callable], Callable]:
    """Đăng ký nghe một sự kiện trong tiến trình.

        pattern      tên sự kiện, hoặc mẫu có `*` (một tầng) / `#` (mọi tầng
                     còn lại): "order.paid", "order.*", "camera.#".
        thread       True = chạy handler trong thread riêng; hàm khai bằng
                     `def` thường và gọi async qua `ctx.run(...)`.
        max_seconds  huỷ lượt chạy nếu quá hạn. 0 = không giới hạn.

    **Nhiều handler cùng nghe một sự kiện là chuyện BÌNH THƯỜNG** — khác hẳn
    `@job`, nơi trùng tên là lỗi. Đó chính là điểm của lớp này.

    Tham số đầu chú kiểu bằng model Pydantic thì dữ liệu được kiểm khuôn trước
    khi vào handler. Khai thêm `ctx: WorkerContext` nếu cần.

    Thứ tự chạy giữa các handler **không xác định** và chúng chạy song song.
    Cần A xong rồi mới tới B thì đó không phải fanout — viết thẳng hai lời gọi.
    """
    pattern = validate_pattern(pattern)
    if max_seconds < 0:
        raise BadRequestError(f"`max_seconds` không được âm (đang là {max_seconds})")

    def decorate(fn: Callable) -> Callable:
        check_thread_mode(fn, thread)
        warn_if_endless(fn, "on_event", pattern)
        setattr(
            fn,
            _SPEC_ATTR,
            EventSpec(
                pattern=pattern,
                thread=thread,
                max_seconds=max_seconds,
                context_param=context_param_of(fn),
            ),
        )
        return fn

    return decorate


def discover_listeners() -> list[EventSpec]:
    """Quét mọi class đã đăng ký để tìm method mang @on_event."""
    found: list[EventSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: EventSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is None:
                continue

            params = [
                p
                for p in list(inspect.signature(fn).parameters.values())[1:]
                if p.name != spec.context_param
            ]
            if len(params) > 1:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký phải là (self, data) hoặc "
                    "(self, data, ctx: WorkerContext), hoặc bỏ hẳn `data`"
                )
            model = None
            if params:
                annotation = get_type_hints(fn).get(params[0].name)
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    model = annotation
            found.append(
                replace(spec, cls=cls, fn=fn, model=model, wants_data=bool(params))
            )
    return found


# ------------------------------------------------------------------ đường ống
@dataclass(slots=True)
class _Runtime:
    """Một nơi nghe đã sẵn sàng chạy — từ decorator hoặc từ `subscribe()`."""

    pattern: str
    label: str
    call: Callable[[Any], Any]
    max_seconds: float = 0.0


@injectable
class EventBus:
    """Chỗ phát sự kiện, và sổ những nơi đang nghe.

    Tiêm vào bất cứ service nào cần báo cho nơi khác:

        def __init__(self, events: EventBus) -> None:
            self._events = events
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._config = settings.events
        self._listeners: list[_Runtime] = []
        self._dynamic: list[_Runtime] = []
        self._pending: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._started = False

    # ------------------------------------------------------------- vòng đời
    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._closing = False
        self._listeners = [self._to_runtime(spec) for spec in discover_listeners()]
        self._started = True
        if not self._listeners:
            return
        log.info(
            "events.started",
            listeners=len(self._listeners),
            events=sorted({item.pattern for item in self._listeners}),
        )

    def _to_runtime(self, spec: EventSpec) -> _Runtime:
        async def call(data: Any) -> None:
            payload = data
            if spec.model is not None:
                try:
                    payload = spec.model.model_validate(data)
                except ValidationError as exc:
                    raise BadRequestError(f"Dữ liệu sự kiện không hợp lệ: {exc}") from exc
            instance = container.resolve(spec.cls)          # type: ignore[arg-type]
            context = WorkerContext(
                spec.pattern, "", asyncio.get_running_loop(), thread_mode=spec.thread
            )
            args = (payload,) if spec.wants_data else ()
            await call_handler(
                spec.fn,                                     # type: ignore[arg-type]
                instance,
                *args,
                context=context,
                context_param=spec.context_param,
                thread=spec.thread,
                own_thread=True,
            )

        return _Runtime(
            pattern=spec.pattern,
            label=spec.label,
            call=call,
            max_seconds=spec.max_seconds or self._config.max_seconds,
        )

    async def shutdown(self) -> None:
        """Chờ handler nền chạy nốt, rồi nói rõ còn sót bao nhiêu."""
        self._closing = True
        pending = list(self._pending)
        if pending:
            _done, still = await asyncio.wait(pending, timeout=self._config.drain_seconds)
            if still:
                log.warning(
                    "events.dropped_on_shutdown",
                    count=len(still),
                    seconds=self._config.drain_seconds,
                )
                for task in still:
                    task.cancel()
        self._pending.clear()
        self._listeners.clear()
        self._dynamic.clear()
        self._started = False

    # ------------------------------------------------------------- đăng ký
    def subscribe(self, pattern: str, fn: Callable[[Any], Any], *, label: str = "") -> Callable[[], None]:
        """Đăng ký nghe LÚC ĐANG CHẠY, trả về hàm để huỷ đăng ký.

            bo_nghe = bus.subscribe("order.*", ghi_so)
            ...
            bo_nghe()

        `@on_event` đủ cho gần hết mọi trường hợp. Cái này dành cho những nơi
        người nghe chỉ tồn tại một lúc: một phiên WebSocket, một lần chờ trong
        test, một tính năng bật/tắt theo cấu hình.
        """
        item = _Runtime(
            pattern=validate_pattern(pattern),
            label=label or getattr(fn, "__qualname__", repr(fn)),
            call=fn,
            max_seconds=self._config.max_seconds,
        )
        self._dynamic.append(item)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._dynamic.remove(item)

        return unsubscribe

    def listeners(self, event: str = "") -> list[str]:
        """Ai đang nghe. Không truyền gì thì liệt kê tất cả.

        Hữu ích đúng lúc bạn cần nó nhất: "vì sao phát mà không thấy gì chạy".
        """
        items = [*self._listeners, *self._dynamic]
        if event:
            items = [item for item in items if matches(item.pattern, event)]
        return sorted(f"{item.pattern} -> {item.label}" for item in items)

    # --------------------------------------------------------------- phát
    async def emit(self, event: str, data: Any = None) -> int:
        """Phát, CHỜ mọi nơi nghe xong, trả về số handler chạy trót lọt.

        Handler chạy **song song**, không theo thứ tự. Một cái ném lỗi thì
        những cái khác vẫn chạy tới nơi — so số trả về với `len(listeners(event))`
        là biết có ai hỏng không.
        """
        targets = self._targets(event)
        if not targets:
            return 0
        if len(targets) == 1:
            # Đường tắt cho ca phổ biến nhất. `asyncio.gather` dựng một Future
            # và một task cho mỗi phần tử; với đúng một nơi nghe thì đó là chi
            # phí thuần tuý — đo được 66.000 -> 96.000 lượt/giây khi bỏ nó đi.
            return 1 if await self._run(targets[0], event, data) else 0
        results = await asyncio.gather(*(self._run(item, event, data) for item in targets))
        return sum(1 for ok in results if ok)

    def dispatch(self, event: str, data: Any = None) -> int:
        """Phát rồi trả về NGAY, không chờ. Trả về số handler sẽ chạy.

        Đây là cái hay dùng: một request không nên chậm đi chỉ vì có thêm người
        đăng ký nghe. Đổi lại, bên gọi không biết được kết quả — lỗi chỉ nằm
        trong log.
        """
        targets = self._targets(event)
        if not targets:
            return 0
        if len(self._pending) >= self._config.max_pending:
            # Đã có quá nhiều lượt nền đang chạy: nhận thêm chỉ làm bộ nhớ
            # phình. Nói ra bằng con số thay vì âm thầm xếp hàng.
            log.warning(
                "events.dispatch_dropped",
                name=event,
                pending=len(self._pending),
                max_pending=self._config.max_pending,
            )
            return 0

        request_id = get_request_id()
        for item in targets:
            task = asyncio.create_task(
                self._run_detached(item, event, data, request_id),
                name=f"event-{event}",
            )
            self._pending.add(task)
            events_pending.inc_gauge(1)
            task.add_done_callback(self._finish)
        return len(targets)

    def _finish(self, task: asyncio.Task[Any]) -> None:
        self._pending.discard(task)
        events_pending.inc_gauge(-1)

    # Ghi chú cho người sửa sau: KHÔNG truyền `event=` vào log. `event` là tên
    # dòng log của structlog (tham số đầu tiên), truyền thêm là TypeError ngay
    # lúc chạy. Dùng `name=`.
    def _targets(self, event: str) -> list[_Runtime]:
        event = validate_event(event)
        if self._closing:
            return []
        if not self._config.enabled:
            return []
        if not self._started:
            # Phát trước khi lifespan chạy xong: nói ra, vì im lặng ở đây trông
            # y hệt "không có ai nghe" và rất khó lần.
            log.warning("events.not_started", name=event)
            return []
        events_emitted.inc(event=event)
        targets = [
            item for item in (*self._listeners, *self._dynamic) if matches(item.pattern, event)
        ]
        if not targets:
            log.debug("events.no_listener", name=event)
        return targets

    async def _run_detached(self, item: _Runtime, event: str, data: Any, request_id: str | None) -> bool:
        # Giữ nguyên request-id của bên phát: handler nền là phần đuôi của
        # chính request đó, và log phải nối được về nhau.
        token = set_request_id(request_id or new_request_id())
        try:
            async with request_scope():
                return await self._run(item, event, data)
        finally:
            reset_request_id(token)

    async def _run(self, item: _Runtime, event: str, data: Any) -> bool:
        started = time.monotonic()
        try:
            call = item.call(data)
            if item.max_seconds:
                await asyncio.wait_for(call, item.max_seconds)
            else:
                await call
        except asyncio.CancelledError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            events_failed.inc(event=event)
            log.warning(
                "events.handler_timeout",
                name=event,
                listener=item.label,
                seconds=item.max_seconds,
            )
            return False
        except Exception as exc:
            # Một nơi nghe hỏng KHÔNG được kéo theo những nơi khác. Gửi mail
            # hỏng mà mất luôn cập nhật thống kê là vô lý.
            events_failed.inc(event=event)
            log.exception(
                "events.handler_failed", name=event, listener=item.label, error=str(exc)
            )
            return False
        else:
            events_handled.inc(event=event)
            return True
        finally:
            events_duration.observe(time.monotonic() - started, event=event)

    def stats(self) -> dict[str, Any]:
        return {
            "listeners": len(self._listeners) + len(self._dynamic),
            "pending": len(self._pending),
            "events": sorted({item.pattern for item in (*self._listeners, *self._dynamic)}),
        }


__all__ = [
    "EventBus",
    "EventSpec",
    "discover_listeners",
    "matches",
    "on_event",
]
