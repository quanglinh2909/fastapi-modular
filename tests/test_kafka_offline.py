"""Test lớp Kafka KHÔNG cần cụm thật."""

from __future__ import annotations

import pytest

from fastapi_modular.core.config import KafkaSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import ComponentNotEnabledError
from fastapi_modular.core.rpc import (
    KAFKA_CORRELATION_ID,
    KAFKA_NEST_ERR,
    KAFKA_NEST_IS_DISPOSED,
    KAFKA_REPLY_TOPIC,
)
from fastapi_modular.infrastructure.kafka import (
    KafkaBroker,
    discover_kafka_responders,
    kafka_responder,
    kafka_subscriber,
)
from fastapi_modular.infrastructure.kafka.consumers import discover_kafka_subscribers
from fastapi_modular.infrastructure.kafka.responders import read_rpc_headers, send_rpc_reply


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
    async def store(self, payload: dict, meta: dict) -> None: ...

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
        async def bad(self, payload: dict) -> None: ...


def test_consumer_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @kafka_subscriber("don-hang", group="x")
        def dong_bo(self, payload: dict) -> None: ...


# ------------------------------------------------- @kafka_responder (send)
@injectable
class KafkaResponderMau:
    @kafka_responder("tinh-diem", group="scoring")
    async def tinh(self, data: dict) -> int:
        return data["a"] + data["b"]


def test_responder_quet_duoc():
    specs = {s.pattern: s for s in discover_kafka_responders()}
    assert specs["tinh-diem"].group == "scoring"
    assert specs["tinh-diem"].auto_offset_reset == "latest", (
        "nhóm mới chỉ trả lời yêu cầu từ giờ; `earliest` sẽ trả lời cả yêu cầu cũ "
        "mà người gọi đã bỏ đi từ lâu"
    )


def test_responder_cung_doi_group():
    with pytest.raises(ValueError, match="group"):
        kafka_responder("x", group="")


def test_thieu_mot_trong_hai_header_thi_la_su_kien_chu_khong_phai_yeu_cau():
    """Đúng luật của NestJS: không biết trả về đâu thì không thể trả lời."""

    class Record:
        def __init__(self, headers):
            self.headers = headers

    du = [(KAFKA_CORRELATION_ID, b"abc"), (KAFKA_REPLY_TOPIC, b"t.reply")]
    assert read_rpc_headers(Record(du)) == ("abc", "t.reply")
    assert read_rpc_headers(Record(du[:1])) == ("abc", None)
    assert read_rpc_headers(Record([])) == (None, None)


async def test_tra_loi_dat_dung_ba_header_cua_nestjs():
    """Kafka KHÔNG bọc {response, isDisposed} vào thân tin — trạng thái đi hết ở header."""
    da_gui = {}

    class Producer:
        async def send_and_wait(self, topic, value=None, headers=None):
            da_gui.update(topic=topic, value=value, headers=dict(headers))

    await send_rpc_reply(Producer(), topic="t.reply", correlation_id="abc", result={"x": 1})
    assert da_gui["value"] == b'{"x": 1}', "value LÀ câu trả lời, không có vỏ bọc"
    assert da_gui["headers"][KAFKA_CORRELATION_ID] == b"abc"
    assert KAFKA_NEST_IS_DISPOSED in da_gui["headers"]
    assert KAFKA_NEST_ERR not in da_gui["headers"]

    da_gui.clear()
    await send_rpc_reply(
        Producer(), topic="t.reply", correlation_id="abc", error=RuntimeError("vỡ")
    )
    assert da_gui["headers"][KAFKA_NEST_ERR].startswith(b"RuntimeError")
    assert KAFKA_NEST_IS_DISPOSED in da_gui["headers"], "lỗi cũng là kết thúc"
