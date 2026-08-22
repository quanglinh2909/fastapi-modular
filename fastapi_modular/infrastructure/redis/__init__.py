"""Lớp Redis — TUỲ CHỌN, và độc lập với mọi thứ khác.

    pip install 'fastapi-modular[redis]'   # cài thư viện + ghi sẵn APP_REDIS__* vào .env

Hai việc lớp này làm:

    cache / khoá-giá trị / đếm    RedisClient.get, set, incr, cached, delete_prefix
    phát tin tới mọi worker       RedisClient.publish + @redis_subscriber

Không dùng thì để `APP_REDIS__ENABLED=false` (mặc định) — không phải cài thư
viện, không phải sửa dòng code nào.

Lưu ý: adapter Redis của WebSocket (`APP_WS__ADAPTER=redis`) là một thứ KHÁC,
có cấu hình riêng, và không cần lớp này bật lên mới chạy được.
"""

from __future__ import annotations

from fastapi_modular.infrastructure.redis.client import RedisClient
from fastapi_modular.infrastructure.redis.pubsub import (
    RedisRunner,
    discover_redis_subscribers,
    redis_subscriber,
)

__all__ = [
    "RedisClient",
    "RedisRunner",
    "discover_redis_subscribers",
    "redis_subscriber",
]
