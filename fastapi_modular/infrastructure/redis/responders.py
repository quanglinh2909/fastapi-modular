"""`@redis_responder` — bên TRẢ LỜI trên Redis, tương đương `@MessagePattern`.

Kênh yêu cầu là chính `pattern`, kênh trả lời là `<pattern>.reply` — đúng quy
ước của `ClientRedis` trong NestJS.

Một điều phải nhớ, và nó là tính chất của Redis chứ không phải của khung:
**pub/sub không lưu gì cả**. Responder chưa khởi động lúc có người gọi thì yêu
cầu bay mất, không có hàng đợi nào giữ lại. Bên gọi biết ngay điều đó — Redis
đếm được số người nghe, nên `send()` báo lỗi lập tức thay vì bắt đợi hết giờ.

Cần yêu cầu không được mất thì dùng RabbitMQ, không phải chỗ này.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    reply_channel,
)
from fastapi_modular.infrastructure.redis.client import RedisClient
from fastapi_modular.infrastructure.redis.metrics import redis_handler_failed, redis_received

log = get_logger(__name__)

_SPEC_ATTR = "__redis_responder__"


@dataclass(slots=True)
class RedisResponderSpec:
    pattern: str
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.pattern


def redis_responder(pattern: Any) -> Callable[[Callable], Callable]:
    """Gắn method vào một `pattern`; giá trị trả về được gửi về `<pattern>.reply`.

        @redis_responder("tim-nguoi-dung")
        async def tim(self, data: dict) -> dict:
            return {"id": data["id"], "ten": "An"}

    `pattern` nhận chuỗi hoặc dict, chuỗi hoá theo đúng luật NestJS.

    KHÔNG dùng ký tự đại diện ở đây (khác `@redis_subscriber`): kênh trả lời
    phải suy ra được từ pattern, mà `gia.*` thì không nói được nên trả về đâu.
    """
    channel_name = normalize_pattern(pattern)
    if not channel_name:
        raise BadRequestError("`pattern` không được để trống")
    if any(ch in channel_name for ch in "*?["):
        raise BadRequestError(
            f"`@redis_responder` không nhận ký tự đại diện ('{channel_name}'): kênh trả lời là "
            "`<pattern>.reply`, mà một mẫu thì không nói được phải trả về đâu. "
            "Nghe nhiều kênh mà không cần trả lời thì dùng `@redis_subscriber`."
        )

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(fn, _SPEC_ATTR, RedisResponderSpec(pattern=channel_name))
        return fn

    return decorate


def discover_redis_responders() -> list[RedisResponderSpec]:
    found: list[RedisResponderSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: RedisResponderSpec | None = getattr(fn, _SPEC_ATTR, None)
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

    table: dict[str, RedisResponderSpec] = {}
    for spec in found:
        if spec.pattern in table:
            raise RuntimeError(
                f"Đã có responder cho pattern '{spec.pattern}' ({table[spec.pattern].label}). "
                f"{spec.label} sẽ không bao giờ được gọi — đổi pattern đi."
            )
        table[spec.pattern] = spec
    return sorted(found, key=lambda s: s.pattern)


@injectable
class RedisResponderRunner:
    """Nghe các kênh yêu cầu và trả lời — vòng đọc riêng, không dùng chung với
    `RedisRunner`.

    Tách riêng vì hai bên đứt là hai chuyện khác nhau: `@redis_subscriber` đứt
    thì mất vài sự kiện; responder đứt thì có người đang ngồi đợi câu trả lời
    không bao giờ tới. Dùng chung một vòng đọc thì một handler sự kiện chạy lâu
    sẽ giữ luôn lượt của responder.
    """

    def __init__(self, client: RedisClient, settings: Settings) -> None:
        self._client = client
        self._config = settings.redis
        self._table: dict[str, RedisResponderSpec] = {}
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        specs = discover_redis_responders()
        if not specs:
            return
        self._table = {s.pattern: s for s in specs}
        self._closing = False
        self._client.on_ready(self._setup)
        if self._client.connected:
            await self._setup()

    async def _setup(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._read_loop(), name="redis-responders")

    async def _read_loop(self) -> None:
        delay = self._config.reconnect_delay_seconds
        while not self._closing:
            try:
                await self._listen()
                delay = self._config.reconnect_delay_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - đứt kiểu gì cũng đăng ký lại
                if self._closing:
                    return
                log.warning(
                    "redis.responders_lost", error=f"{type(exc).__name__}: {exc}", retry=delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    async def _listen(self) -> None:
        pubsub = self._client.pubsub_client().pubsub(ignore_subscribe_messages=True)
        self._pubsub = pubsub
        try:
            await pubsub.subscribe(*{self._client.key(p) for p in self._table})
            log.info("redis.responders_started", patterns=sorted(self._table))
            async for message in pubsub.listen():
                if self._closing:
                    return
                if message.get("type") != "message":
                    continue
                await self._dispatch(message)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
            self._pubsub = None

    async def _dispatch(self, message: Any) -> None:
        packet = read_packet(decode(message["data"]))
        if packet is None:
            log.warning(
                "redis.responder_bad_packet",
                channel=str(message.get("channel")),
                hint="tin không theo khuôn {pattern, data, id} — người gửi có dùng "
                     "emit()/send() hay ClientProxy của NestJS không?",
            )
            return

        pattern, data, correlation_id = packet
        redis_received.inc(channel=pattern)
        spec = self._table.get(pattern)
        if spec is None:
            log.warning("redis.no_responder", pattern=pattern, has=sorted(self._table))
            if correlation_id:
                await self._reply(pattern, correlation_id, error=NO_MESSAGE_HANDLER)
            return

        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                result = await self._run(spec, data, pattern)
        except Exception as exc:
            redis_handler_failed.inc(channel=pattern)
            log.exception("redis.responder_failed", handler=spec.label, error=str(exc))
            if correlation_id:
                await self._reply(pattern, correlation_id, error=exc)
            return
        finally:
            reset_request_id(token)

        if correlation_id:
            await self._reply(pattern, correlation_id, result=result)
        elif result is not None:
            log.debug("redis.responder_result_dropped", handler=spec.label, pattern=pattern)

    async def _run(self, spec: RedisResponderSpec, data: Any, pattern: str) -> Any:
        if spec.model is not None:
            try:
                data = spec.model.model_validate(data)
            except ValidationError as exc:
                raise BadRequestError(f"Payload không hợp lệ: {exc}") from exc
        instance = container.resolve(spec.cls)      # type: ignore[arg-type]
        if spec.wants_meta:
            return await spec.fn(instance, data, {"pattern": pattern})  # type: ignore[misc]
        return await spec.fn(instance, data)                            # type: ignore[misc]

    async def _reply(
        self,
        pattern: str,
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
        try:
            await self._client.publish(reply_channel(pattern), packet)
        except Exception as exc:  # noqa: BLE001 - việc đã xong, đường về hỏng không làm lại
            log.warning(
                "rpc.reply_failed",
                reply_to=reply_channel(pattern),
                error=f"{type(exc).__name__}: {exc}",
            )

    async def shutdown(self) -> None:
        self._closing = True
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def stats(self) -> dict[str, Any]:
        return {
            "responders": sorted(self._table),
            "running": self._task is not None and not self._task.done(),
        }
