"""Chốt chặn: test không bao giờ được nối vào hạ tầng thật ghi trong .env."""

from __future__ import annotations

from fastapi_modular.core.config import Settings, get_settings


def test_khong_bao_gio_cham_ha_tang_that():
    for settings in (Settings(), get_settings()):
        assert settings.db.driver == "memory"
        assert settings.rabbitmq.enabled is False
        assert settings.redis.enabled is False
        assert settings.mqtt.enabled is False
        assert settings.kafka.enabled is False
        assert settings.ws.adapter == "local"
