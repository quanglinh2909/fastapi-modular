"""Test lớp Redis KHÔNG cần server thật.

Kiểm hai điều: tắt thì thật sự nằm im, và những gì khai bằng decorator được
quét ra đúng. Phần cần server nằm ở tests/test_redis.py.
"""

from __future__ import annotations

import pytest

from fastapi_modular.core.config import RedisSettings, Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import BadRequestError, ComponentNotEnabledError
from fastapi_modular.infrastructure.redis import (
    RedisClient,
    discover_redis_responders,
    redis_responder,
    redis_subscriber,
)
from fastapi_modular.infrastructure.redis import responders as responders_module
from fastapi_modular.infrastructure.redis.client import safe_url
from fastapi_modular.infrastructure.redis.pubsub import discover_redis_subscribers


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


# ------------------------------------------------- @redis_responder (send)
@injectable
class RedisResponderMau:
    @redis_responder("sum")
    async def cong(self, data: list[int]) -> int:
        return sum(data)

    @redis_responder({"cmd": "info"})
    async def thong_tin(self, data: dict, meta: dict) -> dict:
        return {"pattern": meta["pattern"]}


def test_responder_quet_duoc_va_chuan_hoa_pattern():
    patterns = {s.pattern for s in discover_redis_responders()}
    assert "sum" in patterns
    assert '{"cmd":"info"}' in patterns, "pattern dạng dict chuỗi hoá theo luật NestJS"


def test_ky_tu_dai_dien_bi_tu_choi():
    """Kênh trả lời là `<pattern>.reply` — một mẫu thì không nói được trả về đâu."""
    with pytest.raises(BadRequestError, match="ký tự đại diện"):

        @redis_responder("gia.*")
        async def sai(self, data) -> None: ...


def test_trung_pattern_bi_chan(monkeypatch):
    """Một trong hai sẽ không bao giờ được gọi, mà không có gì báo là cái nào."""

    @injectable
    class A:
        @redis_responder("trung-nhau")
        async def mot(self, data) -> int: ...

    @injectable
    class B:
        @redis_responder("trung-nhau")
        async def hai(self, data) -> int: ...

    monkeypatch.setattr(responders_module, "_REGISTRY", {"A": A, "B": B})
    with pytest.raises(RuntimeError, match="không bao giờ được gọi"):
        discover_redis_responders()


def test_responder_phai_la_async():
    with pytest.raises(RuntimeError, match="async def"):

        @redis_responder("dong-bo")
        def sai(self, data) -> int: ...
