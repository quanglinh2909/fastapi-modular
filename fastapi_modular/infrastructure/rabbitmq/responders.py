"""`@rabbitmq_responder` — bên TRẢ LỜI, tương đương `@MessagePattern` của NestJS.

Khác `@rabbitmq_subscriber` ở ba chỗ, và cả ba đều là hệ quả của một sự thật
duy nhất: **có người đang đứng chờ câu trả lời**.

    @rabbitmq_subscriber            @rabbitmq_responder
    ------------------------------  ------------------------------------------
    chọn handler theo ROUTING KEY   chọn handler theo PATTERN nằm trong tin
    trả về gì cũng bị bỏ            giá trị trả về ĐƯỢC GỬI NGƯỢC LẠI
    hỏng -> thử lại / vào .dlq      hỏng -> báo ngay cho người gọi, không thử lại

Vì sao không thử lại: người gọi chỉ chờ vài giây rồi bỏ. Thử lại sau khi họ đã
bỏ cuộc là làm một việc không ai đọc kết quả — tệ hơn nữa nếu việc đó ghi dữ
liệu. Hỏng thì nói ngay để họ còn xử lý.

Nhiều responder DÙNG CHUNG một hàng đợi, phân biệt nhau bằng pattern — đúng mô
hình của NestJS, nơi một microservice nghe đúng một hàng đợi và tự phân việc
theo `pattern` bên trong tin.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import _REGISTRY, container, injectable, request_scope
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.rpc import (
    NO_MESSAGE_HANDLER,
    decode,
    encode,
    error_packet,
    normalize_pattern,
    ok_packet,
    read_packet,
)
from fastapi_modular.infrastructure.rabbitmq.broker import DEFAULT_PREFETCH, RabbitBroker
from fastapi_modular.infrastructure.rabbitmq.metrics import (
    rabbitmq_consume_failed,
    rabbitmq_consumed,
)
from fastapi_modular.infrastructure.rabbitmq.patterns import ExchangeKind, normalize_binding

log = get_logger(__name__)

_SPEC_ATTR = "__rabbitmq_responder__"


@dataclass(slots=True)
class RabbitmqResponderSpec:
    pattern: str
    queue: str
    exchange: str = ""
    routing_key: str = ""
    exchange_type: str = "default"
    bind_arguments: dict[str, Any] | None = None
    prefetch: int = DEFAULT_PREFETCH
    durable: bool = True
    auto_delete: bool = False
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        if self.cls and self.fn:
            return f"{self.cls.__name__}.{self.fn.__name__}"
        return f"{self.queue}:{self.pattern}"


def rabbitmq_responder(
    pattern: Any,
    *,
    queue: str,
    exchange: str = "",
    routing_key: str | None = None,
    exchange_type: ExchangeKind | None = None,
    prefetch: int = DEFAULT_PREFETCH,
    durable: bool = True,
    auto_delete: bool = False,
) -> Callable[[Callable], Callable]:
    """Gắn method vào một `pattern`; giá trị nó trả về được gửi ngược cho người gọi.

        pattern    chuỗi ("sum") hoặc dict ({"cmd": "sum"}) — giống NestJS.
                   Dict được chuỗi hoá đúng luật của NestJS, kể cả thứ tự khoá.
        queue      hàng đợi nhận yêu cầu. NHIỀU responder dùng chung một hàng
                   đợi là chuyện bình thường và đúng mô hình NestJS.
        exchange   để trống = exchange mặc định, tức là người gọi gửi thẳng vào
                   hàng đợi theo tên (`send(..., queue="...")`, và cũng là cách
                   `ClientRMQ` của NestJS gửi). Khai exchange thì định tuyến
                   theo kiểu AMQP như thường.

    Handler nhận `(self, data)` hoặc `(self, data, meta)` và **trả về** kết quả:

        @rabbitmq_responder("sum", queue="math")
        async def cong(self, data: list[int]) -> int:
            return sum(data)

    Tham số đầu chú kiểu bằng một model Pydantic thì payload được kiểm khuôn
    trước; sai khuôn thì người gọi nhận đúng lỗi đó thay vì phải đợi hết giờ.

    KHÔNG có `max_retries`/`dead_letter` ở đây, và đó là cố ý — xem docstring
    của module.
    """
    pattern_name = normalize_pattern(pattern)
    if not pattern_name:
        raise BadRequestError("`pattern` không được để trống")

    kind, rk, bind_arguments = normalize_binding(
        exchange,
        routing_key if routing_key is not None else "",
        kind=exchange_type,
    )
    # Exchange mặc định giao theo TÊN hàng đợi, nên routing key chính là nó.
    if kind == "default":
        rk = queue

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(
            fn,
            _SPEC_ATTR,
            RabbitmqResponderSpec(
                pattern=pattern_name,
                queue=queue,
                exchange=exchange,
                routing_key=rk,
                exchange_type=kind,
                bind_arguments=bind_arguments,
                prefetch=prefetch,
                durable=durable,
                auto_delete=auto_delete,
            ),
        )
        return fn

    return decorate


def discover_rabbitmq_responders() -> list[RabbitmqResponderSpec]:
    """Quét mọi class đã đăng ký để tìm method mang @rabbitmq_responder."""
    found: list[RabbitmqResponderSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: RabbitmqResponderSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is None:
                continue

            params = list(inspect.signature(fn).parameters.values())[1:]
            if not params or len(params) > 2:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký phải là "
                    "(self, data) hoặc (self, data, meta)"
                )

            hints = get_type_hints(fn)
            annotation = hints.get(params[0].name)
            model = (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
            found.append(
                replace(spec, cls=cls, fn=fn, model=model, wants_meta=len(params) == 2)
            )
    return sorted(found, key=lambda s: (s.queue, s.pattern))


def plan_queues(
    specs: list[RabbitmqResponderSpec],
) -> dict[str, tuple[RabbitmqResponderSpec, dict[str, RabbitmqResponderSpec]]]:
    """Gom responder theo hàng đợi: `queue -> (thiết lập, {pattern: spec})`.

    Hai chỗ phải chặn ở đây, vì broker sẽ không chặn hộ:

    1. Hai responder cùng hàng đợi mà khai thiết lập khác nhau (durable, bind…):
       chỉ cái nào dựng trước có tác dụng, cái sau im lặng bị bỏ qua.
    2. Hai responder cùng hàng đợi và TRÙNG pattern: một trong hai sẽ không bao
       giờ được gọi, và không có gì báo cho biết là cái nào.
    """
    plan: dict[str, tuple[RabbitmqResponderSpec, dict[str, RabbitmqResponderSpec]]] = {}
    for spec in specs:
        if spec.queue not in plan:
            plan[spec.queue] = (spec, {})
        first, table = plan[spec.queue]

        different = [
            field
            for field in ("exchange", "routing_key", "exchange_type", "prefetch",
                        "durable", "auto_delete")
            if getattr(first, field) != getattr(spec, field)
        ]
        if different:
            raise RuntimeError(
                f"{spec.label} và {first.label} cùng dùng hàng đợi '{spec.queue}' nhưng khai "
                f"khác nhau ở: {', '.join(different)}. Một hàng đợi chỉ dựng được một lần — "
                "cho hai bên khai giống hệt nhau, hoặc tách ra hai hàng đợi."
            )
        if spec.pattern in table:
            raise RuntimeError(
                f"Hàng đợi '{spec.queue}' đã có responder cho pattern '{spec.pattern}' "
                f"({table[spec.pattern].label}). {spec.label} sẽ không bao giờ được gọi — "
                "đổi pattern, hoặc đổi hàng đợi."
            )
        table[spec.pattern] = spec
    return plan


@injectable
class RabbitmqResponderRunner:
    """Dựng hàng đợi và bật consumer cho mọi @rabbitmq_responder tìm được."""

    def __init__(self, broker: RabbitBroker, settings: Settings) -> None:
        self._broker = broker
        self._config = settings.rabbitmq
        self._plan: dict[str, tuple[RabbitmqResponderSpec, dict[str, RabbitmqResponderSpec]]] = {}
        self._started: dict[str, Any] = {}

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        specs = discover_rabbitmq_responders()
        if not specs:
            return
        self._plan = plan_queues(specs)

        self._broker.on_ready(self._setup)
        if self._broker.connected:
            await self._setup()

    async def _setup(self) -> None:
        """Idempotent: gọi lại bao nhiêu lần cũng không sinh consumer trùng."""
        for queue_name, (first, table) in self._plan.items():
            if queue_name in self._started:
                continue
            try:
                channel = await self._broker.new_channel(prefetch=first.prefetch)
                queue = await self._broker.durable_queue(
                    channel,
                    queue_name,
                    durable=first.durable,
                    auto_delete=first.auto_delete,
                )
                if first.exchange_type != "default":
                    await queue.bind(
                        await self._broker.exchange(first.exchange, first.exchange_type),
                        routing_key=first.routing_key,
                        arguments=first.bind_arguments,
                    )
                tag = await queue.consume(self._make_responder_callback(queue_name, table))
                self._started[queue_name] = (queue, tag)
                log.info(
                    "mq.responder_started",
                    queue=queue_name,
                    patterns=sorted(table),
                    exchange=first.exchange or "(mặc định)",
                )
            except Exception as exc:
                log.exception(
                    "mq.responder_start_failed", queue=queue_name, error=str(exc)
                )

    def _make_responder_callback(
        self, queue_name: str, table: dict[str, RabbitmqResponderSpec]
    ) -> Callable:
        async def callback(message: Any) -> None:
            token = set_request_id(new_request_id())
            try:
                async with request_scope():
                    await self._dispatch(queue_name, table, message)
            finally:
                reset_request_id(token)
                # Luôn ack: yêu cầu đã được trả lời (kể cả trả lời là lỗi), và
                # không có ai để giao lại cho — người gọi đã đi rồi.
                await message.ack()

        return callback

    async def _dispatch(
        self, queue_name: str, table: dict[str, RabbitmqResponderSpec], message: Any
    ) -> None:
        packet = read_packet(decode(message.body))
        if packet is None:
            log.warning(
                "mq.responder_bad_packet",
                queue=queue_name,
                hint="tin không theo khuôn {pattern, data, id} — người gửi có dùng "
                     "emit()/send() hay ClientProxy của NestJS không?",
            )
            return

        pattern, data, correlation_id = packet
        spec = table.get(pattern)
        if spec is None:
            # Nói thẳng cho người gọi thay vì để họ đợi hết giờ. Dùng NGUYÊN VĂN
            # thông báo của NestJS, vì client NestJS vốn đã biết đọc câu này.
            log.warning("mq.no_responder", queue=queue_name, pattern=pattern,
                        has=sorted(table))
            if correlation_id:
                await self._reply(message, correlation_id, error=NO_MESSAGE_HANDLER)
            return

        try:
            result = await self._run(spec, data, message, pattern)
        except Exception as exc:
            rabbitmq_consume_failed.inc(queue=queue_name)
            log.exception(
                "mq.responder_failed", handler=spec.label, pattern=pattern, error=str(exc)
            )
            if correlation_id:
                await self._reply(message, correlation_id, error=exc)
            return

        rabbitmq_consumed.inc(queue=queue_name)
        if correlation_id:
            await self._reply(message, correlation_id, result=result)
        elif result is not None:
            # Tin không có `id` là SỰ KIỆN (NestJS `emit()`): không ai chờ kết
            # quả. Nói ra để không ai ngồi tìm xem câu trả lời đi đâu mất.
            log.debug(
                "mq.responder_result_dropped", handler=spec.label, pattern=pattern
            )

    async def _run(
        self, spec: RabbitmqResponderSpec, data: Any, message: Any, pattern: str
    ) -> Any:
        if spec.model is not None:
            try:
                data = spec.model.model_validate(data)
            except ValidationError as exc:
                raise BadRequestError(f"Payload không hợp lệ: {exc}") from exc

        instance = container.resolve(spec.cls)      # type: ignore[arg-type]
        if spec.wants_meta:
            meta = {
                "pattern": pattern,
                "queue": spec.queue,
                "message_id": message.message_id,
                "correlation_id": message.correlation_id,
                "reply_to": message.reply_to,
            }
            return await spec.fn(instance, data, meta)   # type: ignore[misc]
        return await spec.fn(instance, data)             # type: ignore[misc]

    async def _reply(
        self,
        message: Any,
        correlation_id: str,
        *,
        result: Any = None,
        error: BaseException | str | None = None,
    ) -> None:
        """Gửi câu trả lời về `reply_to`, và nuốt lỗi nếu đường về hỏng.

        Ném tiếp sẽ khiến hạ tầng coi như tin xử lý hỏng — mà việc thì đã làm
        xong rồi. Không đáng làm lại chỉ vì đường về nghẽn.
        """
        address = message.reply_to
        if not address:
            log.warning(
                "mq.reply_to_missing",
                hint="tin có `id` nhưng không có `reply_to`; người gọi sẽ đợi tới hết giờ",
            )
            return

        packet = error_packet(correlation_id, error) if error is not None else ok_packet(
            correlation_id, result
        )
        try:
            await self._broker.publish_to_queue(
                address, encode(packet), persistent=False, correlation_id=correlation_id
            )
        except Exception as exc:  # noqa: BLE001 - việc đã xong, đường về hỏng không làm lại
            log.warning(
                "rpc.reply_failed",
                reply_to=address,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def shutdown(self) -> None:
        self._started.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "responders": [
                {"queue": field, "patterns": sorted(table), "running": field in self._started}
                for field, (_, table) in sorted(self._plan.items())
            ]
        }
