"""Lớp MQTT — TUỲ CHỌN, và độc lập với mọi thứ khác.

    pip install 'fastapi-modular[mqtt]'   # cài aiomqtt + ghi sẵn APP_MQTT__* vào .env

Dùng cho thiết bị IoT: giao thức nhẹ, giữ kết nối lâu, chịu được mạng chập
chờn. Không dùng thì để `APP_MQTT__ENABLED=false` (mặc định).
"""

from __future__ import annotations

from fastapi_modular.infrastructure.mqtt.client import MqttClient
from fastapi_modular.infrastructure.mqtt.consumers import (
    MqttRunner,
    discover_mqtt_subscribers,
    mqtt_subscriber,
)
from fastapi_modular.infrastructure.mqtt.patterns import (
    covers,
    matches,
    narrow_filters,
    validate_topic,
    validate_topic_filter,
)
from fastapi_modular.infrastructure.mqtt.responders import (
    MqttResponderRunner,
    discover_mqtt_responders,
    mqtt_responder,
)

__all__ = [
    "MqttClient",
    "MqttResponderRunner",
    "MqttRunner",
    "covers",
    "discover_mqtt_responders",
    "discover_mqtt_subscribers",
    "matches",
    "mqtt_responder",
    "mqtt_subscriber",
    "narrow_filters",
    "validate_topic",
    "validate_topic_filter",
]
