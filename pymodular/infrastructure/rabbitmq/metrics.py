"""Số đo của lớp nhắn tin.

Khai ở đây chứ không phải trong `core/metrics.py`: lõi không cần biết dự án có
dùng hàng đợi hay không. Registry là thứ dùng chung, ai có số đo thì tự đăng ký
vào — thêm một transport mới (Kafka, MQTT...) cũng chỉ việc thêm file, không
phải sửa lõi.

Nhãn `routing_key` chỉ dùng cho tin ĐĂNG ĐI, nơi tập giá trị do code quyết
định. Phía nhận dùng nhãn `queue`: mẫu "#" có thể nhận về vô số key khác nhau
và sẽ làm nổ số chuỗi số đo của Prometheus.
"""

from __future__ import annotations

from pymodular.core.metrics import Counter, registry

rabbitmq_published = registry.register(
    Counter("rabbitmq_published_total", "Số tin đã đăng lên RabbitMQ")
)
rabbitmq_publish_failed = registry.register(
    Counter("rabbitmq_publish_failed_total", "Số lần đăng tin thất bại")
)
rabbitmq_consumed = registry.register(
    Counter("rabbitmq_consumed_total", "Số tin consumer đã xử lý xong")
)
rabbitmq_consume_failed = registry.register(
    Counter("rabbitmq_consume_failed_total", "Số tin consumer xử lý lỗi")
)
rabbitmq_retried = registry.register(
    Counter("rabbitmq_retried_total", "Số tin được hẹn xử lý lại")
)
rabbitmq_dead_lettered = registry.register(
    Counter("rabbitmq_dead_lettered_total", "Số tin bị đẩy sang hàng đợi chết")
)
