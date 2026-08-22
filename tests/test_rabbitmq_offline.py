"""Test lớp RabbitMQ KHÔNG cần RabbitMQ.

Trọng tâm: dự án không dùng RabbitMQ thì mọi thứ phải chạy như chưa hề có nó,
và những thao tác cần nó phải báo lỗi rõ ràng kèm cách bật.

Phần cần broker thật nằm ở tests/test_rabbitmq.py.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from pymodular.core.config import DatabaseSettings, RabbitSettings, Settings
from pymodular.core.container import injectable
from pymodular.core.exceptions import BadRequestError, ComponentNotEnabledError
from pymodular.infrastructure.rabbitmq import (
    RabbitBroker,
    discover_rabbitmq_subscribers,
    rabbitmq_subscriber,
    validate_pattern,
    validate_routing_key,
)


# ------------------------------------------------------- kiểm tra khuôn mẫu
@pytest.mark.parametrize("pattern", ["", "  ", "alert..created", "alert.tạo", "alert.a b", "x" * 300])
def test_mau_sai_bi_tu_choi(pattern):
    with pytest.raises(BadRequestError):
        validate_pattern(pattern)


def test_routing_key_that_khong_duoc_chua_ky_tu_dai_dien():
    assert validate_routing_key(" alert.created ") == "alert.created"
    with pytest.raises(BadRequestError):
        validate_routing_key("alert.*")


# ------------------------------------------------- khi dự án KHÔNG dùng rabbit
def _settings(enabled: bool = False, **mq: Any) -> Settings:
    return Settings(
        APP_DB=DatabaseSettings(driver="memory"),
        APP_RABBITMQ=RabbitSettings(enabled=enabled, **mq),
    )


async def test_tat_thi_khoi_dong_khong_lam_gi():
    broker = RabbitBroker(_settings())
    await broker.startup()          # không import aio-pika, không mở kết nối
    assert broker.connected is False
    assert broker.stats() == {"enabled": False, "connected": False, "url": None, "exchanges": []}
    await broker.shutdown()


async def test_tat_ma_van_dang_tin_thi_bao_ro_cach_bat():
    broker = RabbitBroker(_settings())
    with pytest.raises(ComponentNotEnabledError) as loi:
        await broker.publish("events", "alert.created", {"id": 1})
    assert "APP_RABBITMQ__ENABLED" in str(loi.value)


def test_khong_co_cai_dat_khai_exchange():
    """Không có cách nào khai sẵn exchange qua config — chúng tự khai lúc dùng."""
    assert "exchanges" not in Settings().rabbitmq.model_fields


CO_AIO_PIKA = importlib.util.find_spec("aio_pika") is not None


@pytest.mark.skipif(not CO_AIO_PIKA, reason="cần thư viện aio-pika (không cần broker)")
async def test_noi_hong_giua_chung_thi_khong_de_lai_ket_noi_nua_voi(monkeypatch):
    """Mở được kết nối rồi hỏng ở bước sau -> phải dọn sạch, coi như chưa nối.

    Không dọn thì `connected` trả về True cho một kết nối dùng không được: vòng
    nối lại thấy "đang ổn" nên bỏ cuộc, còn log thì vừa báo degraded. Lỗi này
    lộ ra khi tôi thử khai một exchange sai kiểu lúc khởi động.
    """
    import aio_pika

    class KetNoiGia:
        is_closed = False

        def __init__(self) -> None:
            self.da_dong = False

        async def channel(self, **kwargs: Any) -> Any:
            raise RuntimeError("mở kênh hỏng")

        async def close(self) -> None:
            self.da_dong = True

    gia = KetNoiGia()

    async def connect_robust_gia(*args: Any, **kwargs: Any) -> KetNoiGia:
        return gia

    monkeypatch.setattr(aio_pika, "connect_robust", connect_robust_gia)

    broker = RabbitBroker(_settings(enabled=True))
    await broker.startup()          # không được ném lỗi ra ngoài: app vẫn phải chạy

    assert gia.da_dong is True, "kết nối nửa vời phải được đóng lại"
    assert broker.connected is False, "chưa dùng được thì không được báo là đã nối"
    await broker.shutdown()


def test_che_mat_khau_trong_log():
    from pymodular.infrastructure.rabbitmq.broker import safe_url

    assert safe_url("amqp://admin:sieubimat@rabbit:5672/") == "amqp://admin:***@rabbit:5672/"
    assert safe_url("amqp://localhost:5672/") == "amqp://localhost:5672/"


def test_app_van_chay_binh_thuong_khi_khong_co_rabbitmq(client):
    """Test quan trọng nhất của nhóm này: không cài, không bật -> không ảnh hưởng."""
    assert client.get("/api/users").status_code == 200
    assert client.get("/api/health/ready").json().get("rabbitmq") is None


# ------------------------------------------------------------ khai báo consumer
@injectable
class ConsumerMau:
    """Consumer chỉ dùng cho test — không phụ thuộc module ví dụ nào."""

    @rabbitmq_subscriber("events", "alert.#", queue="test-mac-dinh")
    async def mac_dinh(self, payload: dict, meta: dict) -> None: ...

    @rabbitmq_subscriber("events", "alert.#", queue="test-day-du", max_retries=3, dead_letter=True)
    async def day_du(self, payload: dict) -> None: ...

    @rabbitmq_subscriber("events", "live.#", queue="test-tam", durable=False, auto_delete=True)
    async def tam(self, payload: dict) -> None: ...


def test_tim_duoc_consumer_da_khai():
    specs = {spec.queue: spec for spec in discover_rabbitmq_subscribers()}
    assert "test-mac-dinh" in specs
    spec = specs["test-mac-dinh"]
    assert (spec.exchange, spec.routing_key) == ("events", "alert.#")
    assert spec.wants_meta is True       # có tham số thứ hai thì nhận thêm meta


def test_mac_dinh_chi_mot_hang_doi():
    """Không khai gì thêm thì broker chỉ mọc ra ĐÚNG MỘT hàng đợi."""
    specs = {spec.queue: spec for spec in discover_rabbitmq_subscribers()}
    mac_dinh = specs["test-mac-dinh"]
    assert mac_dinh.max_retries == 0, "không tự thử lại -> không có <queue>.retry"
    assert mac_dinh.dead_letter is False, "không tự giữ tin hỏng -> không có <queue>.dlq"
    assert (mac_dinh.durable, mac_dinh.prefetch) == (True, 20)
    # Mặc định GIỮ hàng đợi khi app tắt: tin gửi lúc deploy không được mất.
    assert mac_dinh.auto_delete is False

    # Hai hàng đợi phụ chỉ xuất hiện khi tự bật.
    day_du = specs["test-day-du"]
    assert (day_du.max_retries, day_du.dead_letter) == (3, True)
    # auto_delete phải đi trọn đường từ decorator tới spec mà runner đọc.
    assert specs["test-tam"].auto_delete is True
    assert specs["test-mac-dinh"].auto_delete is False


def test_moi_consumer_mot_kieu_khac_nhau():
    """Hai consumer cạnh nhau, hai chính sách khác hẳn — điều mà một biến môi
    trường chung không diễn đạt được."""

    @rabbitmq_subscriber("events", "mail.#", queue="q-mail", max_retries=5, retry_delay=60)
    async def gui_mail(self, payload: dict) -> None: ...

    @rabbitmq_subscriber("events", "metric.#", queue="q-metric", durable=False, prefetch=200)
    async def ghi_so_do(self, payload: dict) -> None: ...

    mail = gui_mail.__rabbitmq_subscriber__
    metric = ghi_so_do.__rabbitmq_subscriber__
    assert (mail.max_retries, mail.retry_delay, mail.durable) == (5, 60, True)
    assert (metric.max_retries, metric.dead_letter) == (0, False), "mặc định là không gì cả"
    assert (metric.durable, metric.prefetch) == (False, 200)


def test_auto_delete_phai_khai_ro_moi_co():
    """Xoá hàng đợi lúc app tắt là mất tin, nên không bao giờ là mặc định."""

    @rabbitmq_subscriber("events", "live.#", queue="q-live", durable=False, auto_delete=True)
    async def theo_doi_truc_tiep(self, payload: dict) -> None: ...

    live = theo_doi_truc_tiep.__rabbitmq_subscriber__
    assert (live.auto_delete, live.durable) == (True, False)

    # Không truyền thì giữ lại — kiểm tra ở cả decorator lẫn spec quét được.
    @rabbitmq_subscriber("events", "keep.#", queue="q-keep")
    async def giu_lai(self, payload: dict) -> None: ...

    assert giu_lai.__rabbitmq_subscriber__.auto_delete is False


class _BrokerGia:
    """Broker giả, chỉ ghi lại xem hàng đợi nào bị xoá."""

    def __init__(self, *, con_hang_doi_chinh: bool) -> None:
        self._con = con_hang_doi_chinh
        self.da_xoa: list[str] = []
        self.connected = True

    async def queue_exists(self, name: str) -> bool:
        return self._con

    async def delete_queue(self, name: str, *, if_unused: bool = True) -> bool:
        self.da_xoa.append(name)
        return True


class _HangDoiGia:
    async def cancel(self, tag: str) -> None: ...


async def _tat_runner(broker, spec):
    from pymodular.core.config import Settings
    from pymodular.infrastructure.rabbitmq.consumers import RabbitmqRunner

    runner = RabbitmqRunner(broker, Settings())
    runner._started[spec.queue] = (_HangDoiGia(), "tag", spec)
    await runner.shutdown()
    return broker.da_xoa


@pytest.mark.asyncio
async def test_auto_delete_don_luon_retry_va_dlq():
    """auto_delete của AMQP không xoá được .retry/.dlq — khung phải tự làm."""

    @rabbitmq_subscriber("events", "live.#", queue="q-don", auto_delete=True,
                max_retries=3, dead_letter=True)
    async def h(self, payload: dict) -> None: ...

    # Hàng đợi chính đã tự biến mất -> mình là worker cuối, dọn nốt phần phụ.
    da_xoa = await _tat_runner(_BrokerGia(con_hang_doi_chinh=False), h.__rabbitmq_subscriber__)
    assert da_xoa == ["q-don.retry", "q-don.dlq"]


@pytest.mark.asyncio
async def test_khong_don_khi_worker_khac_con_nghe():
    """Dọn sớm là cướp hàng đợi thử lại của worker đang chạy."""

    @rabbitmq_subscriber("events", "live.#", queue="q-con-nguoi-nghe", auto_delete=True,
                max_retries=3, dead_letter=True)
    async def h(self, payload: dict) -> None: ...

    da_xoa = await _tat_runner(_BrokerGia(con_hang_doi_chinh=True), h.__rabbitmq_subscriber__)
    assert da_xoa == []


@pytest.mark.asyncio
async def test_mac_dinh_khong_xoa_gi_khi_tat():
    """auto_delete=False: tắt app rồi bật lại phải thấy y nguyên tin tồn đọng."""

    @rabbitmq_subscriber("events", "keep.#", queue="q-o-lai")
    async def h(self, payload: dict) -> None: ...

    da_xoa = await _tat_runner(_BrokerGia(con_hang_doi_chinh=False), h.__rabbitmq_subscriber__)
    assert da_xoa == []


def test_consumer_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @rabbitmq_subscriber("events", "a.b", queue="q")
        def dong_bo(self, payload: dict) -> None: ...


def test_consumer_mau_sai_bi_tu_choi_ngay_luc_khai_bao():
    with pytest.raises(BadRequestError):

        @rabbitmq_subscriber("events", "a..b", queue="q")
        async def sai(self, payload: dict) -> None: ...
