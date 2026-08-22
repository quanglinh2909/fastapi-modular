"""Kết nối MQTT: giữ một phiên lâu dài, gửi tin, và nuôi vòng đọc tin về.

MQTT là TUỲ CHỌN. `APP_MQTT__ENABLED` mặc định false thì lớp này nằm im.

    pip install 'fastapi-modular[mqtt]'   # cài aiomqtt + ghi sẵn APP_MQTT__* vào .env

Về tự nối lại: aiomqtt KHÔNG tự nối lại (khác aio-pika). Nó cố ý để việc đó cho
người dùng, nên lớp này chạy một vòng
`connect -> subscribe -> đọc -> đứt -> chờ -> lặp lại` với backoff. Mọi đăng ký
topic được khai lại sau mỗi lần nối, vì broker chỉ nhớ chúng khi phiên là
persistent (`clean_session=false` + `client_id` cố định).

Gửi tin trong lúc đang đứt thì ném `ServiceUnavailableError`, trừ khi gọi với
`fire_and_forget=True`. Không có hàng đợi chờ gửi ở phía client: giữ tin trong
RAM rồi hứa gửi sau là một lời hứa mà tiến trình chết là mất — muốn bảo đảm thì
đó là việc của QoS 1/2 với phiên persistent, hoặc của RabbitMQ/Kafka.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from fastapi_modular.core.compat import TimeoutErrors
from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import ComponentNotEnabledError, ServiceUnavailableError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.mqtt.metrics import mqtt_publish_failed, mqtt_published
from fastapi_modular.infrastructure.mqtt.patterns import narrow_filters, validate_topic

log = get_logger(__name__)

DEFAULT_URL = "mqtt://localhost:1883"


def _require_aiomqtt() -> Any:
    try:
        import aiomqtt
    except ModuleNotFoundError as exc:
        raise ComponentNotEnabledError(
            "APP_MQTT__ENABLED=true nhưng chưa cài thư viện aiomqtt. "
            "Chạy `pip install 'fastapi-modular[mqtt]'`, hoặc đặt APP_MQTT__ENABLED=false nếu "
            "dự án này không dùng MQTT."
        ) from exc
    return aiomqtt


def parse_url(url: str) -> dict[str, Any]:
    """Tách mqtt://user:pass@host:port thành tham số cho aiomqtt."""
    parsed = urlparse(url)
    if parsed.scheme not in ("mqtt", "mqtts"):
        raise ValueError(f"APP_MQTT__URL phải bắt đầu bằng mqtt:// hoặc mqtts://, nhận {url!r}")
    return {
        "hostname": parsed.hostname or "localhost",
        "port": parsed.port or (8883 if parsed.scheme == "mqtts" else 1883),
        "username": parsed.username or None,
        "password": parsed.password or None,
        "tls": parsed.scheme == "mqtts",
    }


def safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.password:
        return url
    cong = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.username or ''}:***@{parsed.hostname}{cong}"


@injectable
class MqttClient:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.mqtt
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._connected = asyncio.Event()
        self._dang_ky: dict[str, int] = {}          # topic -> qos
        self._router: Callable[[Any], Awaitable[None]] | None = None
        self._lan_dut = 0

    # ------------------------------------------------------------- vòng đời
    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def url(self) -> str:
        return safe_url(self._config.url)

    @property
    def client_id(self) -> str:
        return self._config.client_id or f"fastapi_modular-{uuid.uuid4().hex[:8]}"

    def subscribe_topic(self, topic: str, qos: int = 1) -> None:
        """Ghi nhận một topic cần đăng ký. Gọi TRƯỚC startup().

        Giữ mức QoS cao nhất khi hai handler cùng nghe một topic: đăng ký ở
        QoS 0 rồi lại đòi QoS 1 thì broker giao theo mức đã đăng ký, tin sẽ
        lặng lẽ mất khi mạng chớp.
        """
        self._dang_ky[topic] = max(self._dang_ky.get(topic, 0), qos)

    def _de_dang_ky(self) -> dict[str, int]:
        """Danh sách thật sự gửi lên broker: đã bỏ bộ lọc bị bộ lọc khác bao trọn.

        Đăng ký chồng nhau ("thiet-bi/#" và "thiet-bi/+/nhiet-do") thì broker
        giao MỘT tin thành HAI lần — mỗi đăng ký một bản — và mọi handler khớp
        sẽ chạy hai lượt. Đo trên mosquitto: gửi 1 tin, handler chạy 4 lượt.
        Đăng ký cái rộng nhất rồi tự chia tin trong tiến trình thì đúng một lượt.
        """
        gon = narrow_filters(self._dang_ky)
        if bo := sorted(set(self._dang_ky) - set(gon)):
            log.debug("mqtt.subscription_gop", bo_qua=bo, giu=sorted(gon))
        return gon

    def set_router(self, router: Callable[[Any], Awaitable[None]]) -> None:
        """Ai nhận tin về: `MqttRunner` cắm vào đây."""
        self._router = router

    async def startup(self) -> None:
        if not self._config.enabled:
            log.debug("mqtt.disabled")
            return

        _require_aiomqtt()
        parse_url(self._config.url)           # sai dạng thì báo ngay, đừng đợi vòng nối lại
        if self._config.url == DEFAULT_URL:
            log.info("mqtt.default_url", url=DEFAULT_URL, hint="chưa đặt APP_MQTT__URL?")
        if not self._config.clean_session and not self._config.client_id:
            log.warning(
                "mqtt.session_khong_ben",
                hint="clean_session=false cần APP_MQTT__CLIENT_ID cố định, "
                "không thì mỗi lần khởi động là một phiên mới và tin giữ lại bị bỏ",
            )

        self._closing = False
        self._task = asyncio.create_task(self._vong_ket_noi(), name="mqtt-connection")

        # Chờ một nhịp cho lần nối đầu, để log khởi động nói đúng trạng thái.
        with contextlib.suppress(*TimeoutErrors):
            await asyncio.wait_for(
                self._connected.wait(), self._config.connect_timeout_seconds
            )
        if not self.connected:
            log.warning(
                "mqtt.starting_degraded",
                url=self.url,
                hint="app vẫn chạy; sẽ nối lại ngầm cho tới khi được",
            )

    async def _vong_ket_noi(self) -> None:
        """Nối, đăng ký, đọc tới khi đứt, chờ rồi làm lại. Không bao giờ bỏ cuộc."""
        aiomqtt = _require_aiomqtt()
        tham_so = parse_url(self._config.url)
        tls = tham_so.pop("tls")
        delay = self._config.reconnect_delay_seconds
        ma_so = self.client_id

        while not self._closing:
            try:
                async with aiomqtt.Client(
                    **tham_so,
                    identifier=ma_so,
                    keepalive=self._config.keepalive_seconds,
                    clean_session=self._config.clean_session,
                    tls_context=_tls_context() if tls else None,
                ) as client:
                    self._client = client
                    dang_ky = self._de_dang_ky()
                    for topic, qos in sorted(dang_ky.items()):
                        await client.subscribe(topic, qos=qos)
                    self._connected.set()
                    delay = self._config.reconnect_delay_seconds
                    log.info(
                        "mqtt.connected",
                        url=self.url,
                        client_id=ma_so,
                        topics=sorted(dang_ky),
                    )
                    async for message in client.messages:
                        if self._router is not None:
                            await self._router(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - đứt kiểu gì cũng nối lại
                if self._closing:
                    return
                self._lan_dut += 1
                log.warning(
                    "mqtt.connection_lost",
                    url=self.url,
                    error=f"{type(exc).__name__}: {exc}",
                    retry=delay,
                )
            finally:
                self._connected.clear()
                self._client = None

            if self._closing:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    async def shutdown(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected.clear()
        log.info("mqtt.disconnected")

    # ---------------------------------------------------------------- gửi
    async def publish(
        self,
        topic: str,
        payload: Any = None,
        *,
        qos: int = 1,
        retain: bool = False,
        fire_and_forget: bool = False,
    ) -> bool:
        """Gửi một tin. Trả về True nếu broker đã nhận.

        qos=0  gửi rồi thôi, không xác nhận — nhanh nhất, mất cũng không biết
        qos=1  ít nhất một lần: có xác nhận, nhưng CÓ THỂ TRÙNG khi mạng chớp
        qos=2  đúng một lần: chắc nhất, tốn hai vòng bắt tay, chậm nhất

        `retain=True` bảo broker GIỮ tin này làm giá trị hiện tại của topic:
        client nào đăng ký sau cũng nhận được ngay bản mới nhất mà không phải
        chờ lần cập nhật kế tiếp. Đúng cho trạng thái (nhiệt độ, bật/tắt), sai
        cho sự kiện (nút vừa được bấm) — người nối vào sau sẽ tưởng nút vừa mới
        bấm lần nữa.
        """
        validate_topic(topic)
        if not self._config.enabled:
            raise ComponentNotEnabledError(
                "MQTT đang tắt (APP_MQTT__ENABLED=false) nên không gửi được."
            )

        client = self._client
        if client is None or not self.connected:
            mqtt_publish_failed.inc(topic=topic)
            if fire_and_forget:
                log.warning("mqtt.publish_skipped", topic=topic, reason="chưa kết nối")
                return False
            raise ServiceUnavailableError("Chưa kết nối được MQTT broker")

        body = payload if isinstance(payload, (bytes, str)) else json.dumps(
            payload, ensure_ascii=False, default=str
        )
        try:
            await client.publish(topic, payload=body, qos=qos, retain=retain)
        except Exception as exc:
            mqtt_publish_failed.inc(topic=topic)
            if fire_and_forget:
                log.warning("mqtt.publish_failed", topic=topic, error=str(exc))
                return False
            raise ServiceUnavailableError(f"Gửi MQTT thất bại: {exc}") from exc

        mqtt_published.inc(topic=topic)
        log.debug("mqtt.published", topic=topic, qos=qos, retain=retain)
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "url": self.url if self._config.enabled else None,
            "client_id": self._config.client_id or "(tự sinh)",
            "topics": sorted(self._de_dang_ky()),
            "listeners": sorted(self._dang_ky),
            "disconnects": self._lan_dut,
        }


def _tls_context() -> Any:
    import ssl

    return ssl.create_default_context()
