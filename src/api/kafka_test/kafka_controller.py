"""Endpoint thử lớp Kafka."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from fastapi_modular.core.controller import controller, get, post
from fastapi_modular.infrastructure.kafka import KafkaBroker
from src.api.kafka_test.kafka_consumer import DA_NHAN, TOPIC, DonHang


@controller(prefix="/kafka-test", tags=["kafka-test"])
class KafkaTestController:
    def __init__(self, kafka: KafkaBroker) -> None:
        self._kafka = kafka

    @post("/gui", summary="Gửi một đơn hàng — key quyết định phân vùng")
    async def gui(self, don: DonHang) -> dict[str, object]:
        # key=ma_don: mọi tin của cùng một đơn rơi vào cùng phân vùng, nên
        # chúng được xử lý đúng thứ tự. Không có key thì thứ tự không bảo đảm.
        await self._kafka.publish(TOPIC, don.model_dump(), key=don.ma_don)
        di_dau = {
            "ok": "cả hai nhóm xử lý xong",
            "hong-tam-thoi": f"kho-van thử lại 2 lần rồi sang {TOPIC}.dlt; ke-toan vẫn xong",
            "hong-vinh-vien": f"kho-van sang {TOPIC}.dlt ngay; ke-toan vẫn xong",
        }
        return {
            "topic": TOPIC,
            "key": don.ma_don,
            "du_kien": di_dau.get(don.kieu, "payload sai khuôn -> .dlt ngay"),
        }

    @get("/da-nhan", summary="Hai nhóm đã đọc được gì")
    async def da_nhan(self, limit: Annotated[int, Query(ge=1, le=60)] = 60) -> dict[str, object]:
        return {"so_tin": len(DA_NHAN), "tin": DA_NHAN[-limit:]}
