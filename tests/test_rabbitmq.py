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
from pydantic import BaseModel

from fastapi_modular.core.config import DatabaseSettings, RabbitSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.rpc import RpcRemoteError, RpcTimeoutError
from fastapi_modular.factory import create_app
from fastapi_modular.infrastructure.rabbitmq import (
    PermanentMessageError,
    RabbitBroker,
    rabbitmq_responder,
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
    async def label_(self, payload: dict, meta: dict) -> None:
        DA_NHAN.append({"payload": payload, "routing_key": meta["routing_key"]})


@injectable
class ConsumerDayDu:
    """Bật cả thử lại lẫn hàng đợi chết -> mọc thêm hai hàng đợi phụ."""

    @rabbitmq_subscriber("events", "daydu.#", queue=DAY_DU_QUEUE, max_retries=3, dead_letter=True)
    async def label_(self, payload: dict) -> None: ...


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

    def declared_kind() -> list[str]:
        return mq_client.get("/api/health/ready").json()["rabbitmq"]["exchanges"]

    assert "events" in declared_kind(), "consumer phải tự khai exchange của nó lúc boot"
    assert "audit" not in declared_kind(), "chưa ai dùng thì chưa khai"

    publish(mq_client, "user.login.web", {"u": "an"}, exchange="audit")
    assert "audit" in declared_kind(), "khai lúc dùng lần đầu"


# ------------------------------------------------------------ consumer nền
async def test_consumer_nen_nhan_duoc_tin(mq_client: TestClient):
    DA_NHAN.clear()
    publish(mq_client, "relay.created.hanoi", {"id": "A9"})

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not DA_NHAN:
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
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and ConsumerHayHong.lan_goi < 2:
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
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
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


# ================================================== các kiểu exchange khác
# Bốn kiểu còn lại chỉ chứng minh được với broker thật: RabbitMQ nhận lệnh bind
# sai kiểu mà không kêu một tiếng nào, nên "khai đúng" và "route đúng" là hai
# chuyện khác nhau.
FANOUT_A = "test-fanout-a"
FANOUT_B = "test-fanout-b"
HEADER_HANOI = "test-header-hanoi"
HEADER_HCM = "test-header-hcm"
DIRECT_QUEUE = "test-direct"
THANG_QUEUE = "test-vao-thang"

DA_NHAN_FANOUT: list[str] = []
DA_NHAN_HEADER: list[tuple[str, dict]] = []
DA_NHAN_DIRECT: list[dict] = []
DA_NHAN_THANG: list[dict] = []

_TAM = {"durable": False, "auto_delete": True}   # hàng đợi test, tắt app là dọn sạch


@injectable
class ConsumerPhatTan:
    """fanout: MỖI hàng đợi một bản sao, không ai lọc gì."""

    @rabbitmq_subscriber("test-broadcast", queue=FANOUT_A, exchange_type="fanout", **_TAM)
    async def a(self, payload: dict) -> None:
        DA_NHAN_FANOUT.append("a")

    @rabbitmq_subscriber("test-broadcast", queue=FANOUT_B, exchange_type="fanout", **_TAM)
    async def b(self, payload: dict) -> None:
        DA_NHAN_FANOUT.append("b")


@injectable
class ConsumerTheoHeader:
    """headers: chọn hàng đợi theo HEADER của tin, routing key không có vai trò."""

    @rabbitmq_subscriber(
        "test-headers", queue=HEADER_HANOI, exchange_type="headers",
        headers_match={"vung": "hanoi"}, **_TAM,
    )
    async def hanoi(self, payload: dict) -> None:
        DA_NHAN_HEADER.append(("hanoi", payload))

    @rabbitmq_subscriber(
        "test-headers", queue=HEADER_HCM, exchange_type="headers",
        headers_match={"vung": "hcm"}, **_TAM,
    )
    async def hcm(self, payload: dict) -> None:
        DA_NHAN_HEADER.append(("hcm", payload))


@injectable
class ConsumerTrungKhit:
    """direct: routing key phải trùng khít, không có mẫu nào cả."""

    @rabbitmq_subscriber(
        "test-cmd", "device.reboot", queue=DIRECT_QUEUE, exchange_type="direct", **_TAM
    )
    async def label_(self, payload: dict) -> None:
        DA_NHAN_DIRECT.append(payload)


@injectable
class ConsumerVaoThang:
    """exchange mặc định: tin đi thẳng vào hàng đợi trùng tên, không bind gì."""

    @rabbitmq_subscriber("", queue=THANG_QUEUE, **_TAM)
    async def label_(self, payload: dict) -> None:
        DA_NHAN_THANG.append(payload)


async def _pending(dieu_kien, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not dieu_kien():
        await anyio.sleep(0.1)


async def test_fanout_moi_hang_doi_mot_ban_sao(mq_client: TestClient, mq_settings: Settings):
    DA_NHAN_FANOUT.clear()
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        await broker.publish("test-broadcast", payload={"id": 1}, exchange_type="fanout")
        await _pending(lambda: len(DA_NHAN_FANOUT) >= 2)
    finally:
        await broker.shutdown()

    assert sorted(DA_NHAN_FANOUT) == ["a", "b"], "fanout phải tới CẢ HAI hàng đợi"


async def test_headers_chi_toi_hang_doi_khop_header(mq_client: TestClient, mq_settings: Settings):
    DA_NHAN_HEADER.clear()
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        # routing key để trống hoàn toàn — headers exchange không nhìn tới nó.
        await broker.publish(
            "test-headers", payload={"id": "H1"},
            exchange_type="headers", headers={"vung": "hanoi"},
        )
        await _pending(lambda: DA_NHAN_HEADER)
        await anyio.sleep(0.5)      # để hàng đợi kia có cơ hội nhận nhầm
    finally:
        await broker.shutdown()

    assert DA_NHAN_HEADER == [("hanoi", {"id": "H1"})]


async def test_direct_khong_khop_mau_nao_ca(mq_client: TestClient, mq_settings: Settings):
    DA_NHAN_DIRECT.clear()
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        await broker.publish("test-cmd", "device.reboot.gap", {"sai": True}, exchange_type="direct")
        await broker.publish("test-cmd", "device.reboot", {"dung": True}, exchange_type="direct")
        await _pending(lambda: DA_NHAN_DIRECT)
        await anyio.sleep(0.5)
    finally:
        await broker.shutdown()

    # Với topic thì "device.reboot.#" bắt cả hai. direct thì chỉ khớp trùng khít.
    assert DA_NHAN_DIRECT == [{"dung": True}]


async def test_exchange_mac_dinh_di_thang_vao_hang_doi(
    mq_client: TestClient, mq_settings: Settings
):
    DA_NHAN_THANG.clear()
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        await broker.publish("", THANG_QUEUE, {"id": "T1"})
        await _pending(lambda: DA_NHAN_THANG)
    finally:
        await broker.shutdown()

    assert DA_NHAN_THANG == [{"id": "T1"}]


# ------------------------------------------------------------------- TTL
async def test_tin_qua_han_o_hang_doi_thi_roi_vao_dlq(mq_settings: Settings):
    """TTL chỉ có ý nghĩa khi KHÔNG ai đang nghe — có consumer thì tin được lấy
    ngay, chẳng bao giờ kịp hết hạn. Nên hàng đợi này cố ý không có consumer."""
    label = "test-ttl-hang-doi"
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        channel = await broker.new_channel()
        await broker.durable_queue(channel, label, message_ttl=1, dead_letter=True)
        await broker.publish("", label, {"x": 1})

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            dlq = await broker.queue_info(f"{label}.dlq")
            if dlq and dlq["messages"] >= 1:
                break
            await anyio.sleep(0.2)

        assert (await broker.queue_info(f"{label}.dlq"))["messages"] >= 1, "hết hạn mà không vào .dlq"
        assert (await broker.queue_info(label))["messages"] == 0, "tin phải rời hàng đợi chính"
    finally:
        await broker.delete_queue(label)
        await broker.delete_queue(f"{label}.dlq")
        await broker.shutdown()


async def test_ttl_dat_rieng_cho_mot_tin(mq_settings: Settings):
    """`publish(ttl=…)` đặt hạn cho RIÊNG tin đó, không đụng tới hàng đợi — nên
    đổi lúc nào cũng được, không dính PRECONDITION_FAILED như TTL của hàng đợi."""
    label = "test-ttl-mot-tin"
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        channel = await broker.new_channel()
        await broker.durable_queue(channel, label, dead_letter=True)   # hàng đợi KHÔNG có TTL
        await broker.publish("", label, {"x": 1}, ttl=1)
        await broker.publish("", label, {"x": 2})                      # tin này ở lại

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            dlq = await broker.queue_info(f"{label}.dlq")
            if dlq and dlq["messages"] >= 1:
                break
            await anyio.sleep(0.2)

        assert (await broker.queue_info(f"{label}.dlq"))["messages"] == 1
        assert (await broker.queue_info(label))["messages"] == 1, "tin không đặt hạn phải ở lại"
    finally:
        await broker.delete_queue(label)
        await broker.delete_queue(f"{label}.dlq")
        await broker.shutdown()


# =================================================== emit / send (khuôn NestJS)
# Khuôn tin ở đây tương thích @nestjs/microservices. Phần đối chứng với một
# service NestJS THẬT không chạy trong bộ test này (sẽ phải kéo Node vào CI);
# cách dựng lại nằm ở mục "Đối chứng với NestJS thật" trong docs/rpc.md.
RPC_QUEUE = "test-rpc"


class ThamSo(BaseModel):
    ma: str
    so_luong: int


DA_NHAN_SU_KIEN: list[dict] = []


@injectable
class DichVuTraLoi:
    @rabbitmq_responder("sum", queue=RPC_QUEUE, durable=False)
    async def gateway(self, data: list[int]) -> int:
        return sum(data)

    @rabbitmq_responder({"cmd": "info"}, queue=RPC_QUEUE, durable=False)
    async def thong_tin(self, data: dict, meta: dict) -> dict:
        return {"echo": data, "pattern": meta["pattern"]}

    @rabbitmq_responder("boom", queue=RPC_QUEUE, durable=False)
    async def no(self, data: dict) -> None:
        raise RuntimeError("hỏng cố ý")

    @rabbitmq_responder("cham", queue=RPC_QUEUE, durable=False)
    async def cham(self, data: dict) -> str:
        await anyio.sleep(5)
        return "muộn quá rồi"

    @rabbitmq_responder("dat-hang", queue=RPC_QUEUE, durable=False)
    async def dat_hang(self, don: ThamSo) -> dict:
        return {"ma": don.ma, "thanh_tien": don.so_luong * 1000}

    @rabbitmq_responder("ghi-nhan", queue=RPC_QUEUE, durable=False)
    async def ghi_nhan(self, data: dict) -> str:
        DA_NHAN_SU_KIEN.append(data)
        return "khong-ai-doc"


async def test_send_nhan_lai_dung_gia_tri_handler_tra_ve(mq_client, mq_settings):
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        assert await broker.send("sum", [1, 2, 3, 4], queue=RPC_QUEUE) == 10
        # pattern dạng dict: phải chuỗi hoá giống hệt hai bên mới tra được bảng
        assert await broker.send({"cmd": "info"}, {"ai": "py"}, queue=RPC_QUEUE) == {
            "echo": {"ai": "py"},
            "pattern": '{"cmd":"info"}',
        }
        # model Pydantic được kiểm khuôn trước khi vào handler
        assert await broker.send("dat-hang", {"ma": "A1", "so_luong": 3}, queue=RPC_QUEUE) == {
            "ma": "A1",
            "thanh_tien": 3000,
        }
    finally:
        await broker.shutdown()


async def test_handler_hong_thi_bao_ngay_chu_khong_de_nguoi_goi_doi_het_gio(
    mq_client, mq_settings
):
    """Biết hỏng vì gì (502) khác hẳn không biết gì (504) — đừng trả về cái sau."""
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        with pytest.raises(RpcRemoteError, match="hỏng cố ý"):
            await broker.send("boom", {}, queue=RPC_QUEUE, timeout=10)

        with pytest.raises(RpcRemoteError, match="Payload không hợp lệ"):
            await broker.send("dat-hang", {"ma": "A1"}, queue=RPC_QUEUE, timeout=10)
    finally:
        await broker.shutdown()


async def test_khong_co_responder_thi_tra_dung_nguyen_van_cua_nestjs(mq_client, mq_settings):
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        with pytest.raises(RpcRemoteError, match="no matching message handler"):
            await broker.send("khong-he-co", {}, queue=RPC_QUEUE, timeout=10)
    finally:
        await broker.shutdown()


async def test_het_gio_thi_nem_504_va_khong_de_lai_rac(mq_client, mq_settings):
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        with pytest.raises(RpcTimeoutError):
            await broker.send("cham", {}, queue=RPC_QUEUE, timeout=0.5)
        # Sổ chờ phải sạch, nếu không mỗi lời gọi hỏng để lại một chỗ vĩnh viễn.
        assert len(broker._pending) == 0
    finally:
        await broker.shutdown()


async def test_emit_khong_cho_va_ket_qua_bi_bo(mq_client, mq_settings):
    """`emit` là sự kiện: handler vẫn chạy, nhưng không ai đọc giá trị trả về."""
    DA_NHAN_SU_KIEN.clear()
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        await broker.emit("ghi-nhan", {"tu": "test"}, queue=RPC_QUEUE)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not DA_NHAN_SU_KIEN:
            await anyio.sleep(0.1)
    finally:
        await broker.shutdown()

    assert DA_NHAN_SU_KIEN == [{"tu": "test"}]


async def test_goi_nhieu_lan_khong_de_lai_cho_cho_nao(mq_client, mq_settings):
    """Dùng `amq.rabbitmq.reply-to` nên không phải khai hàng đợi trả lời nào.

    Test này chứng minh phần kiểm được từ trong tiến trình: gọi liên tiếp đều
    trả đúng, và sổ chờ rỗng sau mỗi lần. Phần "broker không mọc thêm hàng đợi"
    kiểm bằng `rabbitmqctl list_queues` — xem docs/rpc.md.
    """
    broker = RabbitBroker(mq_settings)
    await broker.startup()
    try:
        for i in range(5):
            assert await broker.send("sum", [i, i], queue=RPC_QUEUE) == i * 2
            assert len(broker._pending) == 0
    finally:
        await broker.shutdown()
