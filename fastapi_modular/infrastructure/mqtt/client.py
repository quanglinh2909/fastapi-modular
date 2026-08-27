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
from fastapi_modular.core.rpc import (
    DEFAULT_RPC_TIMEOUT,
    PendingReplies,
    decode,
    event_packet,
    normalize_pattern,
    reply_topic_mqtt,
    request_packet,
)
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
    gateway = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.username or ''}:***@{parsed.hostname}{gateway}"


@injectable
class MqttClient:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.mqtt
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._connected = asyncio.Event()
        self._subscriptions: dict[str, int] = {}          # topic -> qos
        self._router: Callable[[Any], Awaitable[None]] | None = None
        self._routers: list[Callable[[Any], Awaitable[None]]] = []
        self._drop_count = 0
        self._pending = PendingReplies("MQTT")
        self._reply_topics: set[str] = set()

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
        self._subscriptions[topic] = max(self._subscriptions.get(topic, 0), qos)

    def _to_subscribe(self) -> dict[str, int]:
        """Danh sách thật sự gửi lên broker: đã bỏ bộ lọc bị bộ lọc khác bao trọn.

        Đăng ký chồng nhau ("thiet-bi/#" và "thiet-bi/+/nhiet-do") thì broker
        giao MỘT tin thành HAI lần — mỗi đăng ký một bản — và mọi handler khớp
        sẽ chạy hai lượt. Đo trên mosquitto: gửi 1 tin, handler chạy 4 lượt.
        Đăng ký cái rộng nhất rồi tự chia tin trong tiến trình thì đúng một lượt.
        """
        narrowed = narrow_filters(self._subscriptions)
        if dropped := sorted(set(self._subscriptions) - set(narrowed)):
            log.debug("mqtt.subscription_gop", skipped=dropped, kept=sorted(narrowed))
        return narrowed

    def set_router(self, router: Callable[[Any], Awaitable[None]]) -> None:
        """Ai nhận tin về: `MqttRunner` cắm vào đây."""
        self._router = router
        self.add_router(router)

    def add_router(self, router: Callable[[Any], Awaitable[None]]) -> None:
        """Thêm một bên nhận tin nữa.

        MQTT chỉ có MỘT luồng tin về cho cả tiến trình, mà nay có ba bên cần
        đọc nó: `@mqtt_subscriber`, `@mqtt_responder`, và chỗ nhận câu trả lời
        cho `send()`. Ai không quan tâm tin nào thì tự bỏ qua.
        """
        if router not in self._routers:
            self._routers.append(router)

    async def _dispatch_to_routers(self, message: Any) -> None:
        for router in list(self._routers):
            try:
                await router(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(
                    "mqtt.router_failed", error=f"{type(exc).__name__}: {exc}"
                )

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
                "mqtt.session_not_persistent",
                hint="clean_session=false cần APP_MQTT__CLIENT_ID cố định, "
                "không thì mỗi lần khởi động là một phiên mới và tin giữ lại bị bỏ",
            )

        self._closing = False
        self._task = asyncio.create_task(self._connection_loop(), name="mqtt-connection")

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

    async def _connection_loop(self) -> None:
        """Nối, đăng ký, đọc tới khi đứt, chờ rồi làm lại. Không bao giờ bỏ cuộc."""
        aiomqtt = _require_aiomqtt()
        connect_kwargs = parse_url(self._config.url)
        tls = connect_kwargs.pop("tls")
        delay = self._config.reconnect_delay_seconds
        identifier = self.client_id

        while not self._closing:
            try:
                async with aiomqtt.Client(
                    **connect_kwargs,
                    identifier=identifier,
                    keepalive=self._config.keepalive_seconds,
                    clean_session=self._config.clean_session,
                    tls_context=_tls_context() if tls else None,
                ) as client:
                    self._client = client
                    subscriptions = self._to_subscribe()
                    for topic, qos in sorted(subscriptions.items()):
                        await client.subscribe(topic, qos=qos)
                    self._connected.set()
                    delay = self._config.reconnect_delay_seconds
                    log.info(
                        "mqtt.connected",
                        url=self.url,
                        client_id=identifier,
                        topics=sorted(subscriptions),
                    )
                    async for message in client.messages:
                        await self._dispatch_to_routers(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - đứt kiểu gì cũng nối lại
                if self._closing:
                    return
                self._drop_count += 1
                log.warning(
                    "mqtt.connection_lost",
                    url=self.url,
                    error=f"{type(exc).__name__}: {exc}",
                    retry=delay,
                )
            finally:
                self._connected.clear()
                self._client = None
                # Câu trả lời đang trên đường chắc chắn không tới nữa: đăng ký
                # mất theo phiên. Đánh thức ngay thay vì để mỗi người đứng đủ
                # `timeout` giây.
                self._pending.fail_all("kết nối MQTT đứt")

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

    # ------------------------------------------------- khuôn NestJS: emit / send
    async def emit(self, pattern: Any, data: Any = None, *, qos: int = 1) -> bool:
        """Bắn một SỰ KIỆN theo khuôn NestJS — tương đương `client.emit()`.

        Topic chính là `pattern`, thân tin là gói `{"pattern", "data"}`.
        """
        topic_name = normalize_pattern(pattern)
        return await self.publish(topic_name, event_packet(pattern, data), qos=qos)

    async def send(
        self, pattern: Any, data: Any = None, *, qos: int = 1, timeout: float | None = None
    ) -> Any:
        """Gửi YÊU CẦU rồi chờ trả lời — tương đương `client.send()`.

        Topic trả lời là `<pattern>/reply`, đúng quy ước của `ClientMqtt`.

        MQTT không đếm được người nghe, nên gọi một pattern không ai trả lời
        chỉ có thể phát hiện bằng cách hết giờ — khác Redis, nơi biết ngay.
        """
        topic_name = normalize_pattern(pattern)
        deadline = timeout or DEFAULT_RPC_TIMEOUT
        await self._listen_for_replies(reply_topic_mqtt(topic_name), qos)

        # Giữ chỗ TRƯỚC khi gửi: bên kia có thể trả lời xong trước khi lệnh gửi
        # của ta kịp trả về.
        correlation_id, waiter = self._pending.open()
        try:
            await self.publish(topic_name, request_packet(pattern, data, correlation_id), qos=qos)
        except BaseException:
            self._pending.deliver(correlation_id, None)
            raise
        return await self._pending.wait(correlation_id, waiter, deadline, target=topic_name)

    async def _listen_for_replies(self, topic: str, qos: int) -> None:
        """Đăng ký topic trả lời TRƯỚC khi gửi yêu cầu, và nhớ nó cho lần nối lại."""
        if topic not in self._reply_topics:
            self._reply_topics.add(topic)
            # Ghi vào sổ đăng ký để lần nối lại sau vẫn có, chứ không chỉ
            # subscribe một lần rồi mất khi rớt mạng.
            self.subscribe_topic(topic, qos)
            self.add_router(self._on_reply)
        client = self._client
        if client is not None:
            await client.subscribe(topic, qos=qos)

    async def _on_reply(self, message: Any) -> None:
        topic = str(message.topic)
        if topic not in self._reply_topics:
            return
        packet = decode(message.payload)
        correlation_id = packet.get("id") if isinstance(packet, dict) else None
        if not isinstance(correlation_id, str):
            log.warning("mqtt.reply_without_id", topic=topic)
            return
        if not self._pending.deliver(correlation_id, packet):
            # Topic trả lời dùng chung cho mọi người gọi cùng pattern (đúng quy
            # ước NestJS), nên tin của người khác đi qua đây là bình thường.
            log.debug("mqtt.reply_not_mine", correlation_id=correlation_id)

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "url": self.url if self._config.enabled else None,
            "client_id": self._config.client_id or "(tự sinh)",
            "topics": sorted(self._to_subscribe()),
            "listeners": sorted(self._subscriptions),
            "disconnects": self._drop_count,
        }


def _tls_context() -> Any:
    import ssl

    return ssl.create_default_context()
