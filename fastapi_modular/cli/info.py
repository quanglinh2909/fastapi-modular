"""`fam info` — đang cấu hình những gì, và thư viện nào đã cài.

Gộp sáu lệnh `*-info` cũ của Makefile vào một chỗ, vì câu hỏi thật sự luôn là "hiện tại
app của tôi đang nối vào đâu" chứ không phải "riêng RabbitMQ thế nào".
"""

from __future__ import annotations

import importlib.util


def _co(ten: str) -> str:
    return "có" if importlib.util.find_spec(ten) else "chưa cài"


def _dong(nhan: str, gia_tri: object) -> None:
    print(f"  {nhan:<16}{gia_tri}")


def info() -> int:
    from fastapi_modular import __version__
    from fastapi_modular.core.config import get_settings
    from fastapi_modular.infrastructure.mqtt.client import safe_url as mqtt_url
    from fastapi_modular.infrastructure.rabbitmq.broker import safe_url as amqp_url
    from fastapi_modular.infrastructure.redis.client import safe_url as redis_url

    s = get_settings()

    print(f"fastapi_modular {__version__}  ·  {type(s).__name__}")
    print("\nỨng dụng")
    _dong("name", s.name)
    _dong("env", s.env)
    _dong("host:port", f"{s.host}:{s.port}")

    print("\nDatabase")
    _dong("driver", s.db.driver)
    _dong("dsn", s.db.resolved_dsn or "(bộ nhớ tạm — mất khi restart)")
    _dong("schema_mode", s.db.schema_mode)
    _dong("thư viện", f"sqlalchemy: {_co('sqlalchemy')} · asyncpg: {_co('asyncpg')} · "
                      f"aiosqlite: {_co('aiosqlite')} · motor: {_co('motor')}")

    print("\nWebSocket")
    _dong("adapter", s.ws.adapter + ("" if s.ws.adapter == "redis" else "  (chỉ đúng với MỘT worker)"))
    if s.ws.adapter == "redis":
        _dong("redis_url", s.ws.redis_url)
    _dong("nhịp tim", f"{s.ws.heartbeat_seconds}s / im lặng tối đa {s.ws.idle_timeout_seconds}s")

    print("\nHạ tầng tuỳ chọn")
    for nhan, bat, dia_chi, thu_vien in (
        ("rabbitmq", s.rabbitmq.enabled, amqp_url(s.rabbitmq.url), "aio_pika"),
        ("redis", s.redis.enabled, redis_url(s.redis.url), "redis"),
        ("mqtt", s.mqtt.enabled, mqtt_url(s.mqtt.url), "aiomqtt"),
        ("kafka", s.kafka.enabled, s.kafka.bootstrap_servers, "aiokafka"),
    ):
        trang_thai = f"bật   {dia_chi}" if bat else "tắt"
        _dong(nhan, f"{trang_thai:<45}thư viện: {_co(thu_vien)}")

    if canh_bao := s.check_production_safety():
        print("\nCẢNH BÁO cho môi trường prod")
        for dong in canh_bao:
            print(f"  - {dong}")
    return 0
