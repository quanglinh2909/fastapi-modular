"""Hai endpoint để thử vòng đời retry/DLQ mà không cần mở công cụ nào khác."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from fastapi_modular.core.controller import controller, get, post
from fastapi_modular.infrastructure.rabbitmq import RabbitBroker
from src.api.rabbitmq_test.rabbitmq_consumer import (
    EXCHANGE,
    QUEUE,
    ROUTING_KEY,
    AlertCreated,
)


@controller(prefix="/rabbitmq-test", tags=["rabbitmq-test"])
class RabbitMQTestController:
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    @post("/gui", summary="Đăng một tin thử — kieu quyết định nó đi đường nào")
    async def send(self, payload: AlertCreated) -> dict[str, object]:
        await self._broker.publish(EXCHANGE, ROUTING_KEY, payload.model_dump())
        di_dau = {
            "ok": f"{QUEUE} -> handler chạy xong -> ack",
            "hong-tam-thoi": f"{QUEUE} -> {QUEUE}.retry (5s) -> thử lại 2 lần -> {QUEUE}.dlq",
            "hong-vinh-vien": f"{QUEUE} -> {QUEUE}.dlq ngay, không thử lại",
        }
        return {
            "da_dang": True,
            "exchange": EXCHANGE,
            "routing_key": ROUTING_KEY,
            "duong_di_du_kien": di_dau.get(payload.kind, "payload sai khuôn -> .dlq ngay"),
        }

    @get("/hang-doi", summary="Ba hàng đợi đang có gì, và tin nào nằm trong DLQ")
    async def queue(
        self, limit: Annotated[int, Query(ge=1, le=50)] = 10
    ) -> dict[str, object]:
        return {
            "chinh": await self._broker.queue_info(QUEUE),
            "retry": await self._broker.queue_info(f"{QUEUE}.retry"),
            "dlq": await self._broker.queue_info(f"{QUEUE}.dlq"),
            # peek() xem xong trả tin lại chỗ cũ, nên gọi bao nhiêu lần cũng được.
            "trong_dlq": await self._broker.peek(f"{QUEUE}.dlq", limit=limit),
        }
