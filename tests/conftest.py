"""Fixture dùng chung cho test.

Container là singleton toàn tiến trình nên PHẢI reset giữa các test, nếu không
dữ liệu của test trước rò sang test sau.

Quan trọng hơn: `make test` KHÔNG được chạm vào hạ tầng thật. Xem khối
`_NGAT_HA_TANG_THAT` ngay dưới đây.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Ngắt mọi đường ra hạ tầng thật, TRƯỚC khi import bất cứ thứ gì của dự án.
#
# `Settings` đọc .env, mà .env trên máy lập trình viên hay trỏ vào database,
# RabbitMQ hoặc Redis THẬT. Test nào quên ghim cấu hình sẽ lặng lẽ nối vào đó:
# tạo hàng đợi, ăn mất tin của người khác, xoá dữ liệu. Tôi đã làm đúng lỗi này
# một lần — test chạy vào broker production ghi trong .env.
#
# Biến môi trường thắng file .env trong pydantic-settings, nên đặt ở đây là
# chặn được cả những test tự dựng Settings() riêng.
_NGAT_HA_TANG_THAT = {
    "APP_DB__DRIVER": "memory",
    "APP_DB__DSN": "",
    "APP_RABBITMQ__ENABLED": "false",
    "APP_REDIS__ENABLED": "false",
    "APP_MQTT__ENABLED": "false",
    "APP_KAFKA__ENABLED": "false",
    "APP_WS__ADAPTER": "local",
}
os.environ.update(_NGAT_HA_TANG_THAT)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from pymodular.core.config import (  # noqa: E402
    DatabaseSettings,
    KafkaSettings,
    MqttSettings,
    RabbitSettings,
    RedisSettings,
    Settings,
    WebSocketSettings,
)
from pymodular.core.container import container  # noqa: E402
from pymodular.factory import create_app  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    """Cấu hình cố định cho test.

    Ghi lại tường minh cho dễ đọc; lớp chặn thật sự là `_NGAT_HA_TANG_THAT` ở
    đầu file, nó chặn được cả những test tự dựng Settings() riêng.

    Muốn chạy trên hạ tầng thật thì dùng các file test riêng, bật bằng biến môi
    trường: TEST_SQLITE / TEST_POSTGRES_DSN / TEST_MONGO_DSN / TEST_REDIS_URL /
    TEST_RABBITMQ_URL / TEST_MQTT_URL / TEST_KAFKA_SERVERS.
    """
    return Settings(
        APP_ENV="local",
        APP_DEBUG=True,
        APP_DB=DatabaseSettings(driver="memory"),
        APP_RABBITMQ=RabbitSettings(enabled=False),
        APP_REDIS=RedisSettings(enabled=False),
        APP_MQTT=MqttSettings(enabled=False),
        APP_KAFKA=KafkaSettings(enabled=False),
        APP_WS=WebSocketSettings(adapter="local"),
    )


@pytest.fixture(autouse=True)
def clean_container():
    container.reset()
    yield
    container.reset()


@pytest.fixture
def client(settings: Settings):
    """TestClient đã chạy lifespan (mở/đóng database)."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def user(client) -> dict:
    response = client.post(
        "/api/users", json={"email": "an@example.com", "full_name": "Nguyễn Văn An"}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="session", autouse=True)
def don_file_db_tam():
    """Xoá file SQLite do test sinh ra, để thư mục data/ không phình dần."""
    yield
    data_dir = Path("data")
    if not data_dir.exists():
        return
    for leftover in (*data_dir.glob("test_*.db"), *data_dir.glob("evo_*.db")):
        leftover.unlink(missing_ok=True)
