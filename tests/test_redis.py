"""Test cần Redis THẬT.

Mặc định bỏ qua để `make test` chạy được trên máy trắng. Chạy đầy đủ:

    docker run -d --name redis-test -p 6389:6379 redis:7-alpine
    make install-redis
    TEST_REDIS_URL=redis://localhost:6389/0 make test
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time

import anyio
import pytest

from fastapi_modular.core.config import RedisSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.infrastructure.redis import RedisClient, RedisRunner, redis_subscriber

CO_REDIS = importlib.util.find_spec("redis") is not None
REDIS_URL = os.getenv("TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not (CO_REDIS and REDIS_URL), reason="cần thư viện redis + TEST_REDIS_URL"
)

DA_NHAN: list[dict] = []


@injectable
class KenhTest:
    @redis_subscriber("test-gia:*")
    async def nhan(self, payload: dict, meta: dict) -> None:
        DA_NHAN.append({"payload": payload, "channel": meta["channel"]})


@pytest.fixture
def redis_settings() -> Settings:
    return Settings(
        APP_REDIS=RedisSettings(enabled=True, url=REDIS_URL or "", key_prefix="test:")
    )


@pytest.fixture
async def redis(redis_settings: Settings):
    client = RedisClient(redis_settings)
    await client.startup()
    try:
        yield client
    finally:
        await client.delete_prefix("")       # dọn mọi khoá test:*
        await client.shutdown()


async def test_ghi_doc_va_han_su_dung(redis: RedisClient):
    assert await redis.set("a", {"x": 1}, ttl=30) is True
    assert await redis.get("a") == {"x": 1}
    assert 0 < (await redis.ttl("a") or 0) <= 30
    assert await redis.get("khong-co", default="mac-dinh") == "mac-dinh"

    # if_not_exists: lần thứ hai phải trả False và KHÔNG ghi đè.
    assert await redis.set("a", {"x": 2}, if_not_exists=True) is False
    assert await redis.get("a") == {"x": 1}


async def test_key_prefix_that_su_di_xuong_server(redis: RedisClient):
    await redis.set("co-tien-to", 1)
    tho = redis.raw()
    assert await tho.get("test:co-tien-to") == "1"
    assert await tho.get("co-tien-to") is None


async def test_incr_nguyen_tu_khi_goi_song_song(redis: RedisClient):
    """Đọc-rồi-ghi sẽ đếm thiếu; INCR thì không bao giờ."""
    await asyncio.gather(*(redis.incr("dem", ttl=60) for _ in range(50)))
    assert await redis.get("dem") == 50
    assert 0 < (await redis.ttl("dem") or 0) <= 60


async def test_cache_tinh_mot_lan_roi_thoi(redis: RedisClient):
    lan_goi = 0

    async def tinh() -> dict:
        nonlocal lan_goi
        lan_goi += 1
        return {"ket_qua": 42}

    assert await redis.cached("bao-cao", tinh, ttl=30) == {"ket_qua": 42}
    assert await redis.cached("bao-cao", tinh, ttl=30) == {"ket_qua": 42}
    assert lan_goi == 1, "lần thứ hai phải lấy từ cache"

    assert await redis.delete_prefix("bao-cao") == 1
    await redis.cached("bao-cao", tinh, ttl=30)
    assert lan_goi == 2, "xoá cache rồi thì phải tính lại"


async def test_cache_chiu_hong_khi_redis_chet(redis_settings: Settings):
    """Redis chết thì cache đi đường vòng, KHÔNG làm hỏng request."""
    hong = RedisClient(
        Settings(APP_REDIS=RedisSettings(enabled=True, url="redis://localhost:1/0"))
    )
    await hong.startup()                     # nối không được, app vẫn chạy
    assert hong.connected is False
    assert await hong.cached("x", lambda: _tra("van-chay"), ttl=10) == "van-chay"
    await hong.shutdown()


async def _tra(gia_tri: str) -> str:
    return gia_tri


async def test_pubsub_toi_duoc_handler(redis_settings: Settings):
    """Kênh có `*` -> PSUBSCRIBE; mọi worker đang nghe đều nhận một bản sao."""
    DA_NHAN.clear()
    client = RedisClient(redis_settings)
    runner = RedisRunner(client, redis_settings)
    await client.startup()
    await runner.startup()
    try:
        await anyio.sleep(0.3)               # chờ đăng ký kênh xong
        so_nguoi_nghe = await client.publish("test-gia:vang", {"gia": 78.5})
        assert so_nguoi_nghe == 1

        han = time.monotonic() + 5
        while time.monotonic() < han and not DA_NHAN:
            await anyio.sleep(0.05)
        assert DA_NHAN == [{"payload": {"gia": 78.5}, "channel": "test:test-gia:vang"}]

        # Không ai nghe kênh này -> trả 0, và tin đó MẤT LUÔN.
        assert await client.publish("test-khong-ai-nghe", {"x": 1}) == 0
    finally:
        await runner.shutdown()
        await client.shutdown()
