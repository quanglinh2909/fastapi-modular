"""Kết nối Kafka: gửi tin có xác nhận, tự nối lại, không chặn lúc khởi động.

Kafka là TUỲ CHỌN. `APP_KAFKA__ENABLED` mặc định false thì lớp này nằm im.

    pip install 'fastapi-modular[kafka]'   # cài aiokafka + ghi sẵn APP_KAFKA__* vào .env

Kafka khác RabbitMQ ở chỗ căn bản: tin KHÔNG mất đi sau khi được xử lý. Nó nằm
lại trong nhật ký theo thời gian giữ (retention) của topic, và mỗi nhóm
consumer có con trỏ đọc riêng. Nhờ vậy thêm một nhóm mới là đọc lại được cả
lịch sử — thứ hàng đợi không làm được. Cái giá: không có "hàng đợi thử lại",
không xoá được một tin lẻ, và thứ tự chỉ bảo đảm TRONG MỘT phân vùng.

Về tự nối lại: aiokafka tự tìm lại broker khi cụm chuyển leader hoặc rớt giữa
chừng. Thứ nó không lo là lúc khởi động mà cụm chưa lên — `producer.start()`
ném lỗi ngay. Chỗ đó lớp này chạy vòng thử lại có backoff, y như RabbitMQ, để
app vẫn phục vụ HTTP bình thường.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi_modular.core.compat import TimeoutErrors
from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import ComponentNotEnabledError, ServiceUnavailableError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.rpc import (
    DEFAULT_RPC_TIMEOUT,
    KAFKA_CORRELATION_ID,
    KAFKA_NEST_ERR,
    KAFKA_REPLY_TOPIC,
    PendingReplies,
    decode,
    normalize_pattern,
    reply_channel,
)
from fastapi_modular.infrastructure.kafka.metrics import kafka_publish_failed, kafka_published

log = get_logger(__name__)

DEFAULT_SERVERS = "localhost:9092"


def _require_aiokafka() -> Any:
    try:
        import aiokafka
    except ModuleNotFoundError as exc:
        raise ComponentNotEnabledError(
            "APP_KAFKA__ENABLED=true nhưng chưa cài thư viện aiokafka. "
            "Chạy `pip install 'fastapi-modular[kafka]'`, hoặc đặt APP_KAFKA__ENABLED=false nếu "
            "dự án này không dùng Kafka."
        ) from exc
    return aiokafka


@injectable
class KafkaBroker:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.kafka
        self._producer: Any = None
        self._supervisor: asyncio.Task[None] | None = None
        self._closing = False
        self._ready_hooks: list[Callable[[], Awaitable[None]]] = []
        self._pending = PendingReplies("Kafka")
        self._rpc_consumer: Any = None
        self._rpc_task: asyncio.Task[None] | None = None
        self._rpc_topics: set[str] = set()
        self._rpc_lock = asyncio.Lock()

    # ------------------------------------------------------------- vòng đời
    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def connected(self) -> bool:
        return self._producer is not None

    @property
    def servers(self) -> str:
        return self._config.bootstrap_servers

    async def startup(self) -> None:
        if not self._config.enabled:
            log.debug("kafka.disabled")
            return

        _require_aiokafka()
        self._closing = False
        if await self._try_connect():
            return

        log.warning(
            "kafka.starting_degraded",
            servers=self.servers,
            hint="app vẫn chạy; sẽ nối lại ngầm cho tới khi được",
        )
        self._supervisor = asyncio.create_task(self._reconnect_forever(), name="kafka-reconnect")

    async def _try_connect(self) -> bool:
        aiokafka = _require_aiokafka()
        producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=self._config.bootstrap_servers,
            client_id=self._config.client_id,
            acks=self._config.acks if self._config.acks != "all" else "all",
            request_timeout_ms=int(self._config.request_timeout_seconds * 1000),
            # Không bật idempotence mặc định: nó đòi acks=all và bản Kafka đủ
            # mới; bật ngầm sẽ làm cụm cũ từ chối kết nối mà không rõ vì sao.
        )
        try:
            await asyncio.wait_for(producer.start(), self._config.connect_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - mọi lỗi đều dẫn tới cùng một việc: thử lại
            with contextlib.suppress(Exception):
                await producer.stop()
            log.warning(
                "kafka.connect_failed",
                servers=self.servers,
                error=f"{type(exc).__name__}: {exc}",
            )
            return False

        self._producer = producer
        log.info("kafka.connected", servers=self.servers, client_id=self._config.client_id)
        await self._run_hooks()
        return True

    async def _reconnect_forever(self) -> None:
        delay = self._config.reconnect_delay_seconds
        while not self._closing and self._producer is None:
            await asyncio.sleep(delay)
            if self._closing:
                return
            if await self._try_connect():
                log.info("kafka.recovered", servers=self.servers)
                return
            delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    def on_ready(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Việc cần làm sau mỗi lần nối được — consumer dùng để bật vòng đọc."""
        self._ready_hooks.append(hook)

    async def _run_hooks(self) -> None:
        for hook in list(self._ready_hooks):
            try:
                await hook()
            except Exception as exc:
                log.exception("kafka.ready_hook_failed", error=str(exc))

    async def shutdown(self) -> None:
        self._closing = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        await self._close_rpc()
        if self._producer is not None:
            with contextlib.suppress(Exception):
                await self._producer.stop()
            log.info("kafka.disconnected")
        self._producer = None

    def _ready(self) -> Any:
        if not self._config.enabled:
            raise ComponentNotEnabledError(
                "Kafka đang tắt (APP_KAFKA__ENABLED=false) nên không gửi tin được."
            )
        if self._producer is None:
            raise ServiceUnavailableError("Chưa kết nối được Kafka")
        return self._producer

    # ---------------------------------------------------------------- gửi
    async def publish(
        self,
        topic: str,
        payload: Any = None,
        *,
        key: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        fire_and_forget: bool = False,
    ) -> bool:
        """Gửi một tin và CHỜ cụm xác nhận. Trả về True nếu đã ghi xong.

        `key` quyết định phân vùng: mọi tin cùng key rơi vào cùng một phân vùng,
        nên chúng được xử lý ĐÚNG THỨ TỰ. Không có key thì tin rải đều và thứ tự
        giữa chúng không còn bảo đảm gì. Quy tắc thực dụng: lấy id của thực thể
        làm key (`key=order_id`) khi thứ tự có ý nghĩa.

        Mức bảo đảm ghi lấy theo `APP_KAFKA__ACKS` — đây là thuộc tính của cụm,
        không phải quyết định của từng lời gọi.
        """
        if not self._config.enabled:
            raise ComponentNotEnabledError(
                "Kafka đang tắt (APP_KAFKA__ENABLED=false) nên không gửi tin được."
            )
        if self._producer is None:
            kafka_publish_failed.inc(topic=topic)
            if fire_and_forget:
                log.warning("kafka.publish_skipped", topic=topic, reason="chưa kết nối")
                return False
            raise ServiceUnavailableError("Chưa kết nối được Kafka")

        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False, default=str
        ).encode()
        try:
            await asyncio.wait_for(
                self._producer.send_and_wait(
                    topic,
                    value=body,
                    key=key.encode() if key else None,
                    headers=[(k, str(v).encode()) for k, v in (headers or {}).items()] or None,
                ),
                timeout or self._config.request_timeout_seconds,
            )
        except Exception as exc:
            kafka_publish_failed.inc(topic=topic)
            if fire_and_forget:
                log.warning(
                    "kafka.publish_failed",
                    topic=topic,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return False
            if isinstance(exc, TimeoutErrors):
                raise ServiceUnavailableError(
                    f"Kafka không xác nhận trong {timeout or self._config.request_timeout_seconds}s"
                ) from exc
            raise

        kafka_published.inc(topic=topic)
        log.debug("kafka.published", topic=topic, key=key)
        return True

    def producer(self) -> Any:
        """Producer thô, cho chỗ cần gửi kèm header nhị phân (phía trả lời RPC)."""
        return self._ready()

    # ------------------------------------------------- khuôn NestJS: emit / send
    async def emit(
        self, pattern: Any, data: Any = None, *, key: str | None = None,
        timeout: float | None = None, fire_and_forget: bool = False,
    ) -> bool:
        """Bắn một SỰ KIỆN theo khuôn NestJS — tương đương `client.emit()`.

        Kafka là hạ tầng DUY NHẤT mà NestJS không gói `{pattern, data, id}`:
        topic chính là pattern, và `value` là data thô. Nên `emit()` ở đây gần
        như trùng với `publish()`, chỉ khác là topic lấy từ `pattern`.
        """
        return await self.publish(
            normalize_pattern(pattern), data, key=key, timeout=timeout,
            fire_and_forget=fire_and_forget,
        )

    async def send(
        self, pattern: Any, data: Any = None, *, key: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Gửi YÊU CẦU rồi chờ trả lời — tương đương `client.send()`.

        Topic trả lời là `<pattern>.reply`; mã đối chiếu đi trong HEADER
        `kafka_correlationId`, đúng quy ước của `ClientKafka`.

        Đọc kỹ phần "Kafka hợp với việc gì" trong docs/rpc.md trước khi dùng:
        Kafka là nhật ký để đọc lại, không phải đường gọi hàm. Độ trễ ở đây
        tính bằng chục mili-giây trở lên, và topic trả lời PHẢI tồn tại sẵn.
        """
        topic_name = normalize_pattern(pattern)
        reply_topic = reply_channel(topic_name)
        deadline = timeout or DEFAULT_RPC_TIMEOUT
        await self._listen_for_replies(reply_topic)

        # Giữ chỗ TRƯỚC khi gửi: bên kia có thể trả lời xong trước khi lệnh
        # gửi của ta kịp trả về.
        correlation_id, waiter = self._pending.open()
        try:
            await self.publish(
                topic_name, data, key=key, timeout=timeout,
                headers={
                    KAFKA_CORRELATION_ID: correlation_id,
                    KAFKA_REPLY_TOPIC: reply_topic,
                },
            )
        except BaseException:
            self._pending.deliver(correlation_id, None)
            raise
        return await self._pending.wait(correlation_id, waiter, deadline, target=topic_name)

    async def _listen_for_replies(self, topic: str) -> None:
        """Mở consumer đọc topic trả lời, và CHỜ nó nhận phân vùng xong.

        Cố ý KHÔNG dùng consumer group: gán phân vùng bằng tay rồi nhảy tới
        cuối. Vào group thì phải qua một vòng cân bằng lại (vài giây, có khi
        lâu hơn) trước khi đọc được tin nào — mà người gọi chỉ chờ vài giây.
        Gán tay thì có hiệu lực ngay, và ta vốn muốn đọc MỌI phân vùng chứ
        không chia phần với ai.
        """
        aiokafka = _require_aiokafka()
        async with self._rpc_lock:
            if self._rpc_consumer is None:
                self._rpc_consumer = aiokafka.AIOKafkaConsumer(
                    bootstrap_servers=self._config.bootstrap_servers,
                    client_id=f"{self._config.client_id}-rpc",
                    enable_auto_commit=False,
                )
                await self._rpc_consumer.start()
                self._rpc_topics = set()

            if topic in self._rpc_topics:
                return

            partitions = self._rpc_consumer.partitions_for_topic(topic)
            if not partitions:
                # Hỏi lại metadata một lần: cụm bật auto-create thì chính lần
                # hỏi này tạo ra topic.
                with contextlib.suppress(Exception):
                    await self._rpc_consumer._client.force_metadata_update()
                partitions = self._rpc_consumer.partitions_for_topic(topic)
            if not partitions:
                raise ServiceUnavailableError(
                    f"Kafka: topic trả lời '{topic}' không tồn tại và cụm không tự tạo. "
                    "Tạo sẵn nó (kafka-topics --create --topic "
                    f"{topic}), hoặc bật auto.create.topics.enable."
                )

            self._rpc_topics.add(topic)
            tp = [
                aiokafka.TopicPartition(t, p)
                for t in self._rpc_topics
                for p in (self._rpc_consumer.partitions_for_topic(t) or set())
            ]
            self._rpc_consumer.assign(tp)
            # Nhảy tới cuối: câu trả lời cũ trong nhật ký không phải của ta, và
            # đọc lại cả topic từ đầu chỉ để tìm một mã đối chiếu là vô nghĩa.
            await self._rpc_consumer.seek_to_end(*tp)

            if self._rpc_task is None or self._rpc_task.done():
                self._rpc_task = asyncio.create_task(
                    self._reply_loop(), name="kafka-rpc-reply"
                )

    async def _reply_loop(self) -> None:
        consumer = self._rpc_consumer
        try:
            async for record in consumer:
                if self._closing:
                    return
                headers = {k: v for k, v in (record.headers or ())}
                correlation_id = headers.get(KAFKA_CORRELATION_ID)
                if correlation_id is None:
                    continue
                correlation_id = correlation_id.decode() if isinstance(correlation_id, (bytes, bytearray)) else str(correlation_id)
                error = headers.get(KAFKA_NEST_ERR)
                if error is not None:
                    description = error.decode() if isinstance(error, (bytes, bytearray)) else str(error)
                    self._pending.deliver(correlation_id, {"err": description, "isDisposed": True})
                    continue
                # Không có `err` thì `value` CHÍNH LÀ câu trả lời — Kafka không
                # gói {response, isDisposed} vào thân tin như ba hạ tầng kia.
                self._pending.deliver(correlation_id, {"response": decode(record.value), "isDisposed": True})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not self._closing:
                log.warning("kafka.rpc_reply_lost", error=f"{type(exc).__name__}: {exc}")
                self._pending.fail_all("consumer trả lời Kafka đứt")

    async def _close_rpc(self) -> None:
        self._pending.cancel_all()
        if self._rpc_task is not None:
            self._rpc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._rpc_task
            self._rpc_task = None
        if self._rpc_consumer is not None:
            with contextlib.suppress(Exception):
                await self._rpc_consumer.stop()
            self._rpc_consumer = None
        self._rpc_topics = set()

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "servers": self.servers if self._config.enabled else None,
            "acks": self._config.acks,
        }
