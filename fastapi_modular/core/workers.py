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

import ast
import asyncio
import contextlib
import contextvars
import inspect
import queue
import textwrap
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
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
# Thấy một trong những tên này là coi như vòng lặp có nghe lệnh dừng.
_STOP_NAMES = frozenset({"running", "wait", "request_stop", "is_set"})
# Bao lâu thì nhắc lại một lần trong lúc chờ worker dừng.
_STOP_REPORT_EVERY = 5.0

workers_started = registry.register(Counter("workers_started_total", "Số bản worker đã khởi động"))
workers_restarted = registry.register(
    Counter("workers_restarted_total", "Số lần một bản worker được dựng lại sau khi hỏng")
)
workers_running = registry.register(Gauge("workers_running", "Số bản worker đang chạy"))


class BlockingPool:
    """Pool thread cho `ctx.blocking(...)`. Giống `ThreadPoolExecutor`, trừ MỘT điểm.

    Điểm đó là thread ở đây `daemon=True`, và nó chính là lý do lớp này tồn tại.

    `ThreadPoolExecutor` của thư viện chuẩn dùng thread thường, mà lúc thoát,
    Python JOIN mọi thread thường — không có timeout, không cách nào bỏ qua.
    Nên chỉ cần MỘT lời gọi chặn không bao giờ trả về (một `cap.read()` trên
    luồng RTSP đã chết là đủ) thì tiến trình không thoát được nữa: Ctrl+C bấm
    bao nhiêu lần cũng chỉ in ra một KeyboardInterrupt trong `t.join()` rồi lại
    chờ tiếp, và người ta phải `kill -9`.

    Thread daemon thì không bị join. Lời gọi treo vẫn treo — Python không có
    cách nào giết một thread đang kẹt — nhưng nó không kéo cả tiến trình theo.

    Không cắm vào `loop.set_default_executor()`: hàm đó chỉ nhận đúng
    `ThreadPoolExecutor`. `ctx.blocking` gọi thẳng vào đây qua
    `asyncio.wrap_future`, nên `asyncio.to_thread` của người dùng vẫn dùng pool
    mặc định như thường.
    """

    __slots__ = ("_closed", "_idle", "_lock", "_name", "_queue", "_size", "_threads")

    def __init__(self, size: int, name: str = "fam-blocking") -> None:
        self._size = size
        self._name = name
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._threads: list[threading.Thread] = []
        self._idle = 0
        self._lock = threading.Lock()
        self._closed = False

    @property
    def size(self) -> int:
        return self._size

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        if self._closed:
            raise ServiceUnavailableError("App đang tắt nên không nhận việc chặn mới")
        future: Future = Future()
        self._queue.put((future, contextvars.copy_context(), fn, args, kwargs))
        self._grow()
        return future

    def _grow(self) -> None:
        """Mở thêm thread khi không còn ai rảnh, tới trần thì thôi."""
        with self._lock:
            if self._idle > 0 or len(self._threads) >= self._size:
                return
            thread = threading.Thread(
                target=self._loop, daemon=True, name=f"{self._name}-{len(self._threads)}"
            )
            self._threads.append(thread)
        thread.start()

    def _loop(self) -> None:
        while True:
            with self._lock:
                self._idle += 1
            item = self._queue.get()
            with self._lock:
                self._idle -= 1
            if item is None:
                return
            future, context, fn, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(context.run(fn, *args, **kwargs))
            except BaseException as exc:                # noqa: BLE001
                future.set_exception(exc)

    def shutdown(self) -> None:
        """Đóng cửa nhận việc. KHÔNG chờ thread — chờ chính là thứ ta đang tránh."""
        self._closed = True
        for _ in self._threads:
            self._queue.put(None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"size": self._size, "threads": len(self._threads), "idle": self._idle}


_blocking_pool: BlockingPool | None = None


def default_pool_size() -> int:
    """Mặc định của Python cho `asyncio.to_thread`; giữ nguyên cho đỡ bất ngờ."""
    import os

    return min(32, (os.cpu_count() or 1) + 4)


def configure_blocking_pool(size: int = 0) -> BlockingPool:
    """Dựng lại pool theo kích thước trong cấu hình. Lifespan gọi lúc khởi động."""
    global _blocking_pool
    if _blocking_pool is not None:
        _blocking_pool.shutdown()
    _blocking_pool = BlockingPool(size or default_pool_size())
    return _blocking_pool


def get_blocking_pool() -> BlockingPool:
    """Pool đang dùng; tự dựng nếu chưa có (test gọi thẳng, không qua lifespan)."""
    global _blocking_pool
    if _blocking_pool is None or _blocking_pool._closed:
        _blocking_pool = BlockingPool(default_pool_size())
    return _blocking_pool


def shutdown_blocking_pool() -> None:
    global _blocking_pool
    if _blocking_pool is not None:
        _blocking_pool.shutdown()
        _blocking_pool = None


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

        Bên dưới là một pool thread **dùng chung**: thread được TÁI SỬ DỤNG chứ
        không mở mới mỗi lần, và trần mặc định là `min(32, cpu+4)` chỗ. Nhiều
        worker gọi dày hơn số chỗ thì chúng xếp hàng chờ nhau — chậm đi chứ
        không hỏng. Nới trần bằng `APP_WORKERS__THREAD_POOL_SIZE`.

        Thread trong pool là `daemon`, nên một lời gọi chặn treo vĩnh viễn
        không giữ cả tiến trình lại lúc thoát (xem `BlockingPool`). Nhưng nó
        vẫn giữ MỘT chỗ trong pool vĩnh viễn — hàm chặn nào có tham số timeout
        thì hãy đặt nó.
        """
        if self._thread_mode:
            # Đang ở trong thread rồi, đẩy thêm một lớp nữa là vô ích.
            return fn(*args, **kwargs)
        return await asyncio.wrap_future(get_blocking_pool().submit(fn, *args, **kwargs))

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


def warn_if_endless(fn: Callable, kind: str, name: str) -> None:
    """Kêu lên khi thân hàm có `while True:` mà không hề nhìn tới cờ dừng.

    Đây là cái bẫy đắt nhất của cả ba decorator, và nó không lộ ra lúc chạy —
    chỉ lộ lúc TẮT: khung xin dừng, vòng lặp không nghe, khung chờ hết
    `APP_WORKERS__STOP_SECONDS` rồi mới bỏ mặc. Người dùng thấy Ctrl+C bấm mà
    không có gì xảy ra suốt hai chục giây, và kết luận là treo.

    Chỉ cảnh báo chứ không chặn: có những vòng lặp thoát bằng `break` theo điều
    kiện riêng, và ta không đọc được ý định đó từ cây cú pháp.
    """
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError, IndentationError):
        return                                    # hàm sinh động, không có mã nguồn
    nodes = list(ast.walk(tree))
    endless = any(
        isinstance(n, ast.While) and isinstance(n.test, ast.Constant) and bool(n.test.value)
        for n in nodes
    )
    if not endless:
        return
    if any(isinstance(n, ast.Attribute) and n.attr in _STOP_NAMES for n in nodes):
        return
    log.warning(
        "worker.endless_loop",
        kind=kind,
        name=name,
        function=fn.__qualname__,
        hint=(
            "`while True:` không có đường thoát: lúc tắt app khung xin dừng nhưng "
            "vòng lặp không nghe, phải chờ hết APP_WORKERS__STOP_SECONDS rồi bỏ mặc "
            "— Ctrl+C trông như bị treo. Khai `ctx: WorkerContext` rồi viết "
            "`while ctx.running:`, và dùng `ctx.wait(giây)` thay `time.sleep(giây)`."
        ),
    )


async def run_in_own_thread(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Chạy `fn` trong một thread RIÊNG, không mượn pool dùng chung.

    Vì sao không dùng `asyncio.to_thread` ở đây: nó đẩy việc vào
    `ThreadPoolExecutor` mặc định của event loop, mà pool đó chỉ có
    `min(32, cpu+4)` chỗ (16 trên một máy 12 nhân) và **dùng chung với mọi thứ
    khác**, kể cả `ctx.blocking`.

    Việc ngắn thì không sao — mượn rồi trả ngay. Nhưng một vòng lặp `@worker`
    chạy MÃI thì giữ chỗ đó vĩnh viễn: đủ 16 worker `thread=True` là pool cạn
    sạch, và mọi `ctx.blocking` sau đó xếp hàng không bao giờ tới lượt. Đo
    được: 20 worker thì cả tiến trình treo cứng, không phải chậm mà là chết.

    Thread ở đây là `daemon`, nên nó cũng không giữ tiến trình lại lúc thoát
    nếu vòng lặp không chịu dừng.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    context = contextvars.copy_context()      # giữ request-id như `to_thread` làm

    def deliver(setter: Callable[[Any], None], value: Any) -> None:
        if not future.done():
            setter(value)

    def runner() -> None:
        try:
            result = context.run(fn, *args, **kwargs)
        except BaseException as exc:          # noqa: BLE001 - chuyển nguyên vẹn về loop
            loop.call_soon_threadsafe(deliver, future.set_exception, exc)
        else:
            loop.call_soon_threadsafe(deliver, future.set_result, result)

    threading.Thread(target=runner, daemon=True, name=f"fam-worker-{fn.__name__}").start()
    return await future


async def call_handler(
    fn: Callable,
    instance: Any,
    *args: Any,
    context: WorkerContext | None = None,
    context_param: str = "",
    thread: bool = False,
    own_thread: bool = False,
) -> Any:
    """Gọi handler, tiêm `ctx` nếu nó xin, và chạy đúng chỗ (thread hay loop).

    `own_thread=True` dành cho việc CHẠY MÃI (`@worker`): thread riêng, không
    mượn pool. Việc ngắn (`@interval`, `@job`) thì mượn pool là đúng — mở thread
    mới cho mỗi lượt chạy 5 giây một lần là phí.
    """
    kwargs = {context_param: context} if context_param and context is not None else {}
    if thread:
        if own_thread:
            return await run_in_own_thread(fn, instance, *args, **kwargs)
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

        warn_if_endless(fn, "worker", spec.name)

        async def launch(self: Any, key: str = "", data: Any = None) -> WorkerHandle:
            pool = container.resolve(WorkerPool)
            return await pool.start(spec, self, str(key), data)

        async def stop(key: str = "", *, timeout: float | None = None) -> bool:
            """Dừng MỘT bản và chờ nó dọn xong. False = không có bản nào mang khoá đó.

                await self.watch.stop(device_id)

            Chờ tới lúc vòng lặp thoát hẳn — tức là sau `finally:` trong thân
            hàm — nên viết tiếp phần dọn dẹp ngay dưới lời gọi này là an toàn.
            """
            return await container.resolve(WorkerPool).stop(spec.name, str(key), timeout)

        async def stop_all(*, timeout: float | None = None) -> int:
            """Dừng MỌI bản của loại worker này, trả về số bản đã dừng."""
            return await container.resolve(WorkerPool).stop_kind(spec.name, timeout)

        def running() -> list[dict[str, Any]]:
            """Các bản của loại worker này đang chạy: khoá, thời gian sống."""
            pool = container.resolve(WorkerPool)
            return [row for row in pool.running() if row["worker"] == spec.name]

        def is_running(key: str = "") -> bool:
            return container.resolve(WorkerPool).is_running(spec.name, str(key))

        launch.__name__ = fn.__name__
        launch.__doc__ = fn.__doc__
        # Gắn lên chính hàm: method đã bind vẫn chuyển tiếp thuộc tính sang hàm
        # gốc, nên `self.watch.stop(...)` chạy được mà không phải nhắc lại tên
        # worker dưới dạng chuỗi ở chỗ gọi.
        launch.stop = stop                    # type: ignore[attr-defined]
        launch.stop_all = stop_all            # type: ignore[attr-defined]
        launch.running = running              # type: ignore[attr-defined]
        launch.is_running = is_running        # type: ignore[attr-defined]
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
            own_thread=True,          # vòng lặp chạy mãi: KHÔNG mượn pool chung
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

    async def stop(self, name: str, key: str = "", timeout: float | None = None) -> bool:
        """Dừng một bản. Trả về False nếu không có bản nào mang khoá đó."""
        handle = self._handles.get((name, key))
        if handle is None:
            return False
        await handle.stop(self._config.stop_seconds if timeout is None else timeout)
        return True

    async def stop_kind(self, name: str, timeout: float | None = None) -> int:
        """Dừng mọi bản của một loại worker, bất kể khoá. Trả về số bản đã dừng."""
        handles = [h for (kind, _), h in self._handles.items() if kind == name]
        if not handles:
            return 0
        limit = self._config.stop_seconds if timeout is None else timeout
        await asyncio.gather(*(h.stop(limit) for h in handles))
        return len(handles)

    def is_running(self, name: str, key: str = "") -> bool:
        handle = self._handles.get((name, key))
        return handle is not None and handle.running

    async def stop_all(self) -> None:
        """Xin mọi bản dừng, rồi chờ. Quá hạn thì nói rõ cái nào còn kẹt."""
        self._closing = True
        handles = list(self._handles.values())
        if not handles:
            return

        for handle in handles:
            handle.context.request_stop()

        # Nói NGAY, đừng đợi tới lúc quá hạn: một vòng lặp không nghe lệnh dừng
        # làm Ctrl+C trông như không ăn thua suốt `stop_seconds` giây.
        log.info(
            "worker.stopping",
            count=len(handles),
            workers=[f"{h.name}:{h.key}" if h.key else h.name for h in handles],
            timeout=self._config.stop_seconds,
        )
        # Chờ thành từng lát và nhắc lại giữa chừng: hai chục giây im lặng thì
        # người ta kết luận là treo và bấm Ctrl+C tiếp, hoặc `kill -9`.
        tasks = [h.task for h in handles]
        left = self._config.stop_seconds
        done: set[asyncio.Task[None]] = set()
        pending: set[asyncio.Task[None]] = set(tasks)
        while left > 0 and pending:
            slice_seconds = min(_STOP_REPORT_EVERY, left)
            done, pending = await asyncio.wait(tasks, timeout=slice_seconds)
            left -= slice_seconds
            if pending and left > 0:
                log.info(
                    "worker.stopping_still",
                    remaining=[t.get_name() for t in pending],
                    seconds_left=round(left, 1),
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


__all__ = [
    "BlockingPool",
    "WorkerContext",
    "WorkerHandle",
    "WorkerPool",
    "WorkerSpec",
    "configure_blocking_pool",
    "get_blocking_pool",
    "shutdown_blocking_pool",
    "warn_if_endless",
    "worker",
]
