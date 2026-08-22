"""Số đo của lớp Kafka."""

from __future__ import annotations

from pymodular.core.metrics import Counter, registry

kafka_published = registry.register(Counter("kafka_published_total", "Số tin đã gửi lên Kafka"))
kafka_publish_failed = registry.register(
    Counter("kafka_publish_failed_total", "Số lần gửi tin thất bại")
)
kafka_consumed = registry.register(Counter("kafka_consumed_total", "Số tin đã xử lý xong"))
kafka_consume_failed = registry.register(
    Counter("kafka_consume_failed_total", "Số lần handler xử lý lỗi (tính cả lần thử lại)")
)
kafka_dead_lettered = registry.register(
    Counter("kafka_dead_lettered_total", "Số tin bị đẩy sang topic chết")
)
