"""Test cần cụm Kafka THẬT.

Mặc định bỏ qua. Chạy đầy đủ:

    docker run -d --name kafka-test -p 9094:9094 \
      -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
      -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093,EXTERNAL://:9094 \
      -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092,EXTERNAL://localhost:9094 \
      -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,EXTERNAL:PLAINTEXT \
      -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
      -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
      -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
      -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
      -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
      -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
      -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \
      apache/kafka:3.9.0
    make install-kafka
    TEST_KAFKA_SERVERS=localhost:9094 make test
"""

from __future__ import annotations

import importlib.util
import os
import time
import uuid

import anyio
import pytest

from fastapi_modular.core.config import KafkaSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.rpc import RpcRemoteError
from fastapi_modular.infrastructure.kafka import (
    KafkaBroker,
    KafkaResponderRunner,
    KafkaRunner,
    PermanentMessageError,
    kafka_responder,
    kafka_subscriber,
)

CO_AIOKAFKA = importlib.util.find_spec("aiokafka") is not None
SERVERS = os.getenv("TEST_KAFKA_SERVERS")

pytestmark = pytest.mark.skipif(
    not (CO_AIOKAFKA and SERVERS), reason="cần aiokafka + TEST_KAFKA_SERVERS"
)

TOPIC = "kiem-tra-don"
DA_NHAN: list[dict] = []

# Nhóm phải đổi mỗi lần chạy: con trỏ đọc của một nhóm được cụm nhớ lại, dùng
# tên cố định thì lần chạy thứ hai bỏ qua sạch tin của lần trước.
LAN_CHAY = uuid.uuid4().hex[:8]


@injectable
class NhomA:
    @kafka_subscriber(TOPIC, group=f"a-{LAN_CHAY}", auto_offset_reset="earliest",
                      max_retries=2, retry_delay=0.2)
    async def xu_ly(self, payload: dict, meta: dict) -> None:
        DA_NHAN.append({"nhom": "a", "ma": payload["ma"], "lan_thu": meta["attempt"],
                        "key": meta["key"]})
        if payload.get("kieu") == "hong-tam-thoi":
            raise RuntimeError("cố tình hỏng")
        if payload.get("kieu") == "hong-vinh-vien":
            raise PermanentMessageError("sai vĩnh viễn")


@injectable
class NhomB:
    @kafka_subscriber(TOPIC, group=f"b-{LAN_CHAY}", auto_offset_reset="earliest",
                      max_retries=0)
    async def xu_ly(self, payload: dict) -> None:
        DA_NHAN.append({"nhom": "b", "ma": payload["ma"], "lan_thu": 1, "key": None})


@pytest.fixture
def kafka_settings() -> Settings:
    return Settings(APP_KAFKA=KafkaSettings(enabled=True, bootstrap_servers=SERVERS or ""))


@pytest.fixture
async def kafka(kafka_settings: Settings):
    broker = KafkaBroker(kafka_settings)
    runner = KafkaRunner(broker, kafka_settings)
    await broker.startup()
    await runner.startup()
    await anyio.sleep(3)                      # chờ nhóm ổn định (rebalance)
    try:
        yield broker
    finally:
        await runner.shutdown()
        await broker.shutdown()


async def _pending(so_tin: int, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(DA_NHAN) < so_tin:
        await anyio.sleep(0.1)


async def test_hai_nhom_deu_nhan_du_moi_tin(kafka: KafkaBroker):
    """Đây là điểm khác hàng đợi: tin không bị lấy đi, mỗi nhóm một con trỏ."""
    DA_NHAN.clear()
    assert await kafka.publish(TOPIC, {"ma": "D1", "kieu": "ok"}, key="D1") is True
    await _pending(2)

    label_ = sorted((t["nhom"], t["ma"]) for t in DA_NHAN)
    assert label_ == [("a", "D1"), ("b", "D1")]
    assert next(t for t in DA_NHAN if t["nhom"] == "a")["key"] == "D1"


async def test_thu_lai_roi_sang_topic_chet(kafka: KafkaBroker):
    """Thử lại NGAY TẠI CHỖ (làm đứng phân vùng), hết lượt thì sao sang .dlt."""
    DA_NHAN.clear()
    await kafka.publish(TOPIC, {"ma": "D2", "kieu": "hong-tam-thoi"}, key="D2")
    await _pending(4)

    lan_thu_a = sorted(t["lan_thu"] for t in DA_NHAN if t["nhom"] == "a")
    assert lan_thu_a == [1, 2, 3], "1 lần đầu + 2 lần thử lại"
    assert [t for t in DA_NHAN if t["nhom"] == "b"], "nhóm b không bị ảnh hưởng"

    tin = await _doc_dlt(f"{TOPIC}.dlt", 1)
    assert tin, "phải có tin trong topic chết"
    headers = {k: v.decode() for k, v in tin[-1].headers}
    assert headers["x-original-topic"] == TOPIC
    assert "RuntimeError" in headers["x-error"]


async def test_loi_vinh_vien_khong_thu_lai_lan_nao(kafka: KafkaBroker):
    DA_NHAN.clear()
    await kafka.publish(TOPIC, {"ma": "D3", "kieu": "hong-vinh-vien"}, key="D3")
    await _pending(2)
    assert [t["lan_thu"] for t in DA_NHAN if t["nhom"] == "a"] == [1]


async def _doc_dlt(topic: str, so_tin: int) -> list:
    """Đọc topic chết bằng consumer dùng-một-lần, không commit gì."""
    import aiokafka

    consumer = aiokafka.AIOKafkaConsumer(
        topic,
        bootstrap_servers=SERVERS,
        group_id=None,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        found = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(found) < so_tin:
            batch = await consumer.getmany(timeout_ms=1000)
            for tin in batch.values():
                found.extend(tin)
        return found
    finally:
        await consumer.stop()


# ============================================== emit / send (khuôn NestJS)
RPC_TOPIC = "kiem-tra-rpc"


@injectable
class KafkaRpcService:
    @kafka_responder(RPC_TOPIC, group="kiem-tra-rpc-group")
    async def cong(self, data: dict) -> int:
        if data.get("no"):
            raise RuntimeError("hỏng cố ý")
        return data["a"] + data["b"]


@pytest.fixture
async def rpc_kafka(kafka_settings: Settings):
    broker = KafkaBroker(kafka_settings)
    await broker.startup()
    runner = KafkaResponderRunner(broker, kafka_settings)
    await runner.startup()
    await anyio.sleep(3)            # chờ consumer vào nhóm xong
    yield broker
    await runner.shutdown()
    await broker.shutdown()


async def test_send_nhan_lai_gia_tri(rpc_kafka: KafkaBroker):
    """Gán phân vùng tay thay vì vào consumer group, nên không phải chờ cân bằng."""
    assert await rpc_kafka.send(RPC_TOPIC, {"a": 4, "b": 6}, timeout=25) == 10


async def test_handler_hong_thi_bao_qua_header_nest_err(rpc_kafka: KafkaBroker):
    with pytest.raises(RpcRemoteError, match="hỏng cố ý"):
        await rpc_kafka.send(RPC_TOPIC, {"a": 1, "b": 1, "no": True}, timeout=25)
