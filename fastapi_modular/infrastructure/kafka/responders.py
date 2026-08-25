"""`@kafka_responder` — bên TRẢ LỜI trên Kafka, tương đương `@MessagePattern`.

Kafka là hạ tầng khác biệt nhất trong bốn cái, và NestJS cũng đối xử với nó
khác hẳn:

- **Không có gói `{pattern, data, id}`.** Topic CHÍNH LÀ pattern, và `value` là
  data thô. Mã đối chiếu đi trong header `kafka_correlationId`, địa chỉ trả lời
  trong header `kafka_replyTopic`.
- **Phân biệt yêu cầu với sự kiện bằng HEADER**, không phải bằng `id`: thiếu
  một trong hai header trên là sự kiện, không phải trả lời.
- **Topic trả lời `<pattern>.reply` phải tồn tại**. Kafka không tự sinh chỗ
  chứa như hàng đợi tạm của RabbitMQ.

Và một điều nên cân nhắc trước khi dùng: Kafka là **nhật ký để đọc lại**, không
phải đường gọi hàm. Một lượt đi-về ở đây tính bằng chục mili-giây trở lên, so
với vài mili-giây của RabbitMQ. Cần gọi rồi chờ thì RabbitMQ gần như luôn là
lựa chọn đúng hơn; Kafka để dành cho luồng sự kiện.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    KAFKA_CORRELATION_ID,
    KAFKA_NEST_ERR,
    KAFKA_NEST_IS_DISPOSED,
    KAFKA_REPLY_TOPIC,
    NO_MESSAGE_HANDLER,
    decode,
    encode,
    normalize_pattern,
)
from fastapi_modular.infrastructure.kafka.broker import KafkaBroker, _require_aiokafka
from fastapi_modular.infrastructure.kafka.metrics import kafka_consume_failed, kafka_consumed

log = get_logger(__name__)

_SPEC_ATTR = "__kafka_responder__"


@dataclass(slots=True)
class KafkaResponderSpec:
    pattern: str
    group: str
    auto_offset_reset: str = "latest"
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.pattern


def kafka_responder(
    pattern: Any, *, group: str, auto_offset_reset: str = "latest"
) -> Callable[[Callable], Callable]:
    """Gắn method vào một topic; giá trị trả về được gửi về `<topic>.reply`.

        @kafka_responder("tinh-diem", group="scoring")
        async def tinh(self, data: dict) -> int:
            return data["a"] + data["b"]

    `group` BẮT BUỘC như `@kafka_subscriber`: nhiều worker cùng group thì CHIA
    NHAU phân vùng, nên mỗi yêu cầu chỉ một worker trả lời. Khác group thì cả
    hai cùng trả lời MỘT yêu cầu, và người gọi nhận câu đến trước — gần như
    luôn là nhầm lẫn.

    `auto_offset_reset` mặc định `latest`: nhóm mới chỉ xử lý yêu cầu phát sinh
    từ giờ. Để `earliest` thì lúc khởi động nó sẽ đọc lại và trả lời cả những
    yêu cầu cũ trong nhật ký — mà người gọi thì đã bỏ đi từ lâu.
    """
    topic_name = normalize_pattern(pattern)
    if not topic_name:
        raise BadRequestError("`pattern` không được để trống")
    if not group:
        raise ValueError("kafka_responder cần `group` — xem docstring")
    if auto_offset_reset not in ("latest", "earliest"):
        raise ValueError("auto_offset_reset phải là 'latest' hoặc 'earliest'")

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(
            fn,
            _SPEC_ATTR,
            KafkaResponderSpec(pattern=topic_name, group=group, auto_offset_reset=auto_offset_reset),
        )
        return fn

    return decorate


def discover_kafka_responders() -> list[KafkaResponderSpec]:
    found: list[KafkaResponderSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: KafkaResponderSpec | None = getattr(fn, _SPEC_ATTR, None)
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
            found.append(replace(spec, cls=cls, fn=fn, model=model, wants_meta=len(params) == 2))

    table: dict[str, KafkaResponderSpec] = {}
    for spec in found:
        if spec.pattern in table:
            raise RuntimeError(
                f"Đã có responder cho topic '{spec.pattern}' ({table[spec.pattern].label}). "
                f"{spec.label} sẽ không bao giờ được gọi — đổi topic đi."
            )
        table[spec.pattern] = spec
    return sorted(found, key=lambda s: s.pattern)


def read_rpc_headers(record: Any) -> tuple[str | None, str | None]:
    """Lấy `(mã đối chiếu, topic trả lời)` từ header của một bản ghi Kafka.

    Thiếu MỘT trong hai là sự kiện chứ không phải yêu cầu — đúng luật của
    NestJS (`server-kafka.handleMessage`), và cũng là luật duy nhất hợp lý:
    không biết trả về đâu thì không thể trả lời.
    """
    headers = {k: v for k, v in (record.headers or ())}

    def get(header: str) -> str | None:
        value = headers.get(header)
        if value is None:
            return None
        return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)

    return get(KAFKA_CORRELATION_ID), get(KAFKA_REPLY_TOPIC)


async def send_rpc_reply(
    producer: Any,
    *,
    topic: str,
    correlation_id: str,
    result: Any = None,
    error: BaseException | str | None = None,
) -> None:
    """Gửi câu trả lời theo đúng khuôn Kafka của NestJS.

    Khác ba hạ tầng kia: `value` LÀ câu trả lời, không bọc `{response, ...}`.
    Trạng thái đi hết trong header.
    """
    headers: list[tuple[str, bytes]] = [(KAFKA_CORRELATION_ID, correlation_id.encode())]
    if error is not None:
        description = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        headers.append((KAFKA_NEST_ERR, description.encode()))
        body = b"null"
    else:
        body = encode(result)
    # Một byte 0x00, đúng như `Buffer.alloc(1)` của NestJS: đây là CỜ, giá trị
    # không có ý nghĩa gì, chỉ sự có mặt của header mới có.
    headers.append((KAFKA_NEST_IS_DISPOSED, b"\x00"))
    await producer.send_and_wait(topic, value=body, headers=headers)


async def run_responder(spec: KafkaResponderSpec, data: Any, meta: dict[str, Any]) -> Any:
    if spec.model is not None:
        try:
            data = spec.model.model_validate(data)
        except ValidationError as exc:
            raise BadRequestError(f"Payload không hợp lệ: {exc}") from exc
    instance = container.resolve(spec.cls)      # type: ignore[arg-type]
    if spec.wants_meta:
        return await spec.fn(instance, data, meta)   # type: ignore[misc]
    return await spec.fn(instance, data)             # type: ignore[misc]


__all__ = [
    "NO_MESSAGE_HANDLER",
    "KafkaResponderRunner",
    "KafkaResponderSpec",
    "discover_kafka_responders",
    "kafka_responder",
    "read_rpc_headers",
    "run_responder",
    "send_rpc_reply",
]


@injectable
class KafkaResponderRunner:
    """Đọc topic yêu cầu và trả lời cho mọi @kafka_responder tìm được.

    Mỗi responder một consumer riêng, như `KafkaRunner`: nhóm gắn liền với con
    trỏ đọc nên không dùng chung được, và một topic khai sai không được phép
    kéo các topic khác chết theo.
    """

    def __init__(self, broker: KafkaBroker, settings: Settings) -> None:
        self._broker = broker
        self._config = settings.kafka
        self._specs: list[KafkaResponderSpec] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._specs = discover_kafka_responders()
        if not self._specs:
            return
        self._closing = False
        self._broker.on_ready(self._setup)
        if self._broker.connected:
            await self._setup()

    async def _setup(self) -> None:
        for spec in self._specs:
            key = f"{spec.group}:{spec.pattern}"
            previous = self._tasks.get(key)
            if previous is not None and not previous.done():
                continue
            self._tasks[key] = asyncio.create_task(
                self._read_loop(spec), name=f"kafka-responder-{key}"
            )

    async def _read_loop(self, spec: KafkaResponderSpec) -> None:
        aiokafka = _require_aiokafka()
        delay = self._config.reconnect_delay_seconds
        while not self._closing:
            consumer = aiokafka.AIOKafkaConsumer(
                spec.pattern,
                bootstrap_servers=self._config.bootstrap_servers,
                group_id=spec.group,
                client_id=f"{self._config.client_id}-responder",
                auto_offset_reset=spec.auto_offset_reset,
                enable_auto_commit=False,
            )
            try:
                await consumer.start()
                log.info(
                    "kafka.responder_started",
                    handler=spec.label,
                    topic=spec.pattern,
                    group=spec.group,
                )
                delay = self._config.reconnect_delay_seconds
                async for record in consumer:
                    if self._closing:
                        break
                    await self._handle(spec, record)
                    await consumer.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - hỏng kiểu gì cũng dựng lại
                if self._closing:
                    return
                log.warning(
                    "kafka.responder_lost",
                    handler=spec.label,
                    error=f"{type(exc).__name__}: {exc}",
                    retry=delay,
                )
            finally:
                with contextlib.suppress(Exception):
                    await consumer.stop()

            if self._closing:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    async def _handle(self, spec: KafkaResponderSpec, record: Any) -> None:
        """KHÔNG được ném lỗi ra ngoài: ném là vòng đọc đứt và cả phân vùng
        dừng lại vì một yêu cầu hỏng."""
        correlation_id, reply_topic = read_rpc_headers(record)
        is_request = bool(correlation_id and reply_topic)

        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                data = decode(record.value)
                meta = {
                    "pattern": spec.pattern,
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                    "correlation_id": correlation_id,
                }
                result = await run_responder(spec, data, meta)
        except Exception as exc:
            kafka_consume_failed.inc(topic=spec.pattern)
            log.exception(
                "kafka.responder_failed", handler=spec.label, topic=spec.pattern, error=str(exc)
            )
            if is_request:
                await self._send(spec, reply_topic, correlation_id, error=exc)
            return
        finally:
            reset_request_id(token)

        kafka_consumed.inc(topic=spec.pattern)
        if is_request:
            await self._send(spec, reply_topic, correlation_id, result=result)
        elif result is not None:
            # Thiếu header đối chiếu nghĩa là SỰ KIỆN (NestJS `emit()`): không
            # ai chờ kết quả. Nói ra để không ai đi tìm xem nó biến đâu mất.
            log.debug(
                "kafka.responder_result_dropped", handler=spec.label, topic=spec.pattern
            )

    async def _send(
        self,
        spec: KafkaResponderSpec,
        topic: str | None,
        correlation_id: str | None,
        *,
        result: Any = None,
        error: BaseException | str | None = None,
    ) -> None:
        if not topic or not correlation_id:
            return
        try:
            await send_rpc_reply(
                self._broker.producer(),
                topic=topic,
                correlation_id=correlation_id,
                result=result,
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 - việc đã xong, đường về hỏng không làm lại
            log.warning(
                "rpc.reply_failed",
                handler=spec.label,
                reply_to=topic,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def shutdown(self) -> None:
        self._closing = True
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "responders": [
                {"topic": s.pattern, "group": s.group, "handler": s.label} for s in self._specs
            ]
        }
