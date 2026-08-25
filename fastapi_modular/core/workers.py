"""`@worker` — vòng lặp sống mãi, N bản, mỗi bản một tham số.

Chỗ trống mà `@interval` và `@job` không lấp được:

    @interval   tới giờ thì chạy MỘT lượt rồi thôi — không giữ được cái gì
                giữa hai lượt, nên không mở camera ở ngoài vòng lặp được
    @job        một việc, chạy xong là hết
    @worker     một vòng lặp CHẠY MÃI, có phần dựng ở trước và dọn ở sau,
                và chạy được NHIỀU BẢN cùng lúc với tham số khác nhau

Đúng hình dạng của việc "mỗi camera một luồng, khác nhau mỗi cái IP":

    @injectable
    class CameraService:
        def __init__(self, db: Database) -> None:
            self._db = db

        @worker(key="ip")
        async def watch(self, ip: str, ctx: WorkerContext) -> None:
            capture = await ctx.blocking(cv2.VideoCapture, ip)   # dựng, ngoài vòng lặp
            try:
                while ctx.running:                                # vòng lặp
                    frame = await ctx.blocking(capture.read)
                    events = await ctx.blocking(model.predict, frame)
                    await self._db.save(events)                   # await như thường
            finally:
                await ctx.blocking(capture.release)               # dọn

    # gọi hàm là SINH ra một bản chạy nền, trả về ngay
    for ip in ips:
        await service.watch(ip)

Gọi lại cùng một `key` thì **không** sinh bản thứ hai — nó trả về bản đang
chạy. Với camera thì đó là điều bắt buộc: mở hai kết nối RTSP tới cùng một
thiết bị là cách nhanh nhất để cả hai cùng giật.

## Hai kiểu chạy, và chọn kiểu nào

|  | `@worker(...)` | `@worker(..., thread=True)` |
|---|---|---|
| Hàm khai bằng | `async def` | `def` thường |
| Chạy ở đâu | trên vòng lặp sự kiện | trong một thread riêng |
| Gọi `await db.save()` | thẳng, như mọi chỗ khác | `ctx.run(self._db.save(...))` |
| Gọi hàm chặn | `await ctx.blocking(fn, ...)` | gọi thẳng, đang ở thread rồi |

**Mặc định (`async def`) đúng cho gần hết mọi trường hợp**, kể cả camera + AI:
mỗi lời gọi chặn bọc trong `ctx.blocking(...)` là nó chạy ở thread khác, còn
`await` vào database thì thẳng tuột.

`thread=True` chỉ đáng dùng khi vòng lặp gọi hàm chặn **liên tục và dày**, tới
mức bọc từng lời gọi thành rườm rà. Cái giá của nó là mọi lời gọi async phải đi
qua `ctx.run(...)`, và **không huỷ giữa chừng được**: một `capture.read()` treo
sẽ giữ luôn lúc tắt app cho tới hết `APP_WORKERS__STOP_SECONDS`.

## Hỏng thì sao

Vòng lặp ném lỗi thì khung ghi log rồi **dựng lại** sau một khoảng chờ tăng dần
(1s, 2s, 4s… tới trần). Camera rớt mạng là chuyện thường ngày, và một vòng lặp
chết im lặng thì không ai biết cho tới lúc có người hỏi "sao camera 12 không
lên sự kiện nữa".

Không muốn dựng lại thì `restart=False` — khi đó lỗi làm bản chạy đó dừng hẳn.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import container, injectable
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.exceptions import BadRequestError, ServiceUnavailableError
from fastapi_modular.core.locks import NoLock, SingleFlight, build_lock
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.metrics import Counter, Gauge, registry

log = get_logger(__name__)

_SPEC_ATTR = "__worker__"

workers_started = registry.register(Counter("workers_started_total", "Số bản worker đã khởi động"))
workers_restarted = registry.register(
    Counter("workers_restarted_total", "Số lần một bản worker được dựng lại sau khi hỏng")
)
workers_running = registry.register(Gauge("workers_running", "Số bản worker đang chạy"))


class WorkerContext:
    """Thứ khung đưa vào vòng lặp: cờ dừng, và cầu nối giữa thread và event loop.

    Nhận nó bằng cách khai một tham số chú kiểu `WorkerContext`:

        async def watch(self, ip: str, ctx: WorkerContext) -> None: ...
    """

    __slots__ = ("_key", "_loop", "_name", "_stop", "_thread_mode")

    def __init__(self, name: str, key: str, loop: asyncio.AbstractEventLoop, *, thread_mode: bool) -> None:
        self._name = name
        self._key = key
        self._loop = loop
        self._stop = threading.Event()
        self._thread_mode = thread_mode

    @property
    def name(self) -> str:
        return self._name

    @property
    def key(self) -> str:
        return self._key

    @property
    def running(self) -> bool:
        """`while ctx.running:` — hoá False khi có người gọi stop hoặc app tắt.

        Đây là cách DUY NHẤT để vòng lặp thoát sạch. Viết `while True:` thì lúc
        tắt app khung phải huỷ ngang, và với `thread=True` thì nó còn không huỷ
        được — chỉ đợi rồi bỏ mặc.
        """
        return not self._stop.is_set()

    def request_stop(self) -> None:
        self._stop.set()

    def wait(self, seconds: float) -> bool:
        """Ngủ trong thread nhưng TỈNH NGAY khi có lệnh dừng.

        Dùng thay `time.sleep()`: `time.sleep(30)` giữ lúc tắt app thêm 30 giây,
        còn cái này thoát ngay. Trả về True nếu bị đánh thức để dừng.
        """
        return self._stop.wait(seconds)

    async def blocking(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Chạy một hàm CHẶN ở thread khác, chờ kết quả — chỉ cho worker `async def`.

        `cap.read()`, `model.predict()`, `cv2.VideoCapture()` đều là hàm chặn.
        Gọi thẳng trong `async def` thì cả tiến trình đứng im chờ chúng: mọi
        request HTTP, mọi frame WebSocket, mọi worker khác.
        """
        if self._thread_mode:
            # Đang ở trong thread rồi, đẩy thêm một lớp nữa là vô ích.
            return fn(*args, **kwargs)
        if kwargs:
            return await asyncio.to_thread(lambda: fn(*args, **kwargs))
        return await asyncio.to_thread(fn, *args)

    def run(self, coro: Coroutine[Any, Any, Any], *, timeout: float | None = None) -> Any:
        """Chạy một coroutine trên event loop và chờ kết quả — chỉ cho `thread=True`.

        Đây là câu trả lời cho "chạy trong thread thì ghi database kiểu gì":
        `await` không dùng được trong `def` thường, mà gọi `asyncio.run()` thì
        tạo một event loop MỚI, và connection pool của database thuộc về loop
        cũ — nó sẽ hỏng theo những cách rất khó hiểu.

            ctx.run(self._db.save(event))
        """
        if not self._thread_mode:
            coro.close()
            raise RuntimeError(
                "`ctx.run()` chỉ dùng trong worker khai `thread=True`. Worker "
                "`async def` thì `await` thẳng như bình thường."
            )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)


def context_param_of(fn: Callable) -> str:
    """Tên tham số được chú kiểu `WorkerContext`, hoặc "" nếu hàm không xin.

    `ctx` là TUỲ CHỌN ở mọi decorator: không khai thì khung không truyền. Ai
    cần cờ dừng hay cần gọi qua lại giữa thread và event loop thì mới khai.
    """
    for parameter in inspect.signature(fn).parameters.values():
        if parameter.annotation in (WorkerContext, "WorkerContext"):
            return parameter.name
    return ""


def check_thread_mode(fn: Callable, thread: bool) -> None:
    """`thread=True` cần `def` thường; không thì cần `async def`."""
    is_async = inspect.iscoroutinefunction(fn)
    if thread and is_async:
        raise RuntimeError(
            f"{fn.__name__} khai `thread=True` thì phải là `def` thường, không phải "
            "`async def` — cả điểm của nó là chạy ngoài vòng lặp sự kiện."
        )
    if not thread and not is_async:
        raise RuntimeError(
            f"{fn.__name__} phải là `async def`. Toàn hàm chặn thì khai `thread=True`."
        )


async def call_handler(
    fn: Callable,
    instance: Any,
    *args: Any,
    context: WorkerContext | None = None,
    context_param: str = "",
    thread: bool = False,
) -> Any:
    """Gọi handler, tiêm `ctx` nếu nó xin, và chạy đúng chỗ (thread hay loop)."""
    kwargs = {context_param: context} if context_param and context is not None else {}
    if thread:
        return await asyncio.to_thread(fn, instance, *args, **kwargs)
    return await fn(instance, *args, **kwargs)


@dataclass(slots=True)
class WorkerSpec:
    name: str
    thread: bool = False
    restart: bool = True
    restart_delay: float = 1.0
    max_restart_delay: float = 30.0
    single: bool = False
    fn: Callable | None = None
    context_param: str = ""
    wants_data: bool = False


@dataclass(slots=True)
class WorkerHandle:
    """Tay cầm của MỘT bản đang chạy."""

    name: str
    key: str
    context: WorkerContext
    task: asyncio.Task[None]
    started_at: float

    @property
    def running(self) -> bool:
        return not self.task.done()

    async def stop(self, timeout: float | None = None) -> None:
        """Xin dừng, rồi chờ vòng lặp thoát."""
        self.context.request_stop()
        with contextlib.suppress(TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(self.task), timeout)


def worker(
    name: str = "",
    *,
    thread: bool = False,
    restart: bool = True,
    restart_delay: float = 1.0,
    max_restart_delay: float = 30.0,
    single: bool = False,
) -> Callable[[Callable], Callable]:
    """Biến một method thành vòng lặp chạy nền; GỌI nó là sinh ra một bản.

        name               tên loại worker. Không khai thì lấy `__qualname__`,
                           tức `CameraService.watch` — đã kèm tên lớp nên hai
                           method trùng tên ở hai lớp khác nhau không đụng nhau.
        thread             True = chạy cả vòng lặp trong thread; hàm phải là
                           `def` thường và ghi database qua `ctx.run(...)`.
        restart            True (mặc định) = hỏng thì dựng lại, chờ tăng dần.
        restart_delay      chờ bao lâu trước lần dựng lại đầu tiên.
        max_restart_delay  trần thời gian chờ.
        single             True = chỉ MỘT tiến trình chạy bản này, khoá theo
                           `<name>:<key>`. Đặt khi nhiều worker uvicorn cùng
                           nối tới một thiết bị.

    Khoá và dữ liệu truyền vào LÚC GỌI, không lấy từ chữ ký hàm:

        @worker("camera")
        async def watch(self, data: dict, ctx: WorkerContext) -> None:
            ip = data["ip"]

        await service.watch("cam-01", {"ip": "10.0.0.1", "fps": 15})

    `ctx: WorkerContext` là tuỳ chọn về cú pháp nhưng gần như luôn cần ở
    `@worker`: `ctx.running` là cách duy nhất thoát vòng lặp cho sạch, và
    `ctx.blocking(...)` là cách gọi hàm chặn mà không giữ cả tiến trình.
    """
    if restart_delay <= 0:
        raise BadRequestError(f"`restart_delay` phải lớn hơn 0 (đang là {restart_delay})")

    def decorate(fn: Callable) -> Callable:
        check_thread_mode(fn, thread)
        context_param = context_param_of(fn)
        others = [
            p for p in inspect.signature(fn).parameters if p not in ("self", context_param)
        ]
        if len(others) > 1:
            raise RuntimeError(
                f"{fn.__name__}: chữ ký phải là (self, data) hoặc (self, data, ctx), "
                f"hoặc bỏ hẳn `data`. Tham số đang thừa: {others[1:]}."
            )

        spec = WorkerSpec(
            name=name or fn.__qualname__,
            thread=thread,
            restart=restart,
            restart_delay=restart_delay,
            max_restart_delay=max_restart_delay,
            single=single,
            fn=fn,
            context_param=context_param,
            wants_data=bool(others),
        )

        async def launch(self: Any, key: str = "", data: Any = None) -> WorkerHandle:
            pool = container.resolve(WorkerPool)
            return await pool.start(spec, self, str(key), data)

        launch.__name__ = fn.__name__
        launch.__doc__ = fn.__doc__
        setattr(launch, _SPEC_ATTR, spec)
        return launch

    return decorate


@injectable
class WorkerPool:
    """Sổ những bản worker đang chạy, và chỗ dừng chúng.

    Tiêm vào service nào cần dừng/liệt kê worker; còn để KHỞI ĐỘNG thì chỉ việc
    gọi chính method mang `@worker`.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._config = settings.workers
        self._handles: dict[tuple[str, str], WorkerHandle] = {}
        self._lock: SingleFlight | None = None
        self._closing = False

    async def start(
        self, spec: WorkerSpec, instance: Any, key: str, data: Any
    ) -> WorkerHandle:
        if self._closing:
            raise ServiceUnavailableError("App đang tắt nên không nhận worker mới")

        existing = self._handles.get((spec.name, key))
        if existing is not None and existing.running:
            # Gọi lại cùng khoá KHÔNG mở thêm bản. Với camera thì mở hai kết
            # nối RTSP tới cùng thiết bị là cách nhanh nhất để cả hai cùng giật.
            log.debug("worker.already_running", worker=spec.name, key=key)
            return existing

        if len(self._handles) >= self._config.max_instances:
            raise ServiceUnavailableError(
                f"Đã có {len(self._handles)} worker đang chạy, chạm trần "
                f"APP_WORKERS__MAX_INSTANCES. Trần này để chặn việc sinh worker trong "
                "một vòng lặp hoặc trong HTTP handler mà quên dừng."
            )

        context = WorkerContext(
            spec.name, key, asyncio.get_running_loop(), thread_mode=spec.thread
        )
        task = asyncio.create_task(
            self._supervise(spec, instance, data, context),
            name=f"worker-{spec.name}-{key}" if key else f"worker-{spec.name}",
        )
        handle = WorkerHandle(spec.name, key, context, task, time.monotonic())
        self._handles[(spec.name, key)] = handle
        workers_started.inc(worker=spec.name)
        workers_running.inc_gauge(1)
        log.info("worker.started", worker=spec.name, key=key, thread=spec.thread)
        return handle

    async def _supervise(
        self, spec: WorkerSpec, instance: Any, data: Any, context: WorkerContext
    ) -> None:
        """Chạy vòng lặp, và dựng lại khi nó hỏng."""
        if spec.single and not await self._become_owner(spec, context):
            return

        delay = spec.restart_delay
        try:
            while context.running and not self._closing:
                token = set_request_id(new_request_id())
                try:
                    await self._run_body(spec, instance, data, context)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception(
                        "worker.crashed",
                        worker=spec.name,
                        key=context.key,
                        error=str(exc),
                        restart_in=delay if spec.restart else None,
                    )
                    if not spec.restart or not context.running:
                        return
                    workers_restarted.inc(worker=spec.name)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, spec.max_restart_delay)
                    continue
                else:
                    # Vòng lặp tự kết thúc bình thường: người viết chủ động
                    # thoát, đừng dựng lại.
                    log.info("worker.finished", worker=spec.name, key=context.key)
                    return
                finally:
                    reset_request_id(token)
        finally:
            workers_running.inc_gauge(-1)
            self._handles.pop((spec.name, context.key), None)
            if spec.single and self._lock is not None:
                await self._lock.release(f"{spec.name}:{context.key}")

    async def _run_body(
        self, spec: WorkerSpec, instance: Any, data: Any, context: WorkerContext
    ) -> None:
        # `thread=True` thì vòng lặp nằm trọn trong thread và KHÔNG huỷ ngang
        # được — `ctx.running` là đường duy nhất bảo nó dừng.
        args = (data,) if spec.wants_data else ()
        await call_handler(
            spec.fn,                                              # type: ignore[arg-type]
            instance,
            *args,
            context=context,
            context_param=spec.context_param,
            thread=spec.thread,
        )

    async def _become_owner(self, spec: WorkerSpec, context: WorkerContext) -> bool:
        if self._lock is None:
            self._lock = build_lock(self._settings) if self._config.single else NoLock()
        name = f"{spec.name}:{context.key}"
        first = True
        while context.running and not self._closing:
            if await self._lock.acquire(name):
                return True
            if first:
                log.info("worker.standby", worker=spec.name, key=context.key)
                first = False
            await asyncio.sleep(self._config.takeover_seconds)
        return False

    async def stop(self, name: str, key: str = "") -> bool:
        """Dừng một bản. Trả về False nếu không có bản nào mang khoá đó."""
        handle = self._handles.get((name, key))
        if handle is None:
            return False
        await handle.stop(self._config.stop_seconds)
        return True

    async def stop_all(self) -> None:
        """Xin mọi bản dừng, rồi chờ. Quá hạn thì nói rõ cái nào còn kẹt."""
        self._closing = True
        handles = list(self._handles.values())
        if not handles:
            return

        for handle in handles:
            handle.context.request_stop()

        done, pending = await asyncio.wait(
            [h.task for h in handles], timeout=self._config.stop_seconds
        )
        if pending:
            stuck = [f"{h.name}:{h.key}" for h in handles if not h.task.done()]
            # Với `thread=True` thì huỷ cũng không ăn thua: thread đang kẹt
            # trong một lời gọi chặn thì phải đợi chính lời gọi đó trả về.
            log.warning(
                "worker.stop_timeout",
                seconds=self._config.stop_seconds,
                stuck=stuck,
                hint="vòng lặp có kiểm `ctx.running` không, và lời gọi chặn có đặt timeout chưa?",
            )
            for task in pending:
                task.cancel()
        for task in done:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.result()
        self._handles.clear()

    def running(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "worker": handle.name,
                "key": handle.key,
                "uptime_seconds": round(now - handle.started_at, 1),
                "running": handle.running,
            }
            for handle in sorted(self._handles.values(), key=lambda h: (h.name, h.key))
        ]

    def stats(self) -> dict[str, Any]:
        return {"count": len(self._handles), "instances": self.running()}


__all__ = ["WorkerContext", "WorkerHandle", "WorkerPool", "WorkerSpec", "worker"]
