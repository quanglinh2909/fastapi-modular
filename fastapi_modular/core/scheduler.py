"""`@interval` / `@cron` / `@timeout` — chạy việc theo lịch, tương đương
`@nestjs/schedule`.

    @injectable
    class CameraService:
        @interval(seconds=5)
        async def update_status(self) -> None: ...

        @cron("0 3 * * *", timezone="Asia/Ho_Chi_Minh")
        async def clean_old_logs(self) -> None: ...

        @timeout(seconds=10)                    # chạy MỘT lần, 10s sau khi boot
        async def warm_cache(self) -> None: ...

Không cần cấu hình gì để bật: có decorator thì runner chạy, không có thì nó
`return` ngay.

## Vì sao thứ này nên nằm trong khung

`fam run` mặc định bật **4 worker**. Một vòng `while True: await sleep(5)` viết
tay trong service sẽ chạy **bốn lần mỗi 5 giây** — và triệu chứng thì rất khó
lần: log nhân bốn, API ngoài kêu vượt quota, hai tiến trình ghi đè trạng thái
của nhau. Mặc định ở đây là `single=True`, tức có khoá; xem `core/locks.py`.

## Năm quyết định đã chốt sẵn

| | Mặc định | Vì sao |
|---|---|---|
| nhiều worker | `single=True`, có khoá | xem trên |
| chạy chồng | bỏ lượt nếu lượt trước chưa xong | nhịp đếm từ lúc XONG nên không thể chồng; cron thì bỏ lượt và ghi log |
| trôi nhịp | đếm từ lúc xong (fixed-delay) | `sleep(5)` sau việc mất 2s là 7 giây một lần — nói rõ ra thay vì để người dùng tự phát hiện |
| lỗi | ghi log, vòng lặp KHÔNG chết | một lần gọi API hỏng không được làm im vĩnh viễn |
| tắt app | chờ lượt đang chạy, có trần | cắt giữa lúc đang ghi DB là để lại dữ liệu dở |
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import _REGISTRY, container, injectable, request_scope
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.cron import CronExpression, parse_cron
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.locks import NoLock, SingleFlight, build_lock
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.metrics import Counter, Histogram, registry
from fastapi_modular.core.workers import (
    WorkerContext,
    call_handler,
    check_thread_mode,
    context_param_of,
    warn_if_endless,
)

log = get_logger(__name__)

_SPEC_ATTR = "__scheduled__"

scheduled_runs = registry.register(
    Counter("scheduled_runs_total", "Số lượt việc theo lịch đã chạy xong")
)
scheduled_failures = registry.register(
    Counter("scheduled_failures_total", "Số lượt việc theo lịch ném lỗi")
)
scheduled_skipped = registry.register(
    Counter("scheduled_skipped_total", "Số lượt bị bỏ (tiến trình khác đang giữ khoá, hoặc quá hạn)")
)
scheduled_duration = registry.register(
    Histogram("scheduled_duration_seconds", "Thời gian chạy một lượt")
)


@dataclass(slots=True)
class ScheduledSpec:
    kind: str                       # "interval" | "cron" | "timeout"
    name: str = ""
    seconds: float = 0.0
    expression: str = ""
    timezone: str = "UTC"
    single: bool = True
    run_on_startup: bool = False
    jitter: float = 0.0
    max_seconds: float | None = None
    thread: bool = False
    context_param: str = ""
    cls: type | None = None
    fn: Callable | None = None
    cron: CronExpression | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.cls and self.fn:
            return f"{self.cls.__name__}.{self.fn.__name__}"
        return self.kind

    @property
    def schedule(self) -> str:
        if self.kind == "cron":
            return f"{self.expression} ({self.timezone})"
        return f"mỗi {self.seconds}s" if self.kind == "interval" else f"sau {self.seconds}s"


def _decorate(spec: ScheduledSpec, thread: bool) -> Callable[[Callable], Callable]:
    def decorate(fn: Callable) -> Callable:
        check_thread_mode(fn, thread)
        warn_if_endless(fn, spec.kind, spec.label or fn.__qualname__)
        context_param = context_param_of(fn)
        others = [
            p for p in inspect.signature(fn).parameters if p not in ("self", context_param)
        ]
        if others:
            raise RuntimeError(
                f"{fn.__name__} chỉ được nhận `self` và (tuỳ chọn) `ctx: WorkerContext`: "
                "việc theo lịch tự chạy, không ai truyền dữ liệu vào. Cần dữ liệu thì "
                f"lấy qua DI trong `__init__`. Tham số thừa: {others}."
            )
        setattr(fn, _SPEC_ATTR, replace(spec, thread=thread, context_param=context_param))
        return fn

    return decorate


def interval(
    seconds: float,
    *,
    name: str = "",
    single: bool = True,
    run_on_startup: bool = False,
    jitter: float = 0.0,
    max_seconds: float | None = None,
    thread: bool = False,
) -> Callable[[Callable], Callable]:
    """Chạy lặp mỗi `seconds` giây — tương đương `@Interval()` của NestJS.

        seconds          chu kỳ, đếm từ lúc lượt trước CHẠY XONG (fixed-delay).
                         Việc mất 2s với chu kỳ 5s thì thực tế là 7 giây một
                         lần. Cần đúng nhịp tuyệt đối thì dùng `@cron`.
        single           True (mặc định) = chỉ một tiến trình chạy mỗi lượt.
                         Đặt False khi bạn CỐ Ý muốn mọi worker cùng chạy
                         (ví dụ dọn cache trong RAM của riêng từng tiến trình).
        run_on_startup   True = chạy ngay lần đầu thay vì đợi hết một chu kỳ.
        jitter           cộng ngẫu nhiên 0..jitter giây vào mỗi lần chờ. Đặt
                         khi nhiều máy cùng gọi một API ngoài, để chúng không
                         đập vào cùng một giây.
        max_seconds      trần thời gian MỘT lượt; quá thì huỷ lượt đó và ghi
                         log. Không đặt thì một lượt treo sẽ làm việc này im
                         luôn, mà không có gì báo.
        thread           True = chạy trong thread, hàm khai bằng `def` thường.
                         Dùng khi thân hàm toàn lời gọi CHẶN. Ghi database thì
                         qua `ctx.run(...)` — xem `WorkerContext`.

    Nhận `ctx: WorkerContext` nếu cần: `ctx.blocking(...)` để gọi hàm chặn ở
    bản `async def`, `ctx.run(...)` để gọi async ở bản `thread=True`. Không cần
    thì cứ bỏ, khung không truyền.
    """
    if seconds <= 0:
        raise BadRequestError(f"`seconds` phải lớn hơn 0 (đang là {seconds})")
    return _decorate(
        ScheduledSpec(
            kind="interval",
            name=name,
            seconds=seconds,
            single=single,
            run_on_startup=run_on_startup,
            jitter=max(0.0, jitter),
            max_seconds=max_seconds,
        ),
        thread,
    )


def cron(
    expression: str,
    *,
    timezone: str = "UTC",
    name: str = "",
    single: bool = True,
    max_seconds: float | None = None,
    thread: bool = False,
) -> Callable[[Callable], Callable]:
    """Chạy theo biểu thức cron — tương đương `@Cron()` của NestJS.

        expression   5 trường: phút giờ ngày tháng thứ. Có `@daily`, `@hourly`...
        timezone     MẶC ĐỊNH LÀ UTC. `"0 3 * * *"` với mặc định này là 10 giờ
                     sáng giờ Việt Nam. Ý bạn là 3 giờ sáng giờ ta thì truyền
                     `timezone="Asia/Ho_Chi_Minh"`.

    Lúc khởi động khung in ra lần chạy kế tiếp ở CẢ hai múi giờ, để đặt nhầm
    múi lộ ra ngay chứ không đợi tới nửa đêm mới biết.
    """
    parsed = parse_cron(expression)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BadRequestError(
            f"Múi giờ {timezone!r} không có. Dùng tên IANA, ví dụ 'Asia/Ho_Chi_Minh' hoặc 'UTC'."
        ) from exc

    return _decorate(
        ScheduledSpec(
            kind="cron",
            name=name,
            expression=expression,
            timezone=timezone,
            single=single,
            max_seconds=max_seconds,
            cron=parsed,
        ),
        thread,
    )


def timeout(
    seconds: float,
    *,
    name: str = "",
    single: bool = True,
    max_seconds: float | None = None,
    thread: bool = False,
) -> Callable[[Callable], Callable]:
    """Chạy ĐÚNG MỘT LẦN, `seconds` giây sau khi app khởi động —
    tương đương `@Timeout()` của NestJS.

    Dùng cho việc hâm nóng: nạp cache, dựng sẵn kết nối, kiểm tra một lần.
    Việc phải chạy NGAY lúc boot thì đừng dùng cái này — đặt thẳng vào lifespan
    của dự án, vì ở đó bạn chặn được app nhận request cho tới khi xong.
    """
    if seconds < 0:
        raise BadRequestError(f"`seconds` không được âm (đang là {seconds})")
    return _decorate(
        ScheduledSpec(
            kind="timeout", name=name, seconds=seconds, single=single, max_seconds=max_seconds
        ),
        thread,
    )


def discover_scheduled() -> list[ScheduledSpec]:
    """Quét mọi class đã đăng ký để tìm method mang @interval/@cron/@timeout."""
    found: list[ScheduledSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: ScheduledSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is not None:
                found.append(replace(spec, cls=cls, fn=fn))

    seen: dict[str, ScheduledSpec] = {}
    for spec in found:
        if spec.label in seen:
            raise RuntimeError(
                f"Hai việc theo lịch cùng tên {spec.label!r}. Tên là danh tính của KHOÁ — "
                "trùng tên thì hai việc khác nhau tranh nhau một khoá và cái nào cũng chỉ "
                "chạy được một nửa số lượt. Đặt `name=` khác nhau."
            )
        seen[spec.label] = spec
    return sorted(found, key=lambda s: s.label)


@injectable
class SchedulerRunner:
    """Chạy mọi việc theo lịch tìm được. Mỗi việc một task riêng."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._config = settings.scheduler
        self._specs: list[ScheduledSpec] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running: set[str] = set()
        self._owned: set[str] = set()
        self._renewer: asyncio.Task[None] | None = None
        self._lock: SingleFlight = NoLock()
        self._closing = False

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._specs = discover_scheduled()
        if not self._specs:
            return

        self._closing = False
        self._lock = (
            build_lock(self._settings, directory=self._config.lock_dir)
            if self._config.single
            else NoLock()
        )
        for spec in self._specs:
            self._log_plan(spec)
            self._tasks[spec.label] = asyncio.create_task(
                self._loop(spec), name=f"scheduled-{spec.label}"
            )
        self._renewer = asyncio.create_task(self._renew_loop(), name="scheduled-renew")
        log.info("scheduler.started", jobs=len(self._specs), lock=self._lock.scope)

    def _log_plan(self, spec: ScheduledSpec) -> None:
        """In lịch ra lúc khởi động, và với cron thì in cả hai múi giờ.

        Đặt nhầm múi giờ là lỗi im lặng đắt nhất của cron: nó vẫn chạy, chỉ là
        chạy sai giờ, và người ta chỉ phát hiện sau vài ngày.
        """
        detail: dict[str, Any] = {"job": spec.label, "schedule": spec.schedule}
        if spec.kind == "cron" and spec.cron is not None:
            zone = ZoneInfo(spec.timezone)
            nxt = spec.cron.next_after(datetime.now(zone))
            detail["next_run"] = nxt.isoformat()
            detail["next_run_local"] = nxt.astimezone().isoformat()
        log.info("scheduler.job", **detail)

    async def _loop(self, spec: ScheduledSpec) -> None:
        try:
            if spec.single and not await self._become_owner(spec):
                return
            if spec.kind == "timeout":
                await asyncio.sleep(spec.seconds)
                await self._run_once(spec)
                return
            if spec.kind == "cron":
                await self._cron_loop(spec)
                return
            await self._interval_loop(spec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("scheduler.loop_crashed", job=spec.label, error=str(exc))

    async def _become_owner(self, spec: ScheduledSpec) -> bool:
        """Chờ tới khi giành được quyền chạy việc này.

        Giành MỘT LẦN rồi giữ, chứ không khoá quanh từng lượt: bốn worker có
        bốn đồng hồ riêng, nên khoá-rồi-nhả-ngay vẫn để cả bốn cùng chạy, chỉ
        là lệch nhau vài mili-giây. Xem `core/locks.py`.

        Tiến trình không giành được thì nằm chờ chứ không thoát — chủ hiện tại
        có thể chết, và khi đó một trong những tiến trình đang chờ phải lên
        thay mà không cần ai can thiệp.
        """
        first = True
        while not self._closing:
            if await self._lock.acquire(spec.label):
                self._owned.add(spec.label)
                log.info("scheduler.owner", job=spec.label, lock=self._lock.scope)
                return True
            if first:
                log.info(
                    "scheduler.standby",
                    job=spec.label,
                    hint="tiến trình khác đang chạy việc này; sẽ lên thay nếu nó dừng",
                )
                first = False
            await self._sleep(self._config.takeover_seconds, 0.0)
        return False

    async def _renew_loop(self) -> None:
        """Gia hạn mọi khoá đang giữ.

        Chỉ có ý nghĩa với khoá Redis (khoá có hạn). Với `flock` thì `renew`
        không làm gì, nhưng vẫn chạy vòng này để một đường code lo cả hai.
        """
        while not self._closing:
            await self._sleep(self._config.renew_seconds, 0.0)
            for name in list(self._owned):
                if not await self._lock.renew(name):
                    # Mất khoá giữa chừng: gần như luôn là Redis chớp hoặc
                    # tiến trình bị treo lâu hơn hạn khoá. Nói ra, vì từ lúc
                    # này việc có thể đang chạy ở hai nơi.
                    log.warning(
                        "scheduler.lock_lost",
                        job=name,
                        hint="khoá hết hạn trước khi gia hạn kịp — việc có thể đang chạy hai nơi",
                    )

    async def _interval_loop(self, spec: ScheduledSpec) -> None:
        if not spec.run_on_startup:
            await self._sleep(spec.seconds, spec.jitter)
        while not self._closing:
            await self._run_once(spec)
            if self._closing:
                return
            # Đếm từ lúc CHẠY XONG. Không thể chồng lượt, đổi lại nhịp trôi
            # theo thời gian chạy — đã nói rõ ở docstring của `interval`.
            await self._sleep(spec.seconds, spec.jitter)

    async def _cron_loop(self, spec: ScheduledSpec) -> None:
        assert spec.cron is not None
        zone = ZoneInfo(spec.timezone)
        while not self._closing:
            now = datetime.now(zone)
            nxt = spec.cron.next_after(now)
            await self._sleep(max(0.0, (nxt - now).total_seconds()), 0.0)
            if self._closing:
                return
            await self._run_once(spec)
            # Lượt chạy lâu hơn khoảng cách tới mốc kế tiếp thì mốc đó đã trôi
            # qua. Bỏ, và nói ra — im lặng ở đây nghĩa là "sao hôm nay thiếu
            # một lần chạy" mà không ai giải thích được.
            if datetime.now(zone) > spec.cron.next_after(nxt):
                scheduled_skipped.inc(job=spec.label)
                log.warning(
                    "scheduler.tick_missed",
                    job=spec.label,
                    hint="một lượt chạy lâu hơn khoảng cách giữa hai mốc cron",
                )

    async def _sleep(self, seconds: float, jitter: float) -> None:
        if jitter:
            seconds += random.uniform(0.0, jitter)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(seconds)

    async def _run_once(self, spec: ScheduledSpec) -> None:
        self._running.add(spec.label)
        token = set_request_id(new_request_id())
        started = time.monotonic()
        try:
            async with request_scope():
                instance = container.resolve(spec.cls)      # type: ignore[arg-type]
                context = WorkerContext(
                    spec.label, "", asyncio.get_running_loop(), thread_mode=spec.thread
                )
                call = call_handler(
                    spec.fn,                                # type: ignore[arg-type]
                    instance,
                    context=context,
                    context_param=spec.context_param,
                    thread=spec.thread,
                    own_thread=True,
                )
                if spec.max_seconds:
                    await asyncio.wait_for(call, spec.max_seconds)
                else:
                    await call
        except (TimeoutError, asyncio.TimeoutError):
            scheduled_failures.inc(job=spec.label)
            log.error(
                "scheduler.run_timeout",
                job=spec.label,
                max_seconds=spec.max_seconds,
                hint="lượt này bị huỷ; lượt sau vẫn chạy như thường",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            scheduled_failures.inc(job=spec.label)
            log.exception("scheduler.run_failed", job=spec.label, error=str(exc))
        else:
            scheduled_runs.inc(job=spec.label)
        finally:
            scheduled_duration.observe(time.monotonic() - started, job=spec.label)
            reset_request_id(token)
            self._running.discard(spec.label)

    async def shutdown(self) -> None:
        """Dừng lịch, nhưng CHỜ lượt đang chạy dở.

        Cắt giữa chừng một lượt đang ghi database là để lại dữ liệu dở dang.
        Chờ có trần: quá `drain_seconds` thì huỷ và ghi log tên việc còn kẹt,
        để không ai phải đoán vì sao tắt app lâu thế.
        """
        self._closing = True
        if self._renewer is not None:
            self._renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewer
            self._renewer = None

        tasks = list(self._tasks.values())
        self._tasks.clear()
        if not tasks:
            return

        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=self._config.drain_seconds)
        if pending:
            log.warning(
                "scheduler.drain_timeout",
                seconds=self._config.drain_seconds,
                still_running=sorted(self._running),
            )
        for task in done:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.result()

        # Nhả khoá để tiến trình khác lên thay ngay, đừng bắt nó đợi hết hạn.
        for name in list(self._owned):
            await self._lock.release(name)
        self._owned.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "lock": self._lock.scope,
            "jobs": [
                {
                    "job": spec.label,
                    "kind": spec.kind,
                    "schedule": spec.schedule,
                    "single": spec.single,
                    "owner": spec.label in self._owned,
                    "running": spec.label in self._running,
                }
                for spec in self._specs
            ],
        }


__all__ = [
    "ScheduledSpec",
    "SchedulerRunner",
    "cron",
    "discover_scheduled",
    "interval",
    "timeout",
]
