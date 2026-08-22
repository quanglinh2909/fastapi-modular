"""Ví dụ MQTT: nghe thiết bị gửi số đo, và một handler bắt-tất-cả để soi.

Bật bằng `make install-mqtt` rồi đặt APP_MQTT__ENABLED=true.
"""

from __future__ import annotations

from pydantic import BaseModel

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.mqtt import mqtt_subscriber

log = get_logger(__name__)

DA_NHAN: list[dict] = []


class NhietDo(BaseModel):
    gia_tri: float
    don_vi: str = "C"


@injectable
class ThietBiListener:
    @mqtt_subscriber("thiet-bi/+/nhiet-do", qos=1)
    async def nhiet_do(self, payload: NhietDo, meta: dict) -> None:
        """`+` là ĐÚNG MỘT TẦNG: khớp thiet-bi/bep/nhiet-do, không khớp
        thiet-bi/tang2/bep/nhiet-do."""
        ma_thiet_bi = meta["topic"].split("/")[1]
        log.info("mqtt.nhiet_do", thiet_bi=ma_thiet_bi, gia_tri=payload.gia_tri)
        _ghi({"topic": meta["topic"], "gia_tri": payload.gia_tri, "qos": meta["qos"],
              "retain": meta["retain"], "handler": "nhiet_do"})

    @mqtt_subscriber("thiet-bi/#", qos=0)
    async def moi_thu(self, payload: object, meta: dict) -> None:
        """`#` nuốt mọi tầng còn lại. Hai handler cùng khớp một tin thì CẢ HAI
        đều chạy — tiện để vừa xử lý vừa soi, nhưng đừng vô tình làm việc gì
        hai lần."""
        _ghi({"topic": meta["topic"], "payload": payload, "handler": "moi_thu"})


def _ghi(muc: dict) -> None:
    DA_NHAN.append(muc)
    del DA_NHAN[:-40]
