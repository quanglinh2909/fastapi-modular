"""Ví dụ Kafka: một topic, hai nhóm consumer đọc cùng dòng tin.

Bật bằng `make install-kafka` rồi đặt APP_KAFKA__ENABLED=true.

Điểm khác RabbitMQ nhìn thấy rõ nhất ở đây: `kho_van` và `ke_toan` là hai
`group` khác nhau, nên MỖI NHÓM nhận đủ một bản sao của mọi tin. Thêm một nhóm
mới là đọc lại được cả nhật ký — hàng đợi không làm được vậy vì tin bị lấy đi
sau khi xử lý.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pymodular.core.container import injectable
from pymodular.core.logging import get_logger
from pymodular.infrastructure.kafka import PermanentMessageError, kafka_subscriber

log = get_logger(__name__)

TOPIC = "don-hang"

DA_NHAN: list[dict] = []


class DonHang(BaseModel):
    ma_don: str
    tien: float = 0
    kieu: str = Field(default="ok", description="ok | hong-tam-thoi | hong-vinh-vien")


@injectable
class KhoVanConsumer:
    @kafka_subscriber(
        TOPIC,
        group="kho-van",
        auto_offset_reset="earliest",   # nhóm mới đọc lại từ đầu nhật ký
        max_retries=2,
        retry_delay=0.5,                # để NHỎ: thử lại làm đứng cả phân vùng
    )
    async def giao_hang(self, don: DonHang, meta: dict) -> None:
        _ghi("kho-van", don, meta)
        if don.kieu == "hong-vinh-vien":
            raise PermanentMessageError(f"Đơn {don.ma_don} sai vĩnh viễn")
        if don.kieu == "hong-tam-thoi":
            raise RuntimeError(f"kho chưa phản hồi (lần {meta['attempt']})")


@injectable
class KeToanConsumer:
    """Cùng topic, khác group -> nhận bản sao riêng, con trỏ đọc riêng."""

    @kafka_subscriber(TOPIC, group="ke-toan", auto_offset_reset="earliest", max_retries=0)
    async def ghi_so(self, don: DonHang, meta: dict) -> None:
        _ghi("ke-toan", don, meta)


def _ghi(nhom: str, don: DonHang, meta: dict) -> None:
    log.info("kafka.nhan", nhom=nhom, ma_don=don.ma_don, offset=meta["offset"])
    DA_NHAN.append(
        {
            "nhom": nhom,
            "ma_don": don.ma_don,
            "kieu": don.kieu,
            "partition": meta["partition"],
            "offset": meta["offset"],
            "key": meta["key"],
            "lan_thu": meta["attempt"],
        }
    )
    del DA_NHAN[:-60]
