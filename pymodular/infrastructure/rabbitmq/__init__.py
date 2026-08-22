"""Lớp RabbitMQ — TUỲ CHỌN, và độc lập với mọi thứ khác.

Dự án không dùng RabbitMQ thì không cần cài gì và không phải đụng tới đây:
`APP_RABBITMQ__ENABLED` mặc định là false, thư viện `aio-pika` chỉ được import khi
thật sự bật lên.

    pip install 'pymodular[rabbitmq]'   # cài thư viện + ghi sẵn APP_RABBITMQ__* vào .env

Lớp này KHÔNG biết gì về HTTP hay WebSocket, và chúng cũng không biết nó. Muốn
một sự kiện từ hàng đợi hiện lên màn hình người dùng thì đó là việc của code
ứng dụng — ba dòng trong consumer của bạn, xem docs/rabbitmq.md.
"""

from __future__ import annotations

from pymodular.infrastructure.rabbitmq.broker import RabbitBroker
from pymodular.infrastructure.rabbitmq.consumers import (
    PermanentMessageError,
    RabbitmqRunner,
    discover_rabbitmq_subscribers,
    rabbitmq_subscriber,
)
from pymodular.infrastructure.rabbitmq.patterns import validate_pattern, validate_routing_key

__all__ = [
    "PermanentMessageError",
    "RabbitBroker",
    "RabbitmqRunner",
    "discover_rabbitmq_subscribers",
    "rabbitmq_subscriber",
    "validate_pattern",
    "validate_routing_key",
]
