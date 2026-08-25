"""`@mqtt_responder` — bên TRẢ LỜI trên MQTT, tương đương `@MessagePattern`.

Topic yêu cầu là chính `pattern`, topic trả lời là `<pattern>/reply` — đúng quy
ước của `ClientMqtt` trong NestJS (dấu ngăn là `/`, không phải `.` như Redis).

MQTT **không đếm được người nghe**, nên gọi một pattern không ai trả lời chỉ
phát hiện được bằng cách hết giờ. Khác Redis, nơi bên gọi biết ngay lập tức.

Và nhớ: thiết bị MQTT thường ở xa, mạng chập chờn. `send` qua MQTT hợp với việc
hỏi trạng thái một thiết bị đang online, không hợp với việc nào mà mất câu trả
lời là hỏng nghiệp vụ.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import _REGISTRY, container, injectable, request_scope
from fastapi_modular.core.context import new_request_id, reset_request_id, set_request_id
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.rpc import (
    NO_MESSAGE_HANDLER,
    decode,
    error_packet,
    normalize_pattern,
    ok_packet,
    read_packet,
    reply_topic_mqtt,
)
from fastapi_modular.infrastructure.mqtt.client import MqttClient
from fastapi_modular.infrastructure.mqtt.metrics import mqtt_handler_failed, mqtt_received
from fastapi_modular.infrastructure.mqtt.patterns import validate_topic

log = get_logger(__name__)

_SPEC_ATTR = "__mqtt_responder__"


@dataclass(slots=True)
class MqttResponderSpec:
    pattern: str
    qos: int = 1
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.pattern


def mqtt_responder(pattern: Any, *, qos: int = 1) -> Callable[[Callable], Callable]:
    """Gắn method vào một `pattern`; giá trị trả về được gửi về `<pattern>/reply`.

        @mqtt_responder("thiet-bi/dahua-01/trang-thai")
        async def trang_thai(self, data: dict) -> dict:
            return {"online": True, "nhiet_do": 41.2}

    `pattern` phải là topic CỤ THỂ, không có `+` hay `#`: topic trả lời suy ra
    từ nó, mà một bộ lọc thì không nói được phải trả về đâu. Nghe nhiều topic mà
    không cần trả lời thì dùng `@mqtt_subscriber`.
    """
    topic_name = normalize_pattern(pattern)
    if not topic_name:
        raise BadRequestError("`pattern` không được để trống")
    if any(ch in topic_name for ch in "+#"):
        raise BadRequestError(
            f"`@mqtt_responder` cần topic cụ thể, không nhận bộ lọc ('{topic_name}'): topic trả "
            "lời là `<pattern>/reply`, mà một bộ lọc thì không nói được phải trả về đâu. "
            "Nghe nhiều topic mà không cần trả lời thì dùng `@mqtt_subscriber`."
        )
    validate_topic(topic_name)
    if qos not in (0, 1, 2):
        raise BadRequestError("qos phải là 0, 1 hoặc 2")

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(fn, _SPEC_ATTR, MqttResponderSpec(pattern=topic_name, qos=qos))
        return fn

    return decorate


def discover_mqtt_responders() -> list[MqttResponderSpec]:
    found: list[MqttResponderSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: MqttResponderSpec | None = getattr(fn, _SPEC_ATTR, None)
            if spec is None:
                continue
            params = list(inspect.signature(fn).parameters.values())[1:]
            if not params or len(params) > 2:
                raise RuntimeError(
                    f"{cls.__name__}.{fn.__name__}: chữ ký phải là "
                    "(self, data) hoặc (self, data, meta)"
                )
            hints = get_type_hints(fn)
            annotation = hints.get(params[0].name)
            model = (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
            found.append(replace(spec, cls=cls, fn=fn, model=model, wants_meta=len(params) == 2))

    table: dict[str, MqttResponderSpec] = {}
    for spec in found:
        if spec.pattern in table:
            raise RuntimeError(
                f"Đã có responder cho pattern '{spec.pattern}' ({table[spec.pattern].label}). "
                f"{spec.label} sẽ không bao giờ được gọi — đổi pattern đi."
            )
        table[spec.pattern] = spec
    return sorted(found, key=lambda s: s.pattern)


@injectable
class MqttResponderRunner:
    """Đăng ký topic yêu cầu và trả lời cho mọi @mqtt_responder tìm được.

    Phải chạy TRƯỚC `MqttClient.startup()`, như `MqttRunner`: danh sách topic
    được gửi lên broker ngay trong lần bắt tay đầu tiên.
    """

    def __init__(self, client: MqttClient, settings: Settings) -> None:
        self._client = client
        self._config = settings.mqtt
        self._table: dict[str, MqttResponderSpec] = {}

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        specs = discover_mqtt_responders()
        if not specs:
            return
        self._table = {s.pattern: s for s in specs}
        for spec in specs:
            self._client.subscribe_topic(spec.pattern, spec.qos)
        self._client.add_router(self._dispatch)
        log.info("mqtt.responders_registered", patterns=sorted(self._table))

    async def _dispatch(self, message: Any) -> None:
        topic = str(message.topic)
        spec = self._table.get(topic)
        if spec is None:
            return          # tin của `@mqtt_subscriber` hoặc của chỗ nhận trả lời

        mqtt_received.inc(topic=topic)
        packet = read_packet(decode(message.payload))
        if packet is None:
            log.warning(
                "mqtt.responder_bad_packet",
                topic=topic,
                hint="tin không theo khuôn {pattern, data, id} — người gửi có dùng "
                     "emit()/send() hay ClientProxy của NestJS không?",
            )
            return

        pattern, data, correlation_id = packet
        if pattern != topic:
            # NestJS gửi pattern trong thân tin ĐÚNG bằng topic. Lệch nhau nghĩa
            # là ai đó tự dựng gói tin sai, và nếu cứ chạy thì handler này xử lý
            # một yêu cầu không dành cho nó.
            log.warning("mqtt.responder_pattern_lech", topic=topic, pattern=pattern)
            if correlation_id:
                await self._reply(topic, correlation_id, error=NO_MESSAGE_HANDLER)
            return

        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                result = await self._run(spec, data, topic, message)
        except Exception as exc:
            mqtt_handler_failed.inc(topic=topic)
            log.exception("mqtt.responder_failed", handler=spec.label, error=str(exc))
            if correlation_id:
                await self._reply(topic, correlation_id, error=exc)
            return
        finally:
            reset_request_id(token)

        if correlation_id:
            await self._reply(topic, correlation_id, result=result)
        elif result is not None:
            log.debug("mqtt.responder_result_dropped", handler=spec.label, topic=topic)

    async def _run(
        self, spec: MqttResponderSpec, data: Any, topic: str, message: Any
    ) -> Any:
        if spec.model is not None:
            try:
                data = spec.model.model_validate(data)
            except ValidationError as exc:
                raise BadRequestError(f"Payload không hợp lệ: {exc}") from exc
        instance = container.resolve(spec.cls)      # type: ignore[arg-type]
        if spec.wants_meta:
            meta = {
                "pattern": topic,
                "topic": topic,
                "qos": int(getattr(message, "qos", spec.qos)),
            }
            return await spec.fn(instance, data, meta)   # type: ignore[misc]
        return await spec.fn(instance, data)             # type: ignore[misc]

    async def _reply(
        self,
        topic: str,
        correlation_id: str,
        *,
        result: Any = None,
        error: BaseException | str | None = None,
    ) -> None:
        packet = (
            error_packet(correlation_id, error)
            if error is not None
            else ok_packet(correlation_id, result)
        )
        target = reply_topic_mqtt(topic)
        try:
            # `fire_and_forget`: việc đã làm xong rồi, đường về hỏng không đáng
            # để ném lỗi ngược lên và làm hỏng cả vòng đọc.
            await self._client.publish(target, packet, qos=1, fire_and_forget=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("rpc.reply_failed", reply_to=target, error=f"{type(exc).__name__}: {exc}")

    def stats(self) -> dict[str, Any]:
        return {"responders": sorted(self._table)}
