"""Test lớp Redis KHÔNG cần server thật.

Kiểm hai điều: tắt thì thật sự nằm im, và những gì khai bằng decorator được
quét ra đúng. Phần cần server nằm ở tests/test_redis.py.
"""

from __future__ import annotations

import pytest

from pymodular.core.config import RedisSettings, Settings
from pymodular.core.container import injectable
from pymodular.core.exceptions import ComponentNotEnabledError
from pymodular.infrastructure.redis import RedisClient, redis_subscriber
from pymodular.infrastructure.redis.client import safe_url
from pymodular.infrastructure.redis.pubsub import discover_redis_subscribers


def test_mac_dinh_la_tat():
    assert RedisSettings().enabled is False


async def test_tat_thi_khong_lam_gi_ca():
    client = RedisClient(Settings(APP_REDIS=RedisSettings(enabled=False)))
    await client.startup()          # không import thư viện, không mở kết nối
    assert client.connected is False
    assert client.stats()["url"] is None
    with pytest.raises(ComponentNotEnabledError):
        await client.get("bat-ky")
    await client.shutdown()


def test_che_mat_khau_trong_log():
    assert safe_url("redis://:sieubimat@cache:6379/0") == "redis://:***@cache:6379/0"
    assert safe_url("redis://localhost:6379/0") == "redis://localhost:6379/0"


def test_key_prefix_ghep_vao_moi_khoa():
    client = RedisClient(Settings(APP_REDIS=RedisSettings(key_prefix="don-hang:")))
    assert client.key("abc") == "don-hang:abc"


def test_app_van_chay_binh_thuong_khi_khong_co_redis(client):
    assert client.get("/api/users").status_code == 200
    assert client.get("/api/health/ready").json().get("redis") is None


@injectable
class KenhMau:
    @redis_subscriber("gia:vang")
    async def dung_ten(self, payload: dict) -> None: ...

    @redis_subscriber("gia:*")
    async def theo_mau(self, payload: dict, meta: dict) -> None: ...


def test_tim_duoc_kenh_da_khai():
    specs = {spec.channel: spec for spec in discover_redis_subscribers()}
    assert specs["gia:vang"].is_pattern is False
    assert specs["gia:vang"].wants_meta is False
    assert specs["gia:*"].is_pattern is True, "có * thì phải dùng PSUBSCRIBE"
    assert specs["gia:*"].wants_meta is True


def test_redis_subscriber_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @redis_subscriber("gia:vang")
        def dong_bo(self, payload: dict) -> None: ...
