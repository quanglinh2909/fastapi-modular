"""Lớp Kafka — TUỲ CHỌN, và độc lập với mọi thứ khác.

    pip install 'pymodular[kafka]'   # cài aiokafka + ghi sẵn APP_KAFKA__* vào .env

Dùng khi cần nhật ký sự kiện ĐỌC LẠI ĐƯỢC: tin nằm lại theo thời gian giữ của
topic, nhiều nhóm consumer đọc cùng một dòng tin ở các vị trí khác nhau. Không
dùng thì để `APP_KAFKA__ENABLED=false` (mặc định).
"""

from __future__ import annotations

from pymodular.infrastructure.kafka.broker import KafkaBroker
from pymodular.infrastructure.kafka.consumers import (
    KafkaRunner,
    PermanentMessageError,
    discover_kafka_subscribers,
    kafka_subscriber,
)

__all__ = [
    "KafkaBroker",
    "KafkaRunner",
    "PermanentMessageError",
    "discover_kafka_subscribers",
    "kafka_subscriber",
]
