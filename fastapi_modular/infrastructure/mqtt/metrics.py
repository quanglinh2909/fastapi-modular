"""Số đo của lớp MQTT."""

from __future__ import annotations

from fastapi_modular.core.metrics import Counter, registry

mqtt_published = registry.register(Counter("mqtt_published_total", "Số tin đã gửi lên broker"))
mqtt_publish_failed = registry.register(
    Counter("mqtt_publish_failed_total", "Số lần gửi tin thất bại")
)
mqtt_received = registry.register(Counter("mqtt_received_total", "Số tin nhận được"))
mqtt_handler_failed = registry.register(
    Counter("mqtt_handler_failed_total", "Số tin handler xử lý lỗi")
)
mqtt_unrouted = registry.register(
    Counter("mqtt_unrouted_total", "Số tin nhận được mà không handler nào khớp")
)
