"""Kết nối Kafka: gửi tin có xác nhận, tự nối lại, không chặn lúc khởi động.

Kafka là TUỲ CHỌN. `APP_KAFKA__ENABLED` mặc định false thì lớp này nằm im.

    pip install 'pymodular[kafka]'   # cài aiokafka + ghi sẵn APP_KAFKA__* vào .env

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

from pymodular.core.compat import TimeoutErrors
from pymodular.core.config import Settings
from pymodular.core.container import injectable
from pymodular.core.exceptions import ComponentNotEnabledError, ServiceUnavailableError
from pymodular.core.logging import get_logger
from pymodular.infrastructure.kafka.metrics import kafka_publish_failed, kafka_published

log = get_logger(__name__)

DEFAULT_SERVERS = "localhost:9092"


def _require_aiokafka() -> Any:
    try:
        import aiokafka
    except ModuleNotFoundError as exc:
        raise ComponentNotEnabledError(
            "APP_KAFKA__ENABLED=true nhưng chưa cài thư viện aiokafka. "
            "Chạy `pip install 'pymodular[kafka]'`, hoặc đặt APP_KAFKA__ENABLED=false nếu "
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
        if await self._thu_noi():
            return

        log.warning(
            "kafka.starting_degraded",
            servers=self.servers,
            hint="app vẫn chạy; sẽ nối lại ngầm cho tới khi được",
        )
        self._supervisor = asyncio.create_task(self._reconnect_forever(), name="kafka-reconnect")

    async def _thu_noi(self) -> bool:
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
            if await self._thu_noi():
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

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "servers": self.servers if self._config.enabled else None,
            "acks": self._config.acks,
        }
