"""Test lớp RabbitMQ KHÔNG cần RabbitMQ.

Trọng tâm: dự án không dùng RabbitMQ thì mọi thứ phải chạy như chưa hề có nó,
và những thao tác cần nó phải báo lỗi rõ ràng kèm cách bật.

Phần cần broker thật nằm ở tests/test_rabbitmq.py.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from fastapi_modular.core.config import DatabaseSettings, RabbitSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import BadRequestError, ComponentNotEnabledError
from fastapi_modular.infrastructure.rabbitmq import (
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
    from fastapi_modular.infrastructure.rabbitmq.broker import safe_url

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
    from fastapi_modular.core.config import Settings
    from fastapi_modular.infrastructure.rabbitmq.consumers import RabbitmqRunner

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


# --------------------------------------------------- năm kiểu exchange
# RabbitMQ KHÔNG báo lỗi khi bind sai kiểu: đưa routing key cho một exchange
# fanout thì broker nhận rồi lờ đi, và hàng đợi âm thầm nhận mọi tin. Nên toàn
# bộ phần kiểm ở đây chạy lúc KHAI BÁO, trước khi có tin nào đi sai chỗ.
def test_mac_dinh_van_la_topic():
    @rabbitmq_subscriber("events", "alert.#", queue="q-topic")
    async def h(self, payload: dict) -> None: ...

    spec = h.__rabbitmq_subscriber__
    assert (spec.exchange_type, spec.routing_key) == ("topic", "alert.#")
    assert spec.bind_arguments is None


def test_exchange_ten_rong_tu_hieu_la_exchange_mac_dinh():
    @rabbitmq_subscriber("", queue="q-thang")
    async def h(self, payload: dict) -> None: ...

    assert h.__rabbitmq_subscriber__.exchange_type == "default"


def test_direct_doi_routing_key_trung_khit():
    @rabbitmq_subscriber("cmd", "device.reboot", queue="q-direct", exchange_type="direct")
    async def h(self, payload: dict) -> None: ...

    assert h.__rabbitmq_subscriber__.exchange_type == "direct"

    with pytest.raises(BadRequestError, match=r"đại diện|không hợp lệ"):

        @rabbitmq_subscriber("cmd", "device.*", queue="q-direct-sai", exchange_type="direct")
        async def sai(self, payload: dict) -> None: ...


@pytest.mark.parametrize("kieu", ["fanout", "headers", "default"])
def test_kieu_khong_dung_routing_key_thi_cam_khai_routing_key(kieu):
    """Đưa routing key cho fanout là hiểu nhầm — nó nhận MỌI tin, không lọc."""
    ten = "" if kieu == "default" else "broadcast"
    with pytest.raises(BadRequestError, match="không dùng routing key"):

        @rabbitmq_subscriber(ten, "alert.#", queue="q", exchange_type=kieu)
        async def sai(self, payload: dict) -> None: ...


def test_fanout_moi_worker_mot_ban_sao():
    @rabbitmq_subscriber("broadcast", queue="q-fanout", exchange_type="fanout")
    async def h(self, payload: dict) -> None: ...

    spec = h.__rabbitmq_subscriber__
    assert (spec.exchange_type, spec.routing_key, spec.bind_arguments) == ("fanout", "", None)


def test_headers_loc_bang_header_chu_khong_phai_routing_key():
    @rabbitmq_subscriber(
        "audit", queue="q-headers", exchange_type="headers",
        headers_match={"vung": "hanoi", "loai": "don"}, match="any",
    )
    async def h(self, payload: dict) -> None: ...

    spec = h.__rabbitmq_subscriber__
    assert spec.bind_arguments == {"x-match": "any", "vung": "hanoi", "loai": "don"}


def test_headers_khong_co_dieu_kien_thi_khong_khop_tin_nao():
    with pytest.raises(BadRequestError, match="headers_match"):

        @rabbitmq_subscriber("audit", queue="q", exchange_type="headers")
        async def sai(self, payload: dict) -> None: ...


def test_headers_match_o_kieu_khac_la_vo_nghia_nen_bi_chan():
    """Đặt headers_match cho topic thì không ai lọc theo nó — im lặng sai."""
    with pytest.raises(BadRequestError, match="chỉ có tác dụng"):

        @rabbitmq_subscriber("events", "a.b", queue="q", headers_match={"x": "1"})
        async def sai(self, payload: dict) -> None: ...


def test_x_match_phai_dat_qua_tham_so_match():
    with pytest.raises(BadRequestError, match="x-match"):

        @rabbitmq_subscriber(
            "audit", queue="q", exchange_type="headers", headers_match={"x-match": "all"}
        )
        async def sai(self, payload: dict) -> None: ...


def test_kieu_khong_co_that_bi_tu_choi():
    with pytest.raises(BadRequestError, match="Kiểu exchange"):

        @rabbitmq_subscriber("events", "a.b", queue="q", exchange_type="round-robin")
        async def sai(self, payload: dict) -> None: ...


def test_ten_exchange_va_kieu_default_phai_di_cung_nhau():
    with pytest.raises(BadRequestError):

        @rabbitmq_subscriber("events", queue="q", exchange_type="default")
        async def co_ten(self, payload: dict) -> None: ...

    with pytest.raises(BadRequestError, match="MẶC ĐỊNH"):

        @rabbitmq_subscriber("", "a.b", queue="q", exchange_type="topic")
        async def khong_ten(self, payload: dict) -> None: ...


# ------------------------------------------------------------------ TTL
class _KenhGia:
    """Kênh giả: ghi lại đúng tham số mà khung gửi xuống AMQP."""

    def __init__(self) -> None:
        self.hang_doi: dict[str, dict] = {}
        self.exchange_da_khai: list[tuple[str, str]] = []

    async def declare_queue(self, name, **kwargs):
        self.hang_doi[name] = kwargs
        return _HangDoiGhiLai(name)

    async def declare_exchange(self, name, kieu, **kwargs):
        self.exchange_da_khai.append((name, str(getattr(kieu, "value", kieu))))
        return name

    async def set_qos(self, **_):
        return None


class _HangDoiGhiLai:
    def __init__(self, name: str) -> None:
        self.name = name
        self.da_bind: list[dict] = []

    async def bind(self, exchange, routing_key=None, arguments=None, **_):
        self.da_bind.append(
            {"exchange": exchange, "routing_key": routing_key, "arguments": arguments}
        )

    async def consume(self, _callback):
        return "tag"


def _broker_gia_lap(monkeypatch) -> tuple[RabbitBroker, _KenhGia]:
    """Broker bật sẵn, bỏ qua kiểm tra kết nối — chỉ để soi tham số khai báo."""
    broker = RabbitBroker(_settings(enabled=True))
    kenh = _KenhGia()
    monkeypatch.setattr(broker, "_ready", lambda: None)
    broker._publish_channel = kenh
    return broker, kenh


async def test_ttl_khai_bang_giay_nhung_gui_xuong_amqp_bang_mili_giay(monkeypatch):
    """AMQP tính bằng mili-giây, khung tính bằng giây. Nhầm đơn vị ở đây không
    báo lỗi gì — chỉ là tin sống lâu gấp một nghìn lần."""
    broker, kenh = _broker_gia_lap(monkeypatch)
    await broker.durable_queue(kenh, "q-ttl", message_ttl=30, queue_expires=3600)

    tham_so = kenh.hang_doi["q-ttl"]["arguments"]
    assert tham_so["x-message-ttl"] == 30_000
    assert tham_so["x-expires"] == 3_600_000


async def test_khong_khai_ttl_thi_khong_them_tham_so_nao(monkeypatch):
    """Hàng đợi mặc định phải khai y như trước khi có TTL — nếu không, mọi hàng
    đợi đang chạy sẽ dính PRECONDITION_FAILED ngay lần nâng cấp thư viện."""
    broker, kenh = _broker_gia_lap(monkeypatch)
    await broker.durable_queue(kenh, "q-thuong")
    assert kenh.hang_doi["q-thuong"]["arguments"] is None


@pytest.mark.parametrize("xau", [0, -1])
async def test_ttl_khong_duong_bi_chan(monkeypatch, xau):
    broker, kenh = _broker_gia_lap(monkeypatch)
    with pytest.raises(Exception, match="lớn hơn 0 giây"):
        await broker.durable_queue(kenh, "q-xau", message_ttl=xau)


def test_ttl_di_tron_duong_tu_decorator_toi_spec():
    @rabbitmq_subscriber("events", "tam.#", queue="q-ttl", message_ttl=60, queue_expires=1800)
    async def h(self, payload: dict) -> None: ...

    spec = h.__rabbitmq_subscriber__
    assert (spec.message_ttl, spec.queue_expires) == (60, 1800)


# ------------------------------------- từ decorator xuống tới lệnh AMQP thật
async def _chay_setup(monkeypatch, spec) -> tuple[Any, _KenhGia]:
    """Chạy đúng đoạn dựng hàng đợi + bind của runner, trên kênh giả."""
    from fastapi_modular.infrastructure.rabbitmq.consumers import RabbitmqRunner

    broker, kenh = _broker_gia_lap(monkeypatch)

    async def new_channel(**_):
        return kenh

    monkeypatch.setattr(broker, "new_channel", new_channel)
    runner = RabbitmqRunner(broker, _settings(enabled=True))
    runner._specs = [spec]
    await runner._setup()
    # `_setup` nuốt mọi lỗi để một consumer hỏng không giết các consumer khác,
    # nên phải kiểm tích cực: có mặt ở đây tức là đã dựng xong, không ném.
    assert spec.queue in runner._started, "consumer không dựng được"
    return runner._started[spec.queue][0], kenh


async def test_exchange_mac_dinh_khong_bind_va_khong_khai_bao(monkeypatch):
    """AMQP CẤM bind tay vào exchange mặc định (ACCESS_REFUSED, đóng cả kênh).
    Không cần bind: hàng đợi đã sẵn nối với nó qua đúng tên của mình."""

    @rabbitmq_subscriber("", queue="viec-nen")
    async def h(self, payload: dict) -> None: ...

    hang_doi, kenh = await _chay_setup(monkeypatch, h.__rabbitmq_subscriber__)
    assert hang_doi.da_bind == []
    assert kenh.exchange_da_khai == []


async def test_fanout_khai_dung_kieu_va_bind_khong_routing_key(monkeypatch):
    @rabbitmq_subscriber("broadcast", queue="q-fan", exchange_type="fanout")
    async def h(self, payload: dict) -> None: ...

    hang_doi, kenh = await _chay_setup(monkeypatch, h.__rabbitmq_subscriber__)
    assert kenh.exchange_da_khai == [("broadcast", "fanout")]
    assert hang_doi.da_bind == [
        {"exchange": "broadcast", "routing_key": "", "arguments": None}
    ]


async def test_dieu_kien_header_di_toi_tan_lenh_bind(monkeypatch):
    """Chỗ dễ đứt nhất: decorator ghi nhận nhưng runner không chuyển tiếp."""

    @rabbitmq_subscriber(
        "audit", queue="q-hd", exchange_type="headers", headers_match={"vung": "hanoi"}
    )
    async def h(self, payload: dict) -> None: ...

    hang_doi, kenh = await _chay_setup(monkeypatch, h.__rabbitmq_subscriber__)
    assert kenh.exchange_da_khai == [("audit", "headers")]
    assert hang_doi.da_bind[0]["arguments"] == {"x-match": "all", "vung": "hanoi"}


async def test_ttl_cua_consumer_di_toi_tan_lenh_khai_hang_doi(monkeypatch):
    @rabbitmq_subscriber("events", "tam.#", queue="q-het-han", message_ttl=15)
    async def h(self, payload: dict) -> None: ...

    _, kenh = await _chay_setup(monkeypatch, h.__rabbitmq_subscriber__)
    assert kenh.hang_doi["q-het-han"]["arguments"]["x-message-ttl"] == 15_000


async def test_mot_exchange_hai_kieu_bi_chan_ngay_thay_vi_giet_ca_kenh(monkeypatch):
    """Khai lại exchange với kiểu khác là lỗi giao thức: RabbitMQ đóng KÊNH ĐĂNG
    TIN, kéo theo mọi lời publish khác của tiến trình, không chỉ lời gọi sai."""
    broker, _ = _broker_gia_lap(monkeypatch)
    await broker.exchange("events", "fanout")
    with pytest.raises(Exception, match="đã khai kiểu 'fanout'"):
        await broker.exchange("events", "topic")


async def test_ben_dang_tin_khong_phai_nhac_lai_kieu(monkeypatch):
    """Consumer đã khai fanout rồi thì publish("events", ...) dùng lại đúng kiểu
    đó — quên nhắc kiểu là chuyện chắc chắn xảy ra, và giá của nó là cả kênh."""
    broker, kenh = _broker_gia_lap(monkeypatch)
    await broker.exchange("events", "fanout")
    await broker.exchange("events")          # không nhắc kiểu
    assert kenh.exchange_da_khai == [("events", "fanout")], "không được khai lần hai"


def test_ttl_sai_bi_chan_ngay_luc_khai_bao():
    """Chặn ở decorator, không đợi tới lúc dựng hàng đợi: lỗi lúc dựng chỉ hiện
    trong log rồi app vẫn chạy — không có consumer mà cũng không ai chết."""
    with pytest.raises(BadRequestError, match="lớn hơn 0 giây"):

        @rabbitmq_subscriber("events", "a.b", queue="q", message_ttl=0)
        async def sai(self, payload: dict) -> None: ...

    with pytest.raises(BadRequestError, match="lớn hơn 0 giây"):

        @rabbitmq_subscriber("events", "a.b", queue="q", queue_expires=-5)
        async def sai_nua(self, payload: dict) -> None: ...
