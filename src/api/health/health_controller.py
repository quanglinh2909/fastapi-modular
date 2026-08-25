"""Endpoint kiểm tra sức khoẻ, cho load balancer và Kubernetes probe.

- /health  (liveness) : tiến trình còn sống không — KHÔNG chạm database, để
                        DB chậm không khiến orchestrator giết tiến trình.
- /health/ready (readiness): có phục vụ được không — có ping database.
"""

from __future__ import annotations

from fastapi import Response, status

from fastapi_modular.core.config import Settings
from fastapi_modular.core.controller import controller, get
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.database import Database
from fastapi_modular.infrastructure.kafka import KafkaBroker
from fastapi_modular.infrastructure.mqtt import MqttClient
from fastapi_modular.infrastructure.rabbitmq import RabbitBroker
from fastapi_modular.infrastructure.redis import RedisClient

log = get_logger(__name__)


@controller(prefix="/health", tags=["health"])
class HealthController:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        broker: RabbitBroker,
        redis: RedisClient,
        mqtt: MqttClient,
        kafka: KafkaBroker,
    ) -> None:
        self._settings = settings
        self._database = database
        self._broker = broker
        self._redis = redis
        self._mqtt = mqtt
        self._kafka = kafka

    @get("", summary="Liveness — tiến trình còn sống")
    async def live(self) -> dict[str, str]:
        return {
            "status": "ok",
            "service": self._settings.name,
            "version": self._settings.version,
            "env": self._settings.env,
        }

    @get("/ready", summary="Readiness — phục vụ được chưa (có ping database)")
    async def ready(self, response: Response) -> dict[str, object]:
        try:
            await self._database.ping()
            database_ok = True
            detail = None
        except Exception as exc:  # noqa: BLE001 - readiness phải nuốt mọi lỗi
            database_ok = False
            detail = f"{type(exc).__name__}: {exc}"
            log.warning("health.database_unreachable", error=detail)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        # RabbitMQ KHÔNG làm readiness thất bại: nó là thành phần phụ, và tắt
        # cả API vì hàng đợi rớt là đổi một sự cố nhỏ lấy một sự cố lớn. Trạng
        # thái vẫn được trả ra để cảnh báo dựa vào đó.
        # Cùng lý do đó cho Redis/MQTT/Kafka: tất cả đều là thành phần phụ,
        # tắt cả API vì một trong số chúng rớt là đổi sự cố nhỏ lấy sự cố lớn.
        extra = {
            "rabbitmq": self._broker.stats() if self._broker.enabled else None,
            "redis": self._redis.stats() if self._redis.enabled else None,
            "mqtt": self._mqtt.stats() if self._mqtt.enabled else None,
            "kafka": self._kafka.stats() if self._kafka.enabled else None,
        }

        circuit = getattr(self._database.backend, "stats", None)
        return {
            "status": "ready" if database_ok else "unavailable",
            "driver": self._database.driver,
            "database": database_ok,
            **{name_: stats for name_, stats in extra.items() if stats},
            **({"circuit": circuit} if circuit else {}),
            **({"detail": detail} if detail else {}),
        }
