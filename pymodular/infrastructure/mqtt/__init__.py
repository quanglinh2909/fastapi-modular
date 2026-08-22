"""Lớp MQTT — TUỲ CHỌN, và độc lập với mọi thứ khác.

    pip install 'pymodular[mqtt]'   # cài aiomqtt + ghi sẵn APP_MQTT__* vào .env

Dùng cho thiết bị IoT: giao thức nhẹ, giữ kết nối lâu, chịu được mạng chập
chờn. Không dùng thì để `APP_MQTT__ENABLED=false` (mặc định).
"""

from __future__ import annotations

from pymodular.infrastructure.mqtt.client import MqttClient
from pymodular.infrastructure.mqtt.consumers import (
    MqttRunner,
    discover_mqtt_subscribers,
    mqtt_subscriber,
)
from pymodular.infrastructure.mqtt.patterns import (
    covers,
    matches,
    narrow_filters,
    validate_topic,
    validate_topic_filter,
)

__all__ = [
    "MqttClient",
    "MqttRunner",
    "covers",
    "discover_mqtt_subscribers",
    "matches",
    "mqtt_subscriber",
    "narrow_filters",
    "validate_topic",
    "validate_topic_filter",
]
