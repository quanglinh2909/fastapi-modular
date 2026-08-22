"""Test cần RabbitMQ THẬT.

Mặc định bỏ qua để `make test` chạy được trên máy trắng. Chạy đầy đủ:

    docker run -d --name rabbit-test -p 5673:5672 rabbitmq:3.13-management-alpine
    make install-rabbitmq
    TEST_RABBITMQ_URL=amqp://guest:guest@localhost:5673/ make test
"""

from __future__ import annotations

import importlib.util
import os
import time

import anyio
import pytest
from fastapi.testclient import TestClient

from pymodular.core.config import DatabaseSettings, RabbitSettings, Settings
from pymodular.core.container import injectable
from pymodular.factory import create_app
from pymodular.infrastructure.rabbitmq import (
    PermanentMessageError,
    RabbitBroker,
    rabbitmq_subscriber,
)

CO_AIO_PIKA = importlib.util.find_spec("aio_pika") is not None
MQ_URL = os.getenv("TEST_RABBITMQ_URL")

pytestmark = pytest.mark.skipif(
    not (CO_AIO_PIKA and MQ_URL), reason="cần aio-pika + TEST_RABBITMQ_URL"
)

HONG_QUEUE = "test-hay-hong"
VINH_VIEN_QUEUE = "test-hong-vinh-vien"
NHAN_QUEUE = "test-nhan-tin"
DAY_DU_QUEUE = "test-day-du"

DA_NHAN: list[dict] = []


@injectable
class ConsumerNhanTin:
    @rabbitmq_subscriber("events", "relay.#", queue=NHAN_QUEUE)
    async def nhan(self, payload: dict, meta: dict) -> None:
        DA_NHAN.append({"payload": payload, "routing_key": meta["routing_key"]})


@injectable
class ConsumerDayDu:
    """Bật cả thử lại lẫn hàng đợi chết -> mọc thêm hai hàng đợi phụ."""

    @rabbitmq_subscriber("events", "daydu.#", queue=DAY_DU_QUEUE, max_retries=3, dead_letter=True)
    async def nhan(self, payload: dict) -> None: ...


@injectable
class ConsumerHayHong:
    """Consumer cố tình lỗi — để kiểm chứng đường thử lại và hàng đợi chết."""

    lan_goi = 0

    @rabbitmq_subscriber("events", "hong.#", queue=HONG_QUEUE, max_retries=1, retry_delay=0.5,
                dead_letter=True)
    async def luon_hong(self, payload: dict) -> None:
        type(self).lan_goi += 1
        raise RuntimeError("cố tình hỏng")


@injectable
class ConsumerHongVinhVien:
    """Lỗi vĩnh viễn: phải vào thẳng `.dlq`, không tiêu lượt thử lại nào."""

    lan_goi = 0

    @rabbitmq_subscriber("events", "vv.#", queue=VINH_VIEN_QUEUE, max_retries=3, retry_delay=0.5,
                dead_letter=True)
    async def luon_hong(self, payload: dict) -> None:
        type(self).lan_goi += 1
        raise PermanentMessageError("dữ liệu sai vĩnh viễn")


# ------------------------------------------------------------------ tiện ích
@pytest.fixture
def mq_settings() -> Settings:
    return Settings(
        APP_ENV="local",
        APP_DEBUG=True,
        APP_DB=DatabaseSettings(driver="memory"),
        APP_RABBITMQ=RabbitSettings(enabled=True, url=MQ_URL or ""),
    )


@pytest.fixture
def mq_client(mq_settings: Settings):
    with TestClient(create_app(mq_settings)) as client:
        yield client


def publish(client: TestClient, routing_key: str, data: dict, exchange: str = "events") -> None:
    response = client.post(
        "/api/chat/publish",
        json={"exchange": exchange, "routing_key": routing_key, "data": data},
    )
    assert response.status_code == 200, response.text
    assert response.json()["published"] is True


# --------------------------------------------------------------- kết nối
def test_khoi_dong_thi_noi_duoc_broker(mq_client: TestClient):
    ready = mq_client.get("/api/health/ready").json()
    assert ready["rabbitmq"]["connected"] is True
    assert "***" in ready["rabbitmq"]["url"] or "@" not in ready["rabbitmq"]["url"]   # mật khẩu bị che


def test_exchange_tu_khai_luc_dung(mq_client: TestClient):
    """Không có cấu hình nào khai exchange — chúng tự xuất hiện đúng lúc cần.

    - "events" được khai NGAY LÚC BOOT vì có @rabbitmq_subscriber dùng nó.
    - "audit" không ai khai trước, được khai lúc publish lần đầu.
    """

    def da_khai() -> list[str]:
        return mq_client.get("/api/health/ready").json()["rabbitmq"]["exchanges"]

    assert "events" in da_khai(), "consumer phải tự khai exchange của nó lúc boot"
    assert "audit" not in da_khai(), "chưa ai dùng thì chưa khai"

    publish(mq_client, "user.login.web", {"u": "an"}, exchange="audit")
    assert "audit" in da_khai(), "khai lúc dùng lần đầu"


# ------------------------------------------------------------ consumer nền
async def test_consumer_nen_nhan_duoc_tin(mq_client: TestClient):
    DA_NHAN.clear()
    publish(mq_client, "relay.created.hanoi", {"id": "A9"})

    han = time.monotonic() + 10
    while time.monotonic() < han and not DA_NHAN:
        await anyio.sleep(0.1)

    assert DA_NHAN == [{"payload": {"id": "A9"}, "routing_key": "relay.created.hanoi"}]


async def test_chi_tao_hang_doi_phu_khi_that_su_dung(mq_client: TestClient):
    """`.retry` và `.dlq` chỉ mọc ra khi consumer TỰ BẬT chúng."""
    import aio_pika

    connection = await aio_pika.connect_robust(MQ_URL)
    try:

        async def co_hang_doi(name: str) -> bool:
            # Khai báo passive: có thì trả về, không có thì lỗi và ĐÓNG kênh —
            # nên mỗi lần hỏi phải một kênh riêng.
            channel = await connection.channel()
            try:
                await channel.declare_queue(name, passive=True)
                return True
            except aio_pika.exceptions.ChannelNotFoundEntity:
                return False
            finally:
                if not channel.is_closed:
                    await channel.close()

        # Mặc định: ĐÚNG MỘT hàng đợi, không có gì thêm.
        assert await co_hang_doi(NHAN_QUEUE)
        assert not await co_hang_doi(f"{NHAN_QUEUE}.retry")
        assert not await co_hang_doi(f"{NHAN_QUEUE}.dlq")

        # Tự bật thì mới mọc thêm hai hàng đợi phụ.
        assert await co_hang_doi(DAY_DU_QUEUE)
        assert await co_hang_doi(f"{DAY_DU_QUEUE}.retry")
        assert await co_hang_doi(f"{DAY_DU_QUEUE}.dlq")
    finally:
        await connection.close()


async def test_tin_hong_di_qua_retry_roi_vao_hang_doi_chet(mq_client: TestClient):
    import aio_pika

    ConsumerHayHong.lan_goi = 0
    publish(mq_client, "hong.mot", {"x": 1})

    # 1 lần đầu + 1 lần thử lại = 2, rồi tin phải nằm ở <queue>.dlq.
    han = time.monotonic() + 15
    while time.monotonic() < han and ConsumerHayHong.lan_goi < 2:
        await anyio.sleep(0.2)
    assert ConsumerHayHong.lan_goi == 2, "phải thử lại đúng một lần rồi thôi"

    connection = await aio_pika.connect_robust(MQ_URL)
    try:
        channel = await connection.channel()
        await anyio.sleep(0.5)
        dlq = await channel.declare_queue(f"{HONG_QUEUE}.dlq", durable=True, passive=True)
        assert dlq.declaration_result.message_count >= 1
        # Dọn để chạy lại test không bị cộng dồn.
        await channel.queue_delete(f"{HONG_QUEUE}.dlq")
    finally:
        await connection.close()


async def test_loi_vinh_vien_khong_thu_lai_lan_nao(mq_client: TestClient, mq_settings: Settings):
    """PermanentMessageError bỏ qua mọi lượt thử — và `peek` đọc được tin đó."""
    ConsumerHongVinhVien.lan_goi = 0
    publish(mq_client, "vv.mot", {"x": 1})

    # Broker riêng cho test: mq_client chạy app trên vòng lặp khác, dùng chung
    # đối tượng kết nối giữa hai vòng lặp là lỗi "attached to a different loop".
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        han = time.monotonic() + 15
        while time.monotonic() < han:
            info = await broker.queue_info(f"{VINH_VIEN_QUEUE}.dlq")
            if info and info["messages"] >= 1:
                break
            await anyio.sleep(0.2)

        assert ConsumerHongVinhVien.lan_goi == 1, "lỗi vĩnh viễn thì không được thử lại"
        assert (await broker.queue_info(f"{VINH_VIEN_QUEUE}.retry"))["messages"] == 0

        tin = await broker.peek(f"{VINH_VIEN_QUEUE}.dlq")
        assert tin[0]["body"] == {"x": 1}
        assert "x-attempt" not in tin[0]["headers"], "rớt ngay lần đầu thì không có header này"

        # peek trả tin lại chỗ cũ -> xem bao nhiêu lần cũng ra cùng kết quả.
        assert len(await broker.peek(f"{VINH_VIEN_QUEUE}.dlq")) == len(tin)
        assert (await broker.queue_info(f"{VINH_VIEN_QUEUE}.dlq"))["messages"] == len(tin)

        assert await broker.queue_info("khong-he-ton-tai") is None
        assert await broker.peek("khong-he-ton-tai") == []
    finally:
        await broker.delete_queue(f"{VINH_VIEN_QUEUE}.dlq")   # dọn để chạy lại không cộng dồn
        await broker.shutdown()
