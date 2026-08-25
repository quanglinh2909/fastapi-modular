"""Test cần broker MQTT THẬT.

Mặc định bỏ qua. Chạy đầy đủ:

    docker run -d --name mqtt-test -p 1893:1883 eclipse-mosquitto:2 \
        sh -c "printf 'listener 1883\\nallow_anonymous true\\n' > /m.conf && mosquitto -c /m.conf"
    make install-mqtt
    TEST_MQTT_URL=mqtt://localhost:1893 make test
"""

from __future__ import annotations

import importlib.util
import os
import time

import anyio
import pytest
from pydantic import BaseModel

from fastapi_modular.core.config import MqttSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.rpc import RpcRemoteError, RpcTimeoutError
from fastapi_modular.infrastructure.mqtt import (
    MqttClient,
    MqttResponderRunner,
    MqttRunner,
    mqtt_responder,
    mqtt_subscriber,
)

CO_AIOMQTT = importlib.util.find_spec("aiomqtt") is not None
MQTT_URL = os.getenv("TEST_MQTT_URL")

pytestmark = pytest.mark.skipif(
    not (CO_AIOMQTT and MQTT_URL), reason="cần aiomqtt + TEST_MQTT_URL"
)

DA_NHAN: list[tuple[str, str]] = []          # (tên handler, topic)


class NhietDo(BaseModel):
    value: float


@injectable
class ThietBiTest:
    @mqtt_subscriber("kiem-tra/+/nhiet-do", qos=1)
    async def narrow(self, payload: NhietDo, meta: dict) -> None:
        DA_NHAN.append(("hep", meta["topic"]))

    @mqtt_subscriber("kiem-tra/#", qos=0)
    async def wide(self, payload: dict, meta: dict) -> None:
        DA_NHAN.append(("rong", meta["topic"]))


@pytest.fixture
def mqtt_settings() -> Settings:
    return Settings(
        APP_MQTT=MqttSettings(enabled=True, url=MQTT_URL or "", client_id="test-app")
    )


@pytest.fixture
async def mqtt(mqtt_settings: Settings):
    client = MqttClient(mqtt_settings)
    runner = MqttRunner(client, mqtt_settings)
    await runner.startup()                   # khai topic TRƯỚC khi nối
    await client.startup()
    await anyio.sleep(0.3)
    try:
        yield client
    finally:
        await client.shutdown()


async def _pending(so_tin: int, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(DA_NHAN) < so_tin:
        await anyio.sleep(0.05)
    await anyio.sleep(0.3)                    # để tin thừa (nếu có) kịp tới


async def test_noi_duoc_va_dang_ky_dung_topic(mqtt: MqttClient):
    stats = mqtt.stats()
    assert stats["connected"] is True
    # Hai handler, nhưng CHỈ MỘT đăng ký gửi lên broker: "kiem-tra/#" bao trọn
    # "kiem-tra/+/nhiet-do". (Danh sách còn topic của module ví dụ trong
    # app/, vì runner quét mọi provider đã đăng ký — nên chỉ soi phần của
    # test này.)
    assert "kiem-tra/#" in stats["topics"]
    assert "kiem-tra/+/nhiet-do" not in stats["topics"], "bộ lọc bị bao trọn thì không đăng ký"
    assert "kiem-tra/+/nhiet-do" in stats["listeners"]


async def test_bo_loc_chong_nhau_van_chi_giao_mot_lan(mqtt: MqttClient):
    """Đăng ký cả hai bộ lọc thì mosquitto giao MỘT tin thành HAI bản."""
    DA_NHAN.clear()
    assert await mqtt.publish("kiem-tra/bep/nhiet-do", {"value": 31.2}, qos=1) is True
    await _pending(2)

    assert sorted(DA_NHAN) == [("hep", "kiem-tra/bep/nhiet-do"), ("rong", "kiem-tra/bep/nhiet-do")]


async def test_chi_handler_khop_moi_chay(mqtt: MqttClient):
    """`+` là đúng một tầng nên "hep" không được gọi cho topic ba tầng."""
    DA_NHAN.clear()
    await mqtt.publish("kiem-tra/bep/tang2/do-am", {"value": 70}, qos=1)
    await _pending(1)
    assert DA_NHAN == [("rong", "kiem-tra/bep/tang2/do-am")]


async def test_payload_sai_khuon_khong_lam_dut_vong_doc(mqtt: MqttClient):
    """Không có DLQ để đẩy vào: ghi log, bỏ tin, và TIẾP TỤC nhận tin sau."""
    DA_NHAN.clear()
    await mqtt.publish("kiem-tra/san/nhiet-do", {"thieu_gia_tri": 1}, qos=1)
    await _pending(1)
    assert ("hep", "kiem-tra/san/nhiet-do") not in DA_NHAN, "sai khuôn thì handler không chạy"

    DA_NHAN.clear()
    await mqtt.publish("kiem-tra/san/nhiet-do", {"value": 25.0}, qos=1)
    await _pending(2)
    assert ("hep", "kiem-tra/san/nhiet-do") in DA_NHAN, "tin sau vẫn phải tới nơi"


async def test_khong_gui_duoc_khi_chua_ket_noi(mqtt_settings: Settings):
    from fastapi_modular.core.exceptions import ServiceUnavailableError

    client = MqttClient(
        Settings(APP_MQTT=MqttSettings(enabled=True, url="mqtt://localhost:1",
                                       connect_timeout_seconds=0.5))
    )
    await client.startup()                   # nối không được, app vẫn chạy
    assert client.connected is False
    with pytest.raises(ServiceUnavailableError):
        await client.publish("a/b", {})
    # fire_and_forget: cảnh báo rồi đi tiếp, không ném lỗi
    assert await client.publish("a/b", {}, fire_and_forget=True) is False
    await client.shutdown()


# ============================================== emit / send (khuôn NestJS)
DA_TRA_LOI_MQTT: list[dict] = []


@injectable
class MqttRpcService:
    @mqtt_responder("kiem-tra/rpc/sum")
    async def cong(self, data: list[int]) -> int:
        return sum(data)

    @mqtt_responder("kiem-tra/rpc/boom")
    async def no(self, data: dict) -> None:
        raise RuntimeError("hỏng cố ý")

    @mqtt_responder("kiem-tra/rpc/event")
    async def su_kien(self, data: dict) -> str:
        DA_TRA_LOI_MQTT.append(data)
        return "không ai đọc"


@pytest.fixture
async def rpc_mqtt(mqtt_settings: Settings):
    client = MqttClient(mqtt_settings)
    runner = MqttResponderRunner(client, mqtt_settings)
    await runner.startup()          # TRƯỚC client: topic gửi lên trong lần bắt tay đầu
    await client.startup()
    await anyio.sleep(0.5)
    yield client
    await client.shutdown()


async def test_send_nhan_lai_gia_tri(rpc_mqtt: MqttClient):
    assert await rpc_mqtt.send("kiem-tra/rpc/sum", [1, 2, 3, 4], timeout=10) == 10


async def test_handler_hong_thi_bao_ngay(rpc_mqtt: MqttClient):
    with pytest.raises(RpcRemoteError, match="hỏng cố ý"):
        await rpc_mqtt.send("kiem-tra/rpc/boom", {}, timeout=10)


async def test_khong_ai_tra_loi_thi_het_gio(rpc_mqtt: MqttClient):
    """MQTT KHÔNG đếm được người nghe — khác Redis, chỉ biết bằng cách hết giờ."""
    with pytest.raises(RpcTimeoutError):
        await rpc_mqtt.send("kiem-tra/rpc/khong-ai-nghe", {}, timeout=1.0)


async def test_emit_khong_cho_ket_qua(rpc_mqtt: MqttClient):
    DA_TRA_LOI_MQTT.clear()
    await rpc_mqtt.emit("kiem-tra/rpc/event", {"tu": "test"})
    han = time.monotonic() + 10
    while time.monotonic() < han and not DA_TRA_LOI_MQTT:
        await anyio.sleep(0.1)
    assert DA_TRA_LOI_MQTT == [{"tu": "test"}]
