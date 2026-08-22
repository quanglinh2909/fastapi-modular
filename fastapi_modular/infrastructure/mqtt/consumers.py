"""Nhận tin MQTT — tương đương `@rabbitmq_subscriber` của RabbitMQ, cho thiết bị IoT.

    @injectable
    class DenListener:
        @mqtt_subscriber("nha/+/den", qos=1)
        async def trang_thai(self, payload: dict, meta: dict) -> None:
            print(meta["topic"], payload)

Khác RabbitMQ ở chỗ **không có hàng đợi thử lại và không có DLQ**. MQTT không
cho client từ chối một tin: nhận là xong, broker coi như đã giao. Nên khi
handler ném lỗi, khung ghi log và ĐI TIẾP — bịa ra vòng thử lại trong tiến
trình sẽ chặn mọi tin phía sau của cùng một kết nối.

Cần chắc chắn không mất việc thì đẩy sang chỗ có hàng đợi ngay trong handler:
nhận tin MQTT rồi `broker.publish(...)` vào RabbitMQ, xử lý nặng ở đó.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import _REGISTRY, container, injectable, request_scope
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.mqtt.client import MqttClient
from fastapi_modular.infrastructure.mqtt.metrics import (
    mqtt_handler_failed,
    mqtt_received,
    mqtt_unrouted,
)
from fastapi_modular.infrastructure.mqtt.patterns import matches, validate_topic_filter

log = get_logger(__name__)

_SPEC_ATTR = "__mqtt_subscriber__"


@dataclass(slots=True)
class MqttSpec:
    topic: str
    qos: int = 1
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.topic


def mqtt_subscriber(topic: str, *, qos: int = 1) -> Callable[[Callable], Callable]:
    """Gắn method vào một topic MQTT (có thể chứa `+` và `#`).

        topic   bộ lọc: "nha/bep/den" khớp đúng, "nha/+/den" một tầng bất kỳ,
                "nha/#" mọi tầng phía dưới
        qos     mức bảo đảm khi broker GIAO tin cho mình:
                0 = có thì nhận, mất không biết (số đo dày đặc, mất vài cái
                    không sao)
                1 = ít nhất một lần — mặc định. Handler PHẢI chịu được tin
                    trùng, vì mạng chớp là broker giao lại.
                2 = đúng một lần, chậm hơn hẳn; chỉ dùng khi xử lý trùng thật
                    sự gây hại (trừ tiền, mở khoá)

    Hai handler cùng một topic ở hai mức QoS khác nhau thì lấy mức CAO HƠN cho
    lần đăng ký với broker.
    """
    validate_topic_filter(topic)
    if qos not in (0, 1, 2):
        raise ValueError(f"qos phải là 0, 1 hoặc 2 — nhận {qos!r}")

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(fn, _SPEC_ATTR, MqttSpec(topic=topic, qos=qos))
        return fn

    return decorate


def discover_mqtt_subscribers() -> list[MqttSpec]:
    """Quét mọi provider đã đăng ký để tìm method mang @mqtt_subscriber."""
    found: list[MqttSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: MqttSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is None:
                continue

            params = list(inspect.signature(fn).parameters.values())[1:]
            if not params or len(params) > 2:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký phải là "
                    "(self, payload) hoặc (self, payload, meta)"
                )

            hints = get_type_hints(fn)
            annotation = hints.get(params[0].name)
            model = (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
            found.append(
                MqttSpec(
                    topic=spec.topic,
                    qos=spec.qos,
                    cls=cls,
                    fn=fn,
                    model=model,
                    wants_meta=len(params) == 2,
                )
            )
    return sorted(found, key=lambda s: s.topic)


@injectable
class MqttRunner:
    """Đăng ký topic với client và chia tin về đúng handler."""

    def __init__(self, client: MqttClient, settings: Settings) -> None:
        self._client = client
        self._config = settings.mqtt
        self._specs: list[MqttSpec] = []

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._specs = discover_mqtt_subscribers()
        if not self._specs:
            return

        # Đăng ký TRƯỚC khi client mở kết nối, để lần nối đầu tiên đã có đủ
        # topic — và mọi lần nối lại sau đó cũng dùng đúng danh sách này.
        for spec in self._specs:
            self._client.subscribe_topic(spec.topic, spec.qos)
        self._client.set_router(self._giao)
        log.info(
            "mqtt.listeners_registered",
            count=len(self._specs),
            topics=sorted({s.topic for s in self._specs}),
        )

    async def _giao(self, message: Any) -> None:
        topic = str(message.topic)
        mqtt_received.inc(topic=topic)

        khop = [spec for spec in self._specs if matches(spec.topic, topic)]
        if not khop:
            # Broker giao một topic không ai nghe: gần như luôn là đăng ký thừa
            # hoặc gõ nhầm bộ lọc. Im lặng ở đây thì không cách nào biết.
            mqtt_unrouted.inc(topic=topic)
            log.debug("mqtt.unrouted", topic=topic)
            return

        for spec in khop:
            await self._chay(spec, topic, message)

    async def _chay(self, spec: MqttSpec, topic: str, message: Any) -> None:
        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                payload = _doc(message.payload)
                if spec.model is not None:
                    try:
                        payload = spec.model.model_validate(payload)
                    except ValidationError as exc:
                        # Không có DLQ để đẩy vào — thiết bị gửi sai khuôn thì
                        # ghi log kèm topic để còn lần ra đúng con nào.
                        log.error(
                            "mqtt.payload_invalid",
                            handler=spec.label,
                            topic=topic,
                            error=str(exc),
                        )
                        return

                instance = container.resolve(spec.cls)      # type: ignore[arg-type]
                if spec.wants_meta:
                    meta = {
                        "topic": topic,
                        "filter": spec.topic,
                        "qos": int(getattr(message, "qos", spec.qos)),
                        "retain": bool(getattr(message, "retain", False)),
                    }
                    await spec.fn(instance, payload, meta)  # type: ignore[misc]
                else:
                    await spec.fn(instance, payload)        # type: ignore[misc]
        except Exception as exc:
            # Ném ra khỏi đây sẽ làm đứt vòng đọc, tức là mọi handler khác im
            # theo cho tới lần nối lại. Một tin hỏng không đáng giá vậy.
            mqtt_handler_failed.inc(topic=spec.topic)
            log.exception("mqtt.handler_failed", handler=spec.label, topic=topic, error=str(exc))
        finally:
            reset_request_id(token)

    def stats(self) -> dict[str, Any]:
        return {
            "listeners": [
                {"handler": spec.label, "topic": spec.topic, "qos": spec.qos}
                for spec in self._specs
            ]
        }


def _doc(payload: Any) -> Any:
    """Thiết bị hay gửi chuỗi thuần ("ON", "23.5") chứ không phải JSON."""
    raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw
