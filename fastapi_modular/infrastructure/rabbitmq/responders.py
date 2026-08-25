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
    ten_pattern = normalize_pattern(pattern)
    if not ten_pattern:
        raise BadRequestError("`pattern` không được để trống")

    kieu, rk, bind_arguments = normalize_binding(
        exchange,
        routing_key if routing_key is not None else "",
        kind=exchange_type,
    )
    # Exchange mặc định giao theo TÊN hàng đợi, nên routing key chính là nó.
    if kieu == "default":
        rk = queue

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(
            fn,
            _SPEC_ATTR,
            RabbitmqResponderSpec(
                pattern=ten_pattern,
                queue=queue,
                exchange=exchange,
                routing_key=rk,
                exchange_type=kieu,
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
    ke_hoach: dict[str, tuple[RabbitmqResponderSpec, dict[str, RabbitmqResponderSpec]]] = {}
    for spec in specs:
        if spec.queue not in ke_hoach:
            ke_hoach[spec.queue] = (spec, {})
        dau, bang = ke_hoach[spec.queue]

        khac = [
            ten
            for ten in ("exchange", "routing_key", "exchange_type", "prefetch",
                        "durable", "auto_delete")
            if getattr(dau, ten) != getattr(spec, ten)
        ]
        if khac:
            raise RuntimeError(
                f"{spec.label} và {dau.label} cùng dùng hàng đợi '{spec.queue}' nhưng khai "
                f"khác nhau ở: {', '.join(khac)}. Một hàng đợi chỉ dựng được một lần — "
                "cho hai bên khai giống hệt nhau, hoặc tách ra hai hàng đợi."
            )
        if spec.pattern in bang:
            raise RuntimeError(
                f"Hàng đợi '{spec.queue}' đã có responder cho pattern '{spec.pattern}' "
                f"({bang[spec.pattern].label}). {spec.label} sẽ không bao giờ được gọi — "
                "đổi pattern, hoặc đổi hàng đợi."
            )
        bang[spec.pattern] = spec
    return ke_hoach


@injectable
class RabbitmqResponderRunner:
    """Dựng hàng đợi và bật consumer cho mọi @rabbitmq_responder tìm được."""

    def __init__(self, broker: RabbitBroker, settings: Settings) -> None:
        self._broker = broker
        self._config = settings.rabbitmq
        self._ke_hoach: dict[str, tuple[RabbitmqResponderSpec, dict[str, RabbitmqResponderSpec]]] = {}
        self._started: dict[str, Any] = {}

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        specs = discover_rabbitmq_responders()
        if not specs:
            return
        self._ke_hoach = plan_queues(specs)

        self._broker.on_ready(self._setup)
        if self._broker.connected:
            await self._setup()

    async def _setup(self) -> None:
        """Idempotent: gọi lại bao nhiêu lần cũng không sinh consumer trùng."""
        for ten_hang_doi, (dau, bang) in self._ke_hoach.items():
            if ten_hang_doi in self._started:
                continue
            try:
                channel = await self._broker.new_channel(prefetch=dau.prefetch)
                queue = await self._broker.durable_queue(
                    channel,
                    ten_hang_doi,
                    durable=dau.durable,
                    auto_delete=dau.auto_delete,
                )
                if dau.exchange_type != "default":
                    await queue.bind(
                        await self._broker.exchange(dau.exchange, dau.exchange_type),
                        routing_key=dau.routing_key,
                        arguments=dau.bind_arguments,
                    )
                tag = await queue.consume(self._lam_callback(ten_hang_doi, bang))
                self._started[ten_hang_doi] = (queue, tag)
                log.info(
                    "mq.responder_started",
                    queue=ten_hang_doi,
                    patterns=sorted(bang),
                    exchange=dau.exchange or "(mặc định)",
                )
            except Exception as exc:
                log.exception(
                    "mq.responder_start_failed", queue=ten_hang_doi, error=str(exc)
                )

    def _lam_callback(
        self, ten_hang_doi: str, bang: dict[str, RabbitmqResponderSpec]
    ) -> Callable:
        async def callback(message: Any) -> None:
            token = set_request_id(new_request_id())
            try:
                async with request_scope():
                    await self._giao(ten_hang_doi, bang, message)
            finally:
                reset_request_id(token)
                # Luôn ack: yêu cầu đã được trả lời (kể cả trả lời là lỗi), và
                # không có ai để giao lại cho — người gọi đã đi rồi.
                await message.ack()

        return callback

    async def _giao(
        self, ten_hang_doi: str, bang: dict[str, RabbitmqResponderSpec], message: Any
    ) -> None:
        goi = read_packet(decode(message.body))
        if goi is None:
            log.warning(
                "mq.responder_bad_packet",
                queue=ten_hang_doi,
                hint="tin không theo khuôn {pattern, data, id} — người gửi có dùng "
                     "emit()/send() hay ClientProxy của NestJS không?",
            )
            return

        pattern, data, ma = goi
        spec = bang.get(pattern)
        if spec is None:
            # Nói thẳng cho người gọi thay vì để họ đợi hết giờ. Dùng NGUYÊN VĂN
            # thông báo của NestJS, vì client NestJS vốn đã biết đọc câu này.
            log.warning("mq.no_responder", queue=ten_hang_doi, pattern=pattern,
                        co=sorted(bang))
            if ma:
                await self._tra_loi(message, ma, loi=NO_MESSAGE_HANDLER)
            return

        try:
            ket_qua = await self._chay(spec, data, message, pattern)
        except Exception as exc:
            rabbitmq_consume_failed.inc(queue=ten_hang_doi)
            log.exception(
                "mq.responder_failed", handler=spec.label, pattern=pattern, error=str(exc)
            )
            if ma:
                await self._tra_loi(message, ma, loi=exc)
            return

        rabbitmq_consumed.inc(queue=ten_hang_doi)
        if ma:
            await self._tra_loi(message, ma, ket_qua=ket_qua)
        elif ket_qua is not None:
            # Tin không có `id` là SỰ KIỆN (NestJS `emit()`): không ai chờ kết
            # quả. Nói ra để không ai ngồi tìm xem câu trả lời đi đâu mất.
            log.debug(
                "mq.responder_result_dropped", handler=spec.label, pattern=pattern
            )

    async def _chay(
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

    async def _tra_loi(
        self,
        message: Any,
        correlation_id: str,
        *,
        ket_qua: Any = None,
        loi: BaseException | str | None = None,
    ) -> None:
        """Gửi câu trả lời về `reply_to`, và nuốt lỗi nếu đường về hỏng.

        Ném tiếp sẽ khiến hạ tầng coi như tin xử lý hỏng — mà việc thì đã làm
        xong rồi. Không đáng làm lại chỉ vì đường về nghẽn.
        """
        dia_chi = message.reply_to
        if not dia_chi:
            log.warning(
                "mq.reply_to_missing",
                hint="tin có `id` nhưng không có `reply_to`; người gọi sẽ đợi tới hết giờ",
            )
            return

        goi = error_packet(correlation_id, loi) if loi is not None else ok_packet(
            correlation_id, ket_qua
        )
        try:
            await self._broker.publish_to_queue(
                dia_chi, encode(goi), persistent=False, correlation_id=correlation_id
            )
        except Exception as exc:  # noqa: BLE001 - việc đã xong, đường về hỏng không làm lại
            log.warning(
                "rpc.reply_failed",
                reply_to=dia_chi,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def shutdown(self) -> None:
        self._started.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "responders": [
                {"queue": ten, "patterns": sorted(bang), "running": ten in self._started}
                for ten, (_, bang) in sorted(self._ke_hoach.items())
            ]
        }
