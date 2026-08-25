"""`fam info` — đang cấu hình những gì, và thư viện nào đã cài.

Gộp sáu lệnh `*-info` cũ của Makefile vào một chỗ, vì câu hỏi thật sự luôn là "hiện tại
app của tôi đang nối vào đâu" chứ không phải "riêng RabbitMQ thế nào".
"""

from __future__ import annotations

import importlib.util


def _has(name: str) -> str:
    return "có" if importlib.util.find_spec(name) else "chưa cài"


def _close(label_: str, value: object) -> None:
    print(f"  {label_:<16}{value}")


def info() -> int:
    from fastapi_modular import __version__
    from fastapi_modular.core.config import get_settings
    from fastapi_modular.infrastructure.mqtt.client import safe_url as mqtt_url
    from fastapi_modular.infrastructure.rabbitmq.broker import safe_url as amqp_url
    from fastapi_modular.infrastructure.redis.client import safe_url as redis_url

    s = get_settings()

    print(f"fastapi_modular {__version__}  ·  {type(s).__name__}")
    print("\nỨng dụng")
    _close("name", s.name)
    _close("env", s.env)
    _close("host:port", f"{s.host}:{s.port}")

    print("\nDatabase")
    _close("driver", s.db.driver)
    _close("dsn", s.db.resolved_dsn or "(bộ nhớ tạm — mất khi restart)")
    _close("schema_mode", s.db.schema_mode)
    _close("thư viện", f"sqlalchemy: {_has('sqlalchemy')} · asyncpg: {_has('asyncpg')} · "
                      f"aiosqlite: {_has('aiosqlite')} · motor: {_has('motor')}")

    print("\nWebSocket")
    _close("adapter", s.ws.adapter + ("" if s.ws.adapter == "redis" else "  (chỉ đúng với MỘT worker)"))
    if s.ws.adapter == "redis":
        _close("redis_url", s.ws.redis_url)
    _close("nhịp tim", f"{s.ws.heartbeat_seconds}s / im lặng tối đa {s.ws.idle_timeout_seconds}s")

    print("\nHạ tầng tuỳ chọn")
    for label_, on, address, library in (
        ("rabbitmq", s.rabbitmq.enabled, amqp_url(s.rabbitmq.url), "aio_pika"),
        ("redis", s.redis.enabled, redis_url(s.redis.url), "redis"),
        ("mqtt", s.mqtt.enabled, mqtt_url(s.mqtt.url), "aiomqtt"),
        ("kafka", s.kafka.enabled, s.kafka.bootstrap_servers, "aiokafka"),
    ):
        status = f"bật   {address}" if on else "tắt"
        _close(label_, f"{status:<45}thư viện: {_has(library)}")

    if warn := s.check_production_safety():
        print("\nCẢNH BÁO cho môi trường prod")
        for line in warn:
            print(f"  - {line}")
    return 0
