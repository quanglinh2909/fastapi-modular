"""Endpoint thử lớp Redis: cache, đếm nguyên tử, pub/sub."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Query

from fastapi_modular.core.controller import controller, delete, get, post
from fastapi_modular.infrastructure.redis import RedisClient
from src.api.redis_test.redis_listener import DA_NHAN, KENH, BaoCaoService, GiaMoi


@controller(prefix="/redis-test", tags=["redis-test"])
class RedisTestController:
    def __init__(self, redis: RedisClient, bao_cao: BaoCaoService) -> None:
        self._redis = redis
        self._bao_cao = bao_cao

    @get("/bao-cao/{ma}", summary="Việc chậm 0.4s, có cache 30 giây")
    async def bao_cao(self, ma: str) -> dict[str, object]:
        bat_dau = time.perf_counter()
        du_lieu = await self._bao_cao.bao_cao(ma)
        mili = round((time.perf_counter() - bat_dau) * 1000)
        return {
            "du_lieu": du_lieu,
            "mili_giay": mili,
            # Trượt cache thì tốn ~400ms, trúng thì ~1ms. `lan_tinh_that` không
            # tăng nữa nghĩa là lần này lấy từ Redis.
            "tu_cache": mili < 100,
            "con_song": await self._redis.ttl(f"bao-cao:{ma}"),
        }

    @delete("/bao-cao", summary="Xoá mọi khoá bao-cao:* (quét bằng SCAN)")
    async def xoa_cache(self) -> dict[str, int]:
        return {"da_xoa": await self._redis.delete_prefix("bao-cao:")}

    @post("/dem/{ten}", summary="Đếm nguyên tử, cửa sổ 60 giây")
    async def dem(self, ten: str) -> dict[str, object]:
        # incr là nguyên tử: mười worker cùng gọi vẫn ra đúng số. ttl chỉ được
        # đặt ở lần cộng đầu tiên nên cửa sổ không bị gia hạn vô hạn.
        so = await self._redis.incr(f"dem:{ten}", ttl=60)
        return {"ten": ten, "so": so, "con_song": await self._redis.ttl(f"dem:{ten}")}

    @post("/phat", summary="Phát tin lên kênh — mọi worker đang nghe đều nhận")
    async def phat(self, payload: GiaMoi) -> dict[str, object]:
        nguoi_nghe = await self._redis.publish(KENH, payload.model_dump())
        return {
            "kenh": KENH,
            "nguoi_nghe": nguoi_nghe,
            # 0 nghĩa là lúc này KHÔNG AI nghe và tin vừa rồi mất luôn —
            # pub/sub của Redis không lưu lại gì.
            "canh_bao": None if nguoi_nghe else "không ai đang nghe, tin đã mất",
        }

    @get("/da-nhan", summary="Tin worker này nhận được từ kênh")
    async def da_nhan(self, limit: Annotated[int, Query(ge=1, le=20)] = 20) -> dict[str, object]:
        return {"so_tin": len(DA_NHAN), "tin": DA_NHAN[-limit:]}
