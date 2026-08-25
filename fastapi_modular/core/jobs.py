"""`@job` + `JobQueue` — hàng đợi việc NGAY TRONG TIẾN TRÌNH, không cần broker.

    @injectable
    class ImageService:
        @job("detect")
        async def detect(self, payload: dict) -> None: ...

    @injectable
    class UploadController:
        def __init__(self, jobs: JobQueue) -> None:
            self._jobs = jobs

        async def upload(self, file) -> dict:
            await self._jobs.submit("detect", {"path": path})   # trả về NGAY
            return {"nhan": "đang xử lý"}

Chỉ dùng `asyncio.Queue` của Python. Không Redis, không RabbitMQ, không Kafka,
không thêm một dòng cấu hình nào.

## Đọc dòng này trước khi dùng

**Việc nằm trong RAM. App tắt hay chết là mất sạch những việc còn trong hàng
đợi.** Không có cách nào lách: tiến trình biến mất thì bộ nhớ của nó biến mất
theo. Đây không phải thiếu sót của khung mà là bản chất của hàng đợi trong
tiến trình.

Vậy nên chỉ giao vào đây những việc **mất cũng chấp nhận được**, hoặc **dựng
lại được từ dữ liệu đã lưu**:

    hợp:      ghi log, cập nhật thống kê, gửi thông báo, sinh ảnh thu nhỏ,
              chạy nhận dạng cho một ảnh ĐÃ nằm trên đĩa
    KHÔNG:    trừ tiền, tạo đơn, gửi mail xác nhận, bất cứ việc gì mà mất đi
              thì người dùng phải gọi tổng đài

Việc thuộc nhóm sau cần hàng đợi bền — [`@rabbitmq_subscriber`](../../docs/rabbitmq.md).

Khung nói thẳng con số đó ra lúc tắt: còn bao nhiêu việc chưa chạy thì ghi log
`jobs.dropped_on_shutdown` kèm số lượng, chứ không im lặng.

## Tuần tự, và tuần tự tới đâu

Mặc định `workers=1`: đúng một việc chạy tại một thời điểm, theo thứ tự gửi
vào. Đặt `APP_JOBS__WORKERS=4` thì bốn việc chạy song song và **thứ tự không
còn bảo đảm**.

Thường thứ bạn cần không phải tuần tự toàn cục mà là **tuần tự theo từng
camera**: hai camera chạy song song, nhưng hai việc của cùng một camera phải
đúng thứ tự. Cách làm: `workers=1`, hoặc tự gom việc theo camera trước khi gửi.

## Việc nặng (YOLO) thì sao

`@job("x", blocking=True)` chạy handler trong một **thread** thay vì trên vòng
lặp sự kiện. Việc này CÓ tác dụng với torch/opencv/numpy vì phần tính toán của
chúng viết bằng C và **nhả GIL** trong lúc chạy. Nó KHÔNG có tác dụng với vòng
lặp Python thuần — cái đó vẫn giữ GIL và vẫn làm nghẽn cả tiến trình.

Và dù có `blocking=True`, chạy nhận dạng trong cùng tiến trình với API vẫn
tranh CPU với việc phục vụ request. Tải thật thì tách hẳn ra một tiến trình
worker riêng đọc từ RabbitMQ.
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
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.exceptions import BadRequestError, ServiceUnavailableError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.metrics import Counter, Gauge, Histogram, registry

log = get_logger(__name__)

_SPEC_ATTR = "__job__"

jobs_submitted = registry.register(Counter("jobs_submitted_total", "Số việc đã gửi vào hàng đợi"))
jobs_done = registry.register(Counter("jobs_done_total", "Số việc chạy xong"))
jobs_failed = registry.register(Counter("jobs_failed_total", "Số việc ném lỗi (đã hết lượt thử)"))
jobs_rejected = registry.register(Counter("jobs_rejected_total", "Số việc bị từ chối vì hàng đợi đầy"))
jobs_queued = registry.register(Gauge("jobs_queued", "Số việc đang nằm chờ"))
jobs_duration = registry.register(Histogram("jobs_duration_seconds", "Thời gian chạy một việc"))


@dataclass(slots=True)
class JobSpec:
    name: str
    max_retries: int = 0
    retry_delay: float = 1.0
    blocking: bool = False
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.name


def job(
    name: str, *, max_retries: int = 0, retry_delay: float = 1.0, blocking: bool = False
) -> Callable[[Callable], Callable]:
    """Khai một loại việc chạy nền; gửi vào bằng `JobQueue.submit(name, payload)`.

        name         tên loại việc, dùng lúc gửi. Phải là duy nhất trong app.
        max_retries  số lần thử lại NGAY TẠI CHỖ khi handler ném lỗi.
                     Mặc định 0 — hỏng là ghi log rồi bỏ. Nhớ là thử lại làm
                     đứng cả hàng đợi khi `workers=1`.
        retry_delay  chờ bao lâu giữa hai lần thử (giây).
        blocking     True = chạy trong thread thay vì trên vòng lặp sự kiện.
                     Chỉ đặt cho việc dùng thư viện nhả GIL (torch, opencv,
                     numpy). Xem docstring của module.

    Tham số đầu chú kiểu bằng model Pydantic thì payload được kiểm khuôn trước
    khi vào handler.
    """
    if not name.strip():
        raise BadRequestError("`name` của việc không được để trống")

    def decorate(fn: Callable) -> Callable:
        if blocking:
            if inspect.iscoroutinefunction(fn):
                raise RuntimeError(
                    f"{fn.__name__} khai `blocking=True` thì phải là `def` thường, không "
                    "phải `async def` — cả điểm của nó là chạy ngoài vòng lặp sự kiện."
                )
        elif not inspect.iscoroutinefunction(fn):
            raise RuntimeError(
                f"{fn.__name__} phải là `async def`. Việc nặng chạy đồng bộ thì khai "
                "`@job(..., blocking=True)` để nó chạy trong thread."
            )
        setattr(
            fn,
            _SPEC_ATTR,
            JobSpec(
                name=name.strip(),
                max_retries=max_retries,
                retry_delay=retry_delay,
                blocking=blocking,
            ),
        )
        return fn

    return decorate


def discover_jobs() -> dict[str, JobSpec]:
    """Quét mọi class đã đăng ký để tìm method mang @job."""
    table: dict[str, JobSpec] = {}
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: JobSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is None:
                continue

            params = list(inspect.signature(fn).parameters.values())[1:]
            if len(params) != 1:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký phải là (self, payload)"
                )
            hints = get_type_hints(fn)
            annotation = hints.get(params[0].name)
            model = (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
            if spec.name in table:
                raise RuntimeError(
                    f"Đã có việc tên {spec.name!r} ({table[spec.name].label}). "
                    f"{cls.__name__}.{fn.__name__} sẽ không bao giờ chạy — đổi tên đi."
                )
            table[spec.name] = replace(spec, cls=cls, fn=fn, model=model)
    return table


@injectable
class JobQueue:
    """Chỗ GỬI việc vào. Tiêm nó vào service nào cần chạy nền.

    Tách khỏi `JobRunner` (chỗ chạy việc) vì hai bên có vòng đời khác nhau:
    controller nào cũng gửi được, nhưng chỉ có đúng một chỗ tiêu thụ.
    """

    def __init__(self, settings: Settings) -> None:
        self._config = settings.jobs
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(
            maxsize=max(1, self._config.max_queued)
        )

    async def submit(self, name: str, payload: Any = None, *, wait: bool = False) -> None:
        """Đẩy một việc vào hàng đợi. Trả về ngay, KHÔNG chờ việc chạy xong.

        Hàng đợi đầy thì mặc định ném `ServiceUnavailableError` chứ không chờ.
        Đây là chỗ áp lực ngược lộ ra: hàng đợi đầy nghĩa là bên tiêu thụ chậm
        hơn bên gửi, và giấu điều đó bằng cách chờ chỉ làm request treo theo.

        `wait=True` thì chờ tới khi có chỗ — chỉ dùng khi bên gọi CHẤP NHẬN bị
        chậm lại, ví dụ một vòng nạp dữ liệu chạy nền.
        """
        item = (name, payload)
        if wait:
            await self._queue.put(item)
        else:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                jobs_rejected.inc(job=name)
                raise ServiceUnavailableError(
                    f"Hàng đợi việc đầy ({self._config.max_queued} chỗ) nên không nhận "
                    f"thêm '{name}'. Bên chạy việc đang chậm hơn bên gửi — tăng "
                    "APP_JOBS__WORKERS, hoặc chuyển sang hàng đợi bền (RabbitMQ)."
                ) from None
        jobs_submitted.inc(job=name)
        jobs_queued.inc_gauge(1)

    def depth(self) -> int:
        """Số việc đang nằm chờ. Con số này phình lên là dấu hiệu sớm nhất."""
        return self._queue.qsize()

    @property
    def raw(self) -> asyncio.Queue[tuple[str, Any]]:
        return self._queue


@injectable
class JobRunner:
    """Lấy việc ra khỏi hàng đợi và chạy. Mặc định một việc một lúc."""

    def __init__(self, queue: JobQueue, settings: Settings) -> None:
        self._queue = queue
        self._config = settings.jobs
        self._table: dict[str, JobSpec] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._closing = False

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._table = discover_jobs()
        if not self._table:
            return
        self._closing = False
        count = max(1, self._config.workers)
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"job-worker-{i}") for i in range(count)
        ]
        log.info(
            "jobs.started",
            jobs=sorted(self._table),
            workers=count,
            max_queued=self._config.max_queued,
            hint="việc nằm trong RAM — app tắt là mất phần chưa chạy",
        )

    async def _worker(self, index: int) -> None:
        while not self._closing:
            try:
                name, payload = await self._queue.raw.get()
            except asyncio.CancelledError:
                return
            jobs_queued.inc_gauge(-1)
            try:
                await self._handle(name, payload)
            finally:
                self._queue.raw.task_done()

    async def _handle(self, name: str, payload: Any) -> None:
        spec = self._table.get(name)
        if spec is None:
            log.error(
                "jobs.unknown", job=name, known=sorted(self._table),
                hint="tên gửi vào không khớp @job nào — việc này bị bỏ",
            )
            return

        token = set_request_id(new_request_id())
        started = time.monotonic()
        try:
            for attempt in range(1, spec.max_retries + 2):
                try:
                    async with request_scope():
                        await self._call(spec, payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt > spec.max_retries:
                        jobs_failed.inc(job=name)
                        log.exception("jobs.failed", job=spec.label, attempt=attempt, error=str(exc))
                        return
                    log.warning(
                        "jobs.retrying", job=spec.label, attempt=attempt, error=str(exc)
                    )
                    await asyncio.sleep(spec.retry_delay)
                else:
                    jobs_done.inc(job=name)
                    return
        finally:
            jobs_duration.observe(time.monotonic() - started, job=name)
            reset_request_id(token)

    async def _call(self, spec: JobSpec, payload: Any) -> None:
        if spec.model is not None:
            try:
                payload = spec.model.model_validate(payload)
            except ValidationError as exc:
                raise BadRequestError(f"Payload không hợp lệ: {exc}") from exc

        instance = container.resolve(spec.cls)      # type: ignore[arg-type]
        if spec.blocking:
            # Đẩy sang thread. Có tác dụng vì torch/opencv nhả GIL trong lúc
            # tính; với vòng lặp Python thuần thì không.
            await asyncio.to_thread(spec.fn, instance, payload)     # type: ignore[misc]
        else:
            await spec.fn(instance, payload)                        # type: ignore[misc]

    async def shutdown(self) -> None:
        """Chạy nốt việc đang dở, rồi nói rõ còn bao nhiêu việc bị bỏ."""
        if not self._workers:
            return

        # Dọn TRƯỚC, dừng SAU. Bật cờ dừng trước thì worker thoát ngay và hàng
        # đợi không bao giờ được chạy nốt — đúng lỗi đã mắc một lần.
        if self._queue.depth():
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._queue.raw.join(), timeout=self._config.drain_seconds
                )

        left = self._queue.depth()
        self._closing = True
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        if left:
            # Nói ra bằng con số. Đây là cái giá của hàng đợi trong tiến trình,
            # và giấu nó đi thì người ta chỉ phát hiện khi khách hàng phàn nàn.
            log.warning(
                "jobs.dropped_on_shutdown",
                count=left,
                hint="hàng đợi nằm trong RAM; việc chưa chạy mất khi tiến trình kết thúc",
            )

    def stats(self) -> dict[str, Any]:
        return {
            "jobs": sorted(self._table),
            "workers": len(self._workers),
            "queued": self._queue.depth(),
        }


__all__ = ["JobQueue", "JobRunner", "JobSpec", "discover_jobs", "job"]
