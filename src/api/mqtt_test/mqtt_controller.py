"""Endpoint thử lớp MQTT."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

from fastapi_modular.core.controller import controller, get, post
from fastapi_modular.infrastructure.mqtt import MqttClient
from src.api.mqtt_test.mqtt_listener import DA_NHAN


class TinGui(BaseModel):
    topic: str = Field(default="thiet-bi/bep/nhiet-do", description="Không được chứa + hoặc #")
    payload: dict | str = Field(default_factory=lambda: {"gia_tri": 28.5})
    qos: int = Field(default=1, ge=0, le=2)
    retain: bool = False


@controller(prefix="/mqtt-test", tags=["mqtt-test"])
class MqttTestController:
    def __init__(self, mqtt: MqttClient) -> None:
        self._mqtt = mqtt

    @post("/gui", summary="Gửi một tin lên broker")
    async def gui(self, tin: TinGui) -> dict[str, object]:
        da_gui = await self._mqtt.publish(
            tin.topic, tin.payload, qos=tin.qos, retain=tin.retain
        )
        return {
            "da_gui": da_gui,
            "topic": tin.topic,
            "qos": tin.qos,
            "retain": tin.retain,
            "ghi_chu": (
                "retain=True: broker giữ tin này làm giá trị hiện tại của topic, "
                "ai đăng ký sau cũng nhận ngay"
                if tin.retain
                else "retain=False: chỉ ai đang nghe lúc này mới nhận"
            ),
        }

    @get("/da-nhan", summary="Tin worker này nhận được")
    async def da_nhan(self, limit: Annotated[int, Query(ge=1, le=40)] = 40) -> dict[str, object]:
        return {"so_tin": len(DA_NHAN), "tin": DA_NHAN[-limit:], "ket_noi": self._mqtt.stats()}
