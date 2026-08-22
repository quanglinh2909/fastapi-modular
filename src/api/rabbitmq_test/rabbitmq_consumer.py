"""Ví dụ chạy được: một tin đi qua `<queue>` -> `<queue>.retry` -> `<queue>.dlq`.

Consumer dưới đây TỰ BẬT thử lại và hàng đợi chết (mặc định không có), rồi cố ý
có ba lối ra khác nhau, chọn bằng trường `kieu` trong payload, để nhìn tận mắt
lúc nào tin rơi vào hàng đợi nào:

    kieu="ok"              -> handler chạy xong    -> ack, hết
    kieu="hong-tam-thoi"   -> ném RuntimeError     -> alert-mailer.retry, quay
                                                      lại sau 5 giây, thử 2 lần
                                                      rồi mới sang .dlq
    kieu="hong-vinh-vien"  -> PermanentMessageError -> alert-mailer.dlq NGAY,
                                                      không thử lại lần nào

Gọi thử bằng `POST /api/rabbitmq-test/gui`, xem kết quả bằng
`GET /api/rabbitmq-test/hang-doi`. Chi tiết ở docs/rabbitmq.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.rabbitmq import (
    PermanentMessageError,
    RabbitBroker,
    rabbitmq_subscriber,
)

log = get_logger(__name__)

EXCHANGE = "event.exchange_edge"
ROUTING_KEY = "alert.created"
QUEUE = "alert-mailer"


class AlertCreated(BaseModel):
    """Khuôn của tin. Sai khuôn thì đi thẳng DLQ, không thử lại (thử cũng vẫn sai)."""

    message: str
    kieu: str = Field(default="ok", description="ok | hong-tam-thoi | hong-vinh-vien")


@injectable
class AlertConsumer:
    def __init__(self, mq: RabbitBroker) -> None:
        self._mq = mq

    # Mặc định `@rabbitmq_subscriber` chỉ mọc ra ĐÚNG MỘT hàng đợi. Ba tham số dưới đây
    # là TỰ BẬT thêm, vì mail hỏng thì đáng thử lại và đáng giữ lại để xem:
    #   max_retries=2  -> thêm alert-mailer.retry
    #   dead_letter    -> thêm alert-mailer.dlq
    # `auto_delete` để mặc định (False): hàng đợi mail phải GIỮ tin lại lúc app
    # đang deploy, không thì mọi cảnh báo phát sinh trong 30 giây đó mất sạch.
    @rabbitmq_subscriber(EXCHANGE, ROUTING_KEY, queue=QUEUE, max_retries=2, retry_delay=5,
                dead_letter=True)
    async def event(self, payload: AlertCreated, meta: dict) -> None:
        log.info("alert.nhan_duoc", kieu=payload.kieu, lan_thu=meta["attempt"])

        if payload.kieu == "hong-vinh-vien":
            # Biết chắc thử lại vô ích: dữ liệu sai, bản ghi đã xoá, phiên bản
            # sự kiện không hỗ trợ. Ném cái này để bỏ qua mọi lượt thử còn lại.
            raise PermanentMessageError(f"Không gửi được mail cho {payload.message!r}")

        if payload.kieu == "hong-tam-thoi":
            # Giả lập lỗi mạng/SMTP chập chờn — loại đáng để thử lại.
            raise RuntimeError(f"SMTP không phản hồi (lần thử {meta['attempt']})")

        log.info("alert.da_gui_mail", noi_dung=payload.message)
