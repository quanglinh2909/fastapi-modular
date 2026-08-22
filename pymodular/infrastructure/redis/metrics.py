"""Số đo của lớp Redis. Đăng ký vào registry chung, lõi không cần biết gì."""

from __future__ import annotations

from pymodular.core.metrics import Counter, registry

redis_hit = registry.register(Counter("redis_cache_hit_total", "Số lần đọc trúng cache"))
redis_miss = registry.register(Counter("redis_cache_miss_total", "Số lần đọc trượt cache"))
redis_error = registry.register(
    Counter("redis_error_total", "Số lệnh Redis lỗi (kể cả lúc cache tự bỏ qua)")
)
redis_published = registry.register(
    Counter("redis_published_total", "Số tin đã đăng lên kênh pub/sub")
)
redis_received = registry.register(
    Counter("redis_received_total", "Số tin nhận được từ kênh pub/sub")
)
redis_handler_failed = registry.register(
    Counter("redis_handler_failed_total", "Số tin handler xử lý lỗi")
)
