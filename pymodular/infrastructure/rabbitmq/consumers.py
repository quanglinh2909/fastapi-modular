"""Consumer nền — tương đương `@EventPattern` của NestJS microservices.

    @injectable
    class AlertConsumer:
        def __init__(self, service: AlertService) -> None:
            self._service = service

        @rabbitmq_subscriber("events", "alert.created", queue="alert-mailer")
        async def gui_mail(self, payload: AlertCreated) -> None:
            await self._service.notify(payload.id)

Hàng đợi ở đây BỀN và có TÊN, nên nhiều worker CHIA NHAU xử lý — mỗi tin đúng
một worker làm. Đó là ngữ nghĩa đúng cho việc phải làm một lần: gửi mail, ghi
sổ, gọi dịch vụ ngoài.

Cần ngược lại (MỌI worker đều nhận một bản sao) thì đừng dùng `@rabbitmq_subscriber`;
tự mở hàng đợi bằng `broker.worker_queue(...)`. Đảo hai thứ này là lỗi kinh
điển: hàng đợi riêng cho việc làm-một-lần thì mỗi tin bị xử lý N lần.

Mặc định mọc ra ĐÚNG MỘT hàng đợi. Handler ném lỗi thì tin bị bỏ và có log —
không thử lại, không giữ lại. Muốn chắc hơn thì tự bật:

    @rabbitmq_subscriber(..., max_retries=3)                     # thêm <queue>.retry
    @rabbitmq_subscriber(..., max_retries=3, dead_letter=True)   # thêm cả <queue>.dlq

Khi đã bật, handler ném lỗi sẽ đi đường này:

    lần 1..N   -> đẩy sang `<queue>.retry` (hàng đợi có TTL, hết hạn thì tin tự
                  quay về hàng đợi chính) rồi ack bản gốc
    quá N lần  -> reject; có dead_letter thì tin rơi vào `<queue>.dlq`, không thì bỏ

Cố ý KHÔNG dùng `requeue=True`: tin hỏng vĩnh viễn (dữ liệu sai, bug) sẽ quay
vòng liên tục, ăn hết CPU và che lấp mọi tin khác. Đó là cách phổ biến nhất để
làm sập một hệ thống hàng đợi.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from pymodular.core.config import Settings
from pymodular.core.container import _REGISTRY, container, injectable, request_scope
from pymodular.core.context import new_request_id, reset_request_id, set_request_id
from pymodular.core.logging import get_logger
from pymodular.infrastructure.rabbitmq.broker import DEFAULT_PREFETCH, RabbitBroker
from pymodular.infrastructure.rabbitmq.metrics import (
    rabbitmq_consume_failed,
    rabbitmq_consumed,
    rabbitmq_dead_lettered,
    rabbitmq_retried,
)
from pymodular.infrastructure.rabbitmq.patterns import validate_pattern

log = get_logger(__name__)

_SPEC_ATTR = "__rabbitmq_subscriber__"
ATTEMPT_HEADER = "x-attempt"


class PermanentMessageError(Exception):
    """Tin này sai vĩnh viễn — thử lại vô ích, cho đi thẳng vào DLQ.

    Ném từ handler khi biết chắc thử lại không giúp gì: payload sai khuôn,
    tham chiếu tới bản ghi đã bị xoá, phiên bản sự kiện không hỗ trợ.
    """


@dataclass(slots=True)
class RabbitmqSpec:
    exchange: str
    routing_key: str
    queue: str
    max_retries: int = 0
    retry_delay: float = 10.0
    dead_letter: bool = False
    durable: bool = True
    auto_delete: bool = False
    prefetch: int = DEFAULT_PREFETCH
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.queue


def rabbitmq_subscriber(
    exchange: str,
    routing_key: str,
    *,
    queue: str,
    max_retries: int = 0,
    retry_delay: float = 10.0,
    dead_letter: bool = False,
    durable: bool = True,
    auto_delete: bool = False,
    prefetch: int = DEFAULT_PREFETCH,
) -> Callable[[Callable], Callable]:
    """Gắn method vào một hàng đợi bền nghe `routing_key` trên `exchange`.

    `queue` là BẮT BUỘC và cố ý không tự sinh: tên hàng đợi là danh tính của
    nhóm consumer. Tên tự sinh sẽ đổi sau mỗi lần deploy, khiến hàng đợi cũ ở
    lại broker và tin đọng trong đó vĩnh viễn.

    Mặc định mọc ra ĐÚNG MỘT hàng đợi trên broker, không có gì thêm. Handler ném
    lỗi thì tin bị bỏ, kèm log `mq.message_dropped`.

    Hai hàng đợi phụ chỉ xuất hiện khi bạn tự bật:

        max_retries=3     -> thêm <queue>.retry  (chỗ tin nằm chờ giữa hai lần thử)
        dead_letter=True  -> thêm <queue>.dlq    (chỗ tin nằm lại sau khi bỏ cuộc)

    Bật khi tin đáng tiền: đơn hàng, thanh toán, gửi mail. Để nguyên mặc định
    cho loại mất cũng không sao: số đo, nhịp tim, log.

    Mọi tham số ở đây là quyết định của RIÊNG consumer này, nên khai ngay tại
    chỗ chứ không phải trong .env — "gửi mail thử lại 5 lần, cách nhau 60 giây"
    và "ghi log không thử lại" là hai câu chuyện khác nhau, một biến môi trường
    chung không nói được cả hai:

        max_retries   số lần thử lại; 0 (mặc định) = hỏng là bỏ ngay
        retry_delay   chờ bao lâu giữa các lần (giây); chỉ dùng khi max_retries > 0
        dead_letter   True = giữ tin hỏng lại ở <queue>.dlq để xem
        durable       hàng đợi sống sót qua restart broker
        auto_delete   xoá hàng đợi khi consumer cuối cùng ngắt
        prefetch      số tin nhận trước khi ack — handler chậm thì để nhỏ

    `auto_delete` mặc định False, tức GIỮ LẠI hàng đợi khi app tắt. Đó là điều
    người ta muốn gần như mọi lúc: deploy, restart, app chết — tin gửi trong lúc
    đó vẫn nằm ở broker, app lên là xử lý tiếp. Đặt True thì broker xoá hàng đợi
    ngay khi consumer cuối cùng rời đi, kèm mọi tin còn nằm trong đó; sau đó tin
    nào khớp routing_key cũng rơi vào hư không cho tới lần khởi động sau.

    Chỉ hợp lý cho tin chỉ có giá trị lúc này: theo dõi trực tiếp, đo đạc, log
    tạm. Thường đi cùng `durable=False`.

    Dọn dẹp là TRỌN GÓI: `<queue>.retry` và `<queue>.dlq` cũng bị xoá theo. Bản
    thân `auto_delete` của AMQP không làm nổi việc đó — nó chỉ kích hoạt khi
    consumer CUỐI CÙNG rời đi, mà hai hàng đợi phụ thì chẳng có ai nghe bao giờ,
    nên chúng sẽ nằm lại broker vĩnh viễn. Khung tự xoá chúng lúc tắt, sau khi
    xác nhận hàng đợi chính đã biến mất (tức không còn worker nào khác đang
    nghe) — xem `RabbitmqRunner._don_hang_doi_phu`.
    """
    validate_pattern(routing_key)

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(
            fn,
            _SPEC_ATTR,
            RabbitmqSpec(
                exchange=exchange,
                routing_key=routing_key,
                queue=queue,
                max_retries=max_retries,
                retry_delay=retry_delay,
                dead_letter=dead_letter,
                durable=durable,
                auto_delete=auto_delete,
                prefetch=prefetch,
            ),
        )
        return fn

    return decorate


def discover_rabbitmq_subscribers() -> list[RabbitmqSpec]:
    """Quét mọi provider đã đăng ký để tìm method mang @rabbitmq_subscriber.

    Không cần decorator riêng ở cấp class: bất kỳ class @injectable nào cũng
    chứa consumer được, giống như service thường.
    """
    found: list[RabbitmqSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: RabbitmqSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is None:
                continue

            params = list(inspect.signature(fn).parameters.values())[1:]
            if not params or len(params) > 2:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký phải là "
                    "(self, payload) hoặc (self, payload, meta)"
                )

            hints = get_type_hints(fn)
            annotation = hints.get(params[0].name)
            model = (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
            found.append(
                RabbitmqSpec(
                    exchange=spec.exchange,
                    routing_key=spec.routing_key,
                    queue=spec.queue,
                    max_retries=spec.max_retries,
                    retry_delay=spec.retry_delay,
                    dead_letter=spec.dead_letter,
                    durable=spec.durable,
                    auto_delete=spec.auto_delete,
                    prefetch=spec.prefetch,
                    cls=cls,
                    fn=fn,
                    model=model,
                    wants_meta=len(params) == 2,
                )
            )
    return sorted(found, key=lambda s: (s.queue, s.routing_key))


@injectable
class RabbitmqRunner:
    """Dựng hàng đợi và bật consumer cho mọi @rabbitmq_subscriber tìm được."""

    def __init__(self, broker: RabbitBroker, settings: Settings) -> None:
        self._broker = broker
        self._config = settings.rabbitmq
        self._specs: list[RabbitmqSpec] = []
        self._started: dict[str, Any] = {}     # queue -> (queue, consumer tag, spec)

    async def startup(self) -> None:
        if not self._config.enabled:
            return

        self._specs = discover_rabbitmq_subscribers()
        if not self._specs:
            return

        # Chạy lại sau MỖI lần kết nối được — kể cả lần đầu tiên xảy ra muộn vì
        # broker chưa lên lúc app khởi động.
        self._broker.on_ready(self._setup)
        if self._broker.connected:
            await self._setup()

    async def _setup(self) -> None:
        """Idempotent: gọi lại bao nhiêu lần cũng không sinh consumer trùng."""
        for spec in self._specs:
            if spec.queue in self._started:
                # aio-pika đã tự khôi phục hàng đợi và consumer sau khi nối
                # lại. Gọi consume() lần nữa sẽ thành hai consumer trên cùng
                # hàng đợi, tức mỗi tin xử lý hai lần.
                continue
            try:
                # Kênh riêng cho từng consumer: khai báo hỏng (hàng đợi đã tồn
                # tại với tham số khác) làm RabbitMQ đóng cả kênh, dùng chung
                # thì một consumer sai sẽ giết mọi consumer còn lại.
                channel = await self._broker.new_channel(prefetch=spec.prefetch)
                queue = await self._broker.durable_queue(
                    channel,
                    spec.queue,
                    durable=spec.durable,
                    dead_letter=spec.dead_letter,
                    auto_delete=spec.auto_delete,
                )
                # Chỉ tạo hàng đợi chờ khi thật sự có thử lại. Không kiểm tra
                # thì broker mọc thêm một hàng đợi không bao giờ có tin nào.
                if spec.max_retries > 0:
                    await self._broker.retry_queue(
                        channel, f"{spec.queue}.retry", spec.queue, durable=spec.durable
                    )
                await queue.bind(
                    await self._broker.exchange(spec.exchange), routing_key=spec.routing_key
                )
                tag = await queue.consume(self._make_callback(spec))
                self._started[spec.queue] = (queue, tag, spec)
                log.info(
                    "mq.consumer_started",
                    handler=spec.label,
                    queue=spec.queue,
                    exchange=spec.exchange,
                    routing_key=spec.routing_key,
                    retries=spec.max_retries,
                    dead_letter=spec.dead_letter,
                )
            except Exception as exc:
                # Một consumer hỏng không được chặn các consumer còn lại.
                log.exception("mq.consumer_start_failed", handler=spec.label, error=str(exc))

    def _make_callback(self, spec: RabbitmqSpec) -> Callable:
        max_retries = spec.max_retries

        async def callback(message: Any) -> None:
            attempt = int((message.headers or {}).get(ATTEMPT_HEADER, 0)) + 1
            token = set_request_id(new_request_id())
            try:
                async with request_scope():
                    await self._invoke(spec, message, attempt)
            except Exception as exc:
                # Bắt mọi lỗi: quyết định thử lại hay cho vào DLQ nằm ngay dưới.
                rabbitmq_consume_failed.inc(queue=spec.queue)
                log.exception(
                    "mq.handler_failed",
                    handler=spec.label,
                    queue=spec.queue,
                    routing_key=message.routing_key,
                    attempt=attempt,
                    error=str(exc),
                )
                await self._on_failure(spec, message, attempt, max_retries, exc)
            else:
                await message.ack()
                rabbitmq_consumed.inc(queue=spec.queue)
            finally:
                reset_request_id(token)

        return callback

    async def _invoke(self, spec: RabbitmqSpec, message: Any, attempt: int) -> None:
        payload: Any = json.loads(message.body)
        if spec.model is not None:
            try:
                payload = spec.model.model_validate(payload)
            except ValidationError as exc:
                # Sai khuôn thì thử lại bao nhiêu lần cũng vẫn sai; ném lỗi để
                # tin đi thẳng vào DLQ ở lần đầu.
                raise PermanentMessageError(f"Payload không hợp lệ: {exc}") from exc

        instance = container.resolve(spec.cls)   # type: ignore[arg-type]
        if spec.wants_meta:
            meta = {
                "exchange": spec.exchange,
                "routing_key": message.routing_key,
                "message_id": message.message_id,
                "attempt": attempt,
                "redelivered": bool(message.redelivered),
            }
            await spec.fn(instance, payload, meta)   # type: ignore[misc]
        else:
            await spec.fn(instance, payload)         # type: ignore[misc]

    async def _on_failure(
        self,
        spec: RabbitmqSpec,
        message: Any,
        attempt: int,
        max_retries: int,
        error: BaseException,
    ) -> None:
        # Lỗi vĩnh viễn thì bỏ qua mọi lần thử còn lại — thử lại chỉ tốn thời
        # gian và làm nhiễu log.
        if isinstance(error, PermanentMessageError) or attempt > max_retries:
            # reject(requeue=False): có dead_letter thì RabbitMQ đẩy sang dlx ->
            # <queue>.dlq; không có thì tin bị vứt bỏ hẳn.
            await message.reject(requeue=False)
            rabbitmq_dead_lettered.inc(queue=spec.queue)
            if spec.dead_letter:
                log.error(
                    "mq.dead_lettered",
                    handler=spec.label,
                    queue=f"{spec.queue}.dlq",
                    attempt=attempt,
                )
            else:
                log.error(
                    "mq.message_dropped",
                    handler=spec.label,
                    attempt=attempt,
                    hint="tin bị bỏ, không lưu lại. Thêm max_retries=... để thử lại, "
                    "dead_letter=True để giữ tin ở <queue>.dlq",
                )
            return

        try:
            await self._broker.publish_to_queue(
                f"{spec.queue}.retry",
                message.body,
                headers={**(message.headers or {}), ATTEMPT_HEADER: attempt},
                expiration=spec.retry_delay,
                persistent=spec.durable,
            )
        except Exception as exc:  # noqa: BLE001 - không hẹn lại được thì để RabbitMQ giao lại
            log.warning("mq.retry_publish_failed", handler=spec.label, error=str(exc))
            await message.nack(requeue=True)
            return

        await message.ack()
        rabbitmq_retried.inc(queue=spec.queue)
        log.warning(
            "mq.retry_scheduled",
            handler=spec.label,
            attempt=attempt,
            delay=spec.retry_delay,
        )

    async def shutdown(self) -> None:
        for queue_name, (queue, tag, spec) in list(self._started.items()):
            if not self._broker.connected:
                break
            try:
                await queue.cancel(tag)
            except Exception as exc:  # noqa: BLE001 - đang tắt
                log.debug("mq.consumer_cancel_failed", queue=queue_name, error=str(exc))
                continue
            if spec.auto_delete:
                await self._don_hang_doi_phu(spec)
        self._started.clear()

    async def _don_hang_doi_phu(self, spec: RabbitmqSpec) -> None:
        """Xoá `<queue>.retry` và `<queue>.dlq` khi hàng đợi chính đã tự xoá.

        Phải hỏi lại broker chứ không suy đoán: nhiều worker cùng nghe một hàng
        đợi thì `auto_delete` chỉ kích hoạt lúc worker CUỐI CÙNG ngắt. Worker
        đầu tiên tắt mà đã dọn thì nó cướp mất hàng đợi thử lại của những worker
        còn đang chạy — tin lỗi của họ sẽ đi vào hư không (exchange mặc định
        không tìm thấy hàng đợi thì bỏ tin, không báo gì).
        """
        if await self._broker.queue_exists(spec.queue):
            log.debug("mq.auto_delete_hoan", queue=spec.queue, hint="còn worker khác đang nghe")
            return

        phu = [f"{spec.queue}.retry"] if spec.max_retries > 0 else []
        if spec.dead_letter:
            phu.append(f"{spec.queue}.dlq")
        for ten in phu:
            # if_unused=True: hàng đợi phụ vốn không ai nghe, nhưng để broker
            # tự chốt vẫn hơn là tự tin.
            if await self._broker.delete_queue(ten, if_unused=True):
                log.info("mq.queue_deleted", queue=ten, handler=spec.label)

    def stats(self) -> dict[str, Any]:
        return {
            "consumers": [
                {
                    "handler": spec.label,
                    "queue": spec.queue,
                    "exchange": spec.exchange,
                    "routing_key": spec.routing_key,
                    "retries": spec.max_retries,
                    "retry_delay": spec.retry_delay,
                    "dead_letter": spec.dead_letter,
                    "durable": spec.durable,
                    "auto_delete": spec.auto_delete,
                    "running": spec.queue in self._started,
                }
                for spec in self._specs
            ]
        }
