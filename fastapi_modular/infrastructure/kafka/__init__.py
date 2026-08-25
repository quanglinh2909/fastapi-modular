"""Lớp Kafka — TUỲ CHỌN, và độc lập với mọi thứ khác.

    pip install 'fastapi-modular[kafka]'   # cài aiokafka + ghi sẵn APP_KAFKA__* vào .env

Dùng khi cần nhật ký sự kiện ĐỌC LẠI ĐƯỢC: tin nằm lại theo thời gian giữ của
topic, nhiều nhóm consumer đọc cùng một dòng tin ở các vị trí khác nhau. Không
dùng thì để `APP_KAFKA__ENABLED=false` (mặc định).
"""

from __future__ import annotations

from fastapi_modular.infrastructure.kafka.broker import KafkaBroker
from fastapi_modular.infrastructure.kafka.consumers import (
    KafkaRunner,
    PermanentMessageError,
    discover_kafka_subscribers,
    kafka_subscriber,
)
from fastapi_modular.infrastructure.kafka.responders import (
    KafkaResponderRunner,
    discover_kafka_responders,
    kafka_responder,
)

__all__ = [
    "KafkaBroker",
    "KafkaResponderRunner",
    "KafkaRunner",
    "PermanentMessageError",
    "discover_kafka_responders",
    "discover_kafka_subscribers",
    "kafka_responder",
    "kafka_subscriber",
]
