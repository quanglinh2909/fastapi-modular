"""Consumer Kafka — `@kafka_subscriber`, tương đương `@rabbitmq_subscriber` của RabbitMQ.

    @injectable
    class DonHangConsumer:
        @kafka_subscriber("don-hang", group="kho-van")
        async def xu_ly(self, payload: DonHang, meta: dict) -> None:
            ...

`group` là BẮT BUỘC và cố ý không tự sinh, giống `queue` bên RabbitMQ: nhóm
chính là danh tính của con trỏ đọc. Tên tự sinh sẽ đổi sau mỗi lần deploy, và
mỗi lần deploy sẽ đọc lại từ đầu (hoặc bỏ qua sạch phần cũ, tuỳ
`auto_offset_reset`).

Ba điều khác RabbitMQ, phải nắm trước khi dùng:

1. **Thử lại làm ĐỨNG phân vùng.** Kafka không cho ack lẻ từng tin: con trỏ đọc
   chỉ tiến lên. Nên thử lại tin thứ 5 nghĩa là tin thứ 6, 7, 8... phải chờ.
   Đó là cái giá của việc giữ đúng thứ tự, và cũng là lý do `retry_delay` ở đây
   nên nhỏ hơn nhiều so với bên RabbitMQ.

2. **Không xoá được một tin.** Tin lỗi được sao sang topic `<topic>.dlt` rồi con
   trỏ đi tiếp; bản gốc vẫn nằm trong nhật ký cho tới khi hết hạn giữ.

3. **Số worker chạy song song bị chặn bởi số phân vùng.** Topic một phân vùng
   thì chạy mười worker cũng chỉ một worker có việc.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import _REGISTRY, container, injectable, request_scope
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.kafka.broker import KafkaBroker, _require_aiokafka
from fastapi_modular.infrastructure.kafka.metrics import (
    kafka_consume_failed,
    kafka_consumed,
    kafka_dead_lettered,
)

log = get_logger(__name__)

_SPEC_ATTR = "__kafka_subscriber__"


class PermanentMessageError(Exception):
    """Tin này sai vĩnh viễn — bỏ qua mọi lượt thử, đẩy thẳng sang `<topic>.dlt`."""


@dataclass(slots=True)
class KafkaSpec:
    topic: str
    group: str
    auto_offset_reset: str = "latest"
    max_retries: int = 3
    retry_delay: float = 1.0
    dead_letter: bool = True
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.topic

    @property
    def dlt(self) -> str:
        return f"{self.topic}.dlt"


def kafka_subscriber(
    topic: str,
    *,
    group: str,
    auto_offset_reset: str = "latest",
    max_retries: int = 0,
    retry_delay: float = 1.0,
    dead_letter: bool = False,
) -> Callable[[Callable], Callable]:
    """Gắn method vào một topic Kafka, đọc dưới danh nghĩa nhóm `group`.

        group               BẮT BUỘC — danh tính con trỏ đọc. Nhiều worker cùng
                            group thì CHIA NHAU phân vùng; khác group thì mỗi
                            bên nhận đủ một bản sao của mọi tin.
        auto_offset_reset   nhóm MỚI (chưa có con trỏ) bắt đầu từ đâu:
                            "latest" = chỉ tin phát sinh từ giờ trở đi (mặc
                            định, an toàn) | "earliest" = đọc lại từ đầu nhật
                            ký, có thể là hàng triệu tin. Nhóm đã có con trỏ thì
                            tham số này KHÔNG có tác dụng.
        max_retries         thử lại mấy lần trước khi bỏ sang <topic>.dlt.
                            Nhớ: thử lại làm đứng cả phân vùng.
        retry_delay         chờ giữa các lần thử (giây). Để nhỏ.
        dead_letter         False = tin lỗi bị BỎ QUA hẳn, con trỏ vẫn đi tiếp.
    """
    if not group:
        raise ValueError("kafka_subscriber cần `group` — xem docstring")
    if auto_offset_reset not in ("latest", "earliest"):
        raise ValueError("auto_offset_reset phải là 'latest' hoặc 'earliest'")

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(
            fn,
            _SPEC_ATTR,
            KafkaSpec(
                topic=topic,
                group=group,
                auto_offset_reset=auto_offset_reset,
                max_retries=max_retries,
                retry_delay=retry_delay,
                dead_letter=dead_letter,
            ),
        )
        return fn

    return decorate


def discover_kafka_subscribers() -> list[KafkaSpec]:
    """Quét mọi provider đã đăng ký để tìm method mang @kafka_subscriber."""
    found: list[KafkaSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: KafkaSpec | None = getattr(fn, _SPEC_ATTR, None)
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
                KafkaSpec(
                    topic=spec.topic,
                    group=spec.group,
                    auto_offset_reset=spec.auto_offset_reset,
                    max_retries=spec.max_retries,
                    retry_delay=spec.retry_delay,
                    dead_letter=spec.dead_letter,
                    cls=cls,
                    fn=fn,
                    model=model,
                    wants_meta=len(params) == 2,
                )
            )
    return sorted(found, key=lambda s: (s.group, s.topic))


@injectable
class KafkaRunner:
    """Một AIOKafkaConsumer cho mỗi @kafka_subscriber, mỗi cái một task."""

    def __init__(self, broker: KafkaBroker, settings: Settings) -> None:
        self._broker = broker
        self._config = settings.kafka
        self._specs: list[KafkaSpec] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._specs = discover_kafka_subscribers()
        if not self._specs:
            return

        self._closing = False
        self._broker.on_ready(self._setup)
        if self._broker.connected:
            await self._setup()

    async def _setup(self) -> None:
        """Idempotent: gọi lại bao nhiêu lần cũng không sinh consumer trùng."""
        for spec in self._specs:
            key = f"{spec.group}:{spec.topic}"
            previous = self._tasks.get(key)
            if previous is not None and not previous.done():
                continue
            self._tasks[key] = asyncio.create_task(self._read_loop(spec), name=f"kafka-{key}")

    async def _read_loop(self, spec: KafkaSpec) -> None:
        """Đọc mãi. Consumer chết vì bất cứ lý do gì thì dựng lại, có backoff.

        Mỗi spec một consumer RIÊNG: dùng chung một consumer cho nhiều nhóm là
        không thể (nhóm gắn với con trỏ đọc), và một topic khai sai không được
        phép kéo các topic khác chết theo.
        """
        aiokafka = _require_aiokafka()
        delay = self._config.reconnect_delay_seconds
        while not self._closing:
            consumer = aiokafka.AIOKafkaConsumer(
                spec.topic,
                bootstrap_servers=self._config.bootstrap_servers,
                group_id=spec.group,
                client_id=self._config.client_id,
                auto_offset_reset=spec.auto_offset_reset,
                # Tự commit theo đồng hồ sẽ commit cả những tin CHƯA xử lý xong
                # — tiến trình chết đúng lúc đó là mất tin. Commit tay sau khi
                # handler xong cho ngữ nghĩa "ít nhất một lần".
                enable_auto_commit=False,
            )
            try:
                await consumer.start()
                log.info(
                    "kafka.consumer_started",
                    handler=spec.label,
                    topic=spec.topic,
                    group=spec.group,
                    from_=spec.auto_offset_reset,
                )
                delay = self._config.reconnect_delay_seconds
                async for message in consumer:
                    if self._closing:
                        break
                    await self._handle(spec, message)
                    await consumer.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - hỏng kiểu gì cũng dựng lại
                if self._closing:
                    return
                log.warning(
                    "kafka.consumer_lost",
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

    async def _handle(self, spec: KafkaSpec, message: Any) -> None:
        """Chạy handler, thử lại tại chỗ, hết lượt thì sang <topic>.dlt.

        Hàm này KHÔNG được ném lỗi ra ngoài: ném là vòng đọc đứt và cả phân
        vùng dừng lại vì một tin hỏng.
        """
        for attempt in range(1, spec.max_retries + 2):
            try:
                await self._call(spec, message, attempt)
            except PermanentMessageError as exc:
                log.error("kafka.permanent_error", handler=spec.label, error=str(exc))
                await self._move_to_dlt(spec, message, exc)
                return
            except Exception as exc:
                kafka_consume_failed.inc(topic=spec.topic)
                log.exception(
                    "kafka.handler_failed",
                    handler=spec.label,
                    topic=spec.topic,
                    partition=message.partition,
                    offset=message.offset,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt > spec.max_retries:
                    await self._move_to_dlt(spec, message, exc)
                    return
                await asyncio.sleep(spec.retry_delay)
            else:
                kafka_consumed.inc(topic=spec.topic)
                return

    async def _call(self, spec: KafkaSpec, message: Any, attempt: int) -> None:
        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                payload: Any = json.loads(message.value)
                if spec.model is not None:
                    try:
                        payload = spec.model.model_validate(payload)
                    except ValidationError as exc:
                        raise PermanentMessageError(f"Payload không hợp lệ: {exc}") from exc

                instance = container.resolve(spec.cls)      # type: ignore[arg-type]
                if spec.wants_meta:
                    meta = {
                        "topic": message.topic,
                        "partition": message.partition,
                        "offset": message.offset,
                        "key": message.key.decode() if message.key else None,
                        "timestamp": message.timestamp,
                        "attempt": attempt,
                    }
                    await spec.fn(instance, payload, meta)  # type: ignore[misc]
                else:
                    await spec.fn(instance, payload)        # type: ignore[misc]
        finally:
            reset_request_id(token)

    async def _move_to_dlt(self, spec: KafkaSpec, message: Any, error: BaseException) -> None:
        kafka_dead_lettered.inc(topic=spec.topic)
        if not spec.dead_letter:
            log.error(
                "kafka.message_dropped",
                handler=spec.label,
                offset=message.offset,
                hint="consumer khai dead_letter=False nên tin bị bỏ qua, không lưu lại",
            )
            return

        try:
            await self._broker.publish(
                spec.dlt,
                json.loads(message.value),
                key=message.key.decode() if message.key else None,
                headers={
                    "x-original-topic": message.topic,
                    "x-original-partition": str(message.partition),
                    "x-original-offset": str(message.offset),
                    "x-error": f"{type(error).__name__}: {error}"[:500],
                },
                fire_and_forget=True,
            )
            log.error("kafka.dead_lettered", handler=spec.label, topic=spec.dlt)
        except Exception as exc:  # không cứu được thì cũng đừng chặn phân vùng
            log.exception("kafka.dlt_publish_failed", handler=spec.label, error=str(exc))

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
            "consumers": [
                {
                    "handler": spec.label,
                    "topic": spec.topic,
                    "group": spec.group,
                    "retries": spec.max_retries,
                    "dead_letter": spec.dlt if spec.dead_letter else None,
                    "running": (
                        (task := self._tasks.get(f"{spec.group}:{spec.topic}")) is not None
                        and not task.done()
                    ),
                }
                for spec in self._specs
            ]
        }
