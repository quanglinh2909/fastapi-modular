"""Test lớp Kafka KHÔNG cần cụm thật."""

from __future__ import annotations

import pytest

from pymodular.core.config import KafkaSettings, Settings
from pymodular.core.container import injectable
from pymodular.core.exceptions import ComponentNotEnabledError
from pymodular.infrastructure.kafka import KafkaBroker, kafka_subscriber
from pymodular.infrastructure.kafka.consumers import discover_kafka_subscribers


def test_mac_dinh_la_tat():
    assert KafkaSettings().enabled is False


async def test_tat_thi_khong_lam_gi_ca():
    broker = KafkaBroker(Settings(APP_KAFKA=KafkaSettings(enabled=False)))
    await broker.startup()
    assert broker.connected is False
    assert broker.stats()["servers"] is None
    with pytest.raises(ComponentNotEnabledError):
        await broker.publish("don-hang", {})
    await broker.shutdown()


def test_app_van_chay_binh_thuong_khi_khong_co_kafka(client):
    assert client.get("/api/health/ready").json().get("kafka") is None


@injectable
class DonHangMau:
    @kafka_subscriber("don-hang", group="test-kho-van", auto_offset_reset="earliest",
                      max_retries=2, retry_delay=0.5)
    async def kho(self, payload: dict, meta: dict) -> None: ...

    @kafka_subscriber("don-hang", group="test-ke-toan", max_retries=0)
    async def ke_toan(self, payload: dict) -> None: ...


def test_hai_nhom_cung_mot_topic():
    """Khác group = mỗi bên một con trỏ đọc riêng, cùng nhận đủ mọi tin."""
    specs = {spec.group: spec for spec in discover_kafka_subscribers()}
    # Tên nhóm có tiền tố "test-" để không đụng module ví dụ trong app/,
    # vốn cũng khai "kho-van" và "ke-toan" trên cùng topic.
    assert set(specs) >= {"test-kho-van", "test-ke-toan"}
    assert specs["test-kho-van"].topic == specs["test-ke-toan"].topic == "don-hang"
    assert (specs["test-kho-van"].max_retries, specs["test-kho-van"].retry_delay) == (2, 0.5)
    assert specs["test-kho-van"].auto_offset_reset == "earliest"
    assert specs["test-ke-toan"].auto_offset_reset == "latest", "mặc định là chỉ đọc tin mới"
    assert specs["test-kho-van"].dlt == "don-hang.dlt"


def test_group_la_bat_buoc():
    with pytest.raises(TypeError):

        @kafka_subscriber("don-hang")           # type: ignore[call-arg]
        async def thieu_group(self, payload: dict) -> None: ...

    with pytest.raises(ValueError, match="group"):

        @kafka_subscriber("don-hang", group="")
        async def group_rong(self, payload: dict) -> None: ...


def test_auto_offset_reset_sai_bi_tu_choi():
    with pytest.raises(ValueError, match="auto_offset_reset"):

        @kafka_subscriber("don-hang", group="x", auto_offset_reset="tu-dau")
        async def sai(self, payload: dict) -> None: ...


def test_consumer_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @kafka_subscriber("don-hang", group="x")
        def dong_bo(self, payload: dict) -> None: ...
