"""Ví dụ Redis: cache một việc chậm, và nghe kênh pub/sub.

Bật bằng `make install-redis` rồi đặt APP_REDIS__ENABLED=true. Tắt thì hai class
dưới đây vẫn nạp bình thường, chỉ là không có gì chạy.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.redis import RedisClient, redis_subscriber

log = get_logger(__name__)

KENH = "gia:vang"

# Nơi cất tin đã nhận để endpoint /da-nhan xem lại. Đây là bộ nhớ CỦA MỘT
# WORKER — chạy nhiều worker thì mỗi worker thấy một danh sách khác nhau, và đó
# chính là điều pub/sub muốn cho thấy: mọi worker đều nhận một bản sao.
DA_NHAN: list[dict] = []


class GiaMoi(BaseModel):
    ma: str
    gia: float


@injectable
class GiaListener:
    # "gia:*" có ký tự đại diện -> khung tự dùng PSUBSCRIBE. "gia:vang" thì khớp
    # đúng một kênh.
    @redis_subscriber("gia:*")
    async def doi_gia(self, payload: GiaMoi, meta: dict) -> None:
        log.info("redis.gia_moi", kenh=meta["channel"], ma=payload.ma, gia=payload.gia)
        DA_NHAN.append({"channel": meta["channel"], "ma": payload.ma, "gia": payload.gia})
        del DA_NHAN[:-20]


@injectable
class BaoCaoService:
    """Việc chậm, đáng để cache."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis
        self.lan_tinh = 0

    async def tinh_that(self, ma: str) -> dict:
        self.lan_tinh += 1
        await asyncio.sleep(0.4)          # giả lập truy vấn nặng
        return {"ma": ma, "so_don": 1234, "lan_tinh_that": self.lan_tinh}

    async def bao_cao(self, ma: str) -> dict:
        # cached(): trúng thì trả ngay, trượt thì gọi factory rồi ghi lại 30s.
        # Redis chết thì hàm này KHÔNG ném lỗi — nó gọi thẳng tinh_that().
        return await self._redis.cached(
            f"bao-cao:{ma}", lambda: self.tinh_that(ma), ttl=30
        )
