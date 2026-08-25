"""Test lớp MQTT KHÔNG cần broker thật — chủ yếu là luật khớp topic."""

from __future__ import annotations

import pytest

from fastapi_modular.core.config import MqttSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import BadRequestError, ComponentNotEnabledError
from fastapi_modular.infrastructure.mqtt import (
    MqttClient,
    covers,
    discover_mqtt_responders,
    matches,
    mqtt_responder,
    mqtt_subscriber,
    narrow_filters,
)
from fastapi_modular.infrastructure.mqtt.client import parse_url, safe_url
from fastapi_modular.infrastructure.mqtt.consumers import discover_mqtt_subscribers
from fastapi_modular.infrastructure.mqtt.patterns import validate_topic, validate_topic_filter


def test_mac_dinh_la_tat():
    assert MqttSettings().enabled is False


async def test_tat_thi_khong_lam_gi_ca():
    client = MqttClient(Settings(APP_MQTT=MqttSettings(enabled=False)))
    await client.startup()
    assert client.connected is False
    with pytest.raises(ComponentNotEnabledError):
        await client.publish("a/b", {})
    await client.shutdown()


def test_app_van_chay_binh_thuong_khi_khong_co_mqtt(client):
    assert client.get("/api/health/ready").json().get("mqtt") is None


@pytest.mark.parametrize(
    ("filtered", "topic", "expected"),
    [
        ("nha/+/den", "nha/bep/den", True),
        ("nha/+/den", "nha/bep/tang2/den", False),   # + đúng MỘT tầng
        ("nha/#", "nha/bep/den", True),
        ("nha/#", "nha", True),                      # # nuốt cả zero tầng
        ("nha/#", "san/vuon", False),
        ("#", "$SYS/broker/uptime", False),          # đại diện không chạm topic hệ thống
        ("$SYS/#", "$SYS/broker/uptime", True),
        ("a/b", "a/b/c", False),
    ],
)
def test_khop_topic(filtered: str, topic: str, expected: bool):
    assert matches(filtered, topic) is expected


@pytest.mark.parametrize("xau", ["nha/#/den", "nha#", "a/b+", ""])
def test_topic_sai_bi_tu_choi_ngay_luc_khai_bao(xau: str):
    with pytest.raises(BadRequestError):
        validate_topic_filter(xau)


def test_khong_gui_vao_topic_co_ky_tu_dai_dien():
    with pytest.raises(BadRequestError, match="ký tự đại diện"):
        validate_topic("thiet-bi/#")


def test_bo_loc_chong_nhau_chi_dang_ky_cai_rong_nhat():
    """Đăng ký cả hai thì broker giao MỘT tin thành HAI lần."""
    assert covers("thiet-bi/#", "thiet-bi/+/nhiet-do") is True
    assert covers("a/+", "a/#") is False, "a/+ không bao được a/b/c"
    # QoS dồn về bộ lọc còn lại, lấy mức cao nhất — không thì tin bị hạ cấp.
    assert narrow_filters({"thiet-bi/#": 0, "thiet-bi/+/nhiet-do": 1}) == {"thiet-bi/#": 1}
    assert narrow_filters({"a/b": 1, "c/d": 2}) == {"a/b": 1, "c/d": 2}


def test_doc_url():
    assert parse_url("mqtt://ai:do@broker:1884") == {
        "hostname": "broker", "port": 1884, "username": "ai", "password": "do", "tls": False,
    }
    assert parse_url("mqtts://broker")["port"] == 8883
    assert safe_url("mqtt://ai:sieubimat@broker:1883") == "mqtt://ai:***@broker:1883"
    with pytest.raises(ValueError, match="mqtt://"):
        parse_url("amqp://broker:5672")


@injectable
class ThietBiMau:
    @mqtt_subscriber("thiet-bi/+/nhiet-do", qos=1)
    async def nhiet_do(self, payload: dict, meta: dict) -> None: ...

    @mqtt_subscriber("thiet-bi/#", qos=0)
    async def moi_thu(self, payload: dict) -> None: ...


def test_tim_duoc_topic_da_khai():
    specs = {spec.topic: spec for spec in discover_mqtt_subscribers()}
    assert specs["thiet-bi/+/nhiet-do"].qos == 1
    assert specs["thiet-bi/+/nhiet-do"].wants_meta is True
    assert specs["thiet-bi/#"].qos == 0


def test_qos_sai_bi_tu_choi():
    with pytest.raises(ValueError, match="qos"):

        @mqtt_subscriber("a/b", qos=3)
        async def bad(self, payload: dict) -> None: ...


# -------------------------------------------------- @mqtt_responder (send)
@injectable
class MqttResponderMau:
    @mqtt_responder("thiet-bi/dahua-01/trang-thai")
    async def trang_thai(self, data: dict) -> dict:
        return {"online": True}


def test_responder_quet_duoc_va_dang_ky_topic():
    patterns = {s.pattern for s in discover_mqtt_responders()}
    assert "thiet-bi/dahua-01/trang-thai" in patterns


def test_bo_loc_bi_tu_choi():
    """Topic trả lời là `<pattern>/reply` — bộ lọc thì không nói được trả về đâu."""
    for bad in ("thiet-bi/+/trang-thai", "thiet-bi/#"):
        with pytest.raises(BadRequestError, match="bộ lọc"):

            @mqtt_responder(bad)
            async def sai(self, data) -> None: ...


def test_qos_sai_bi_chan():
    with pytest.raises(BadRequestError, match="qos"):

        @mqtt_responder("a/b", qos=3)
        async def sai(self, data) -> None: ...


async def test_responder_dang_ky_topic_va_cam_router_vao_client():
    """Runner phải chạy TRƯỚC client: danh sách topic gửi lên trong lần bắt tay đầu."""
    from fastapi_modular.infrastructure.mqtt import MqttResponderRunner

    settings = Settings(APP_MQTT=MqttSettings(enabled=True))
    client = MqttClient(settings)
    runner = MqttResponderRunner(client, settings)
    await runner.startup()

    # Chỉ soi phần của test này: các module test khác cũng đăng ký responder,
    # và sổ đăng ký là toàn cục.
    assert "thiet-bi/dahua-01/trang-thai" in client.stats()["topics"]
    assert "thiet-bi/dahua-01/trang-thai" in runner.stats()["responders"]
