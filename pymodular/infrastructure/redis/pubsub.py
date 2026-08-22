"""Nhận tin từ kênh Redis pub/sub — tương đương `@rabbitmq_subscriber` của RabbitMQ.

    @injectable
    class GiaCaListener:
        @redis_subscriber("gia.*")
        async def doi_gia(self, payload: dict, meta: dict) -> None:
            ...

Khác RabbitMQ ở một điểm phải nhớ: **pub/sub của Redis không lưu gì cả**. Tin
phát ra lúc không ai nghe là mất luôn — không hàng đợi, không ack, không thử
lại, không hàng đợi chết. Nó là loa phát thanh, không phải hộp thư.

Vì vậy `@redis_subscriber` cố ý KHÔNG có `max_retries` hay `dead_letter`: bịa
ra một cơ chế thử lại ở phía client sẽ khiến người dùng tưởng tin được bảo đảm,
trong khi tin đã mất từ lúc mạng chớp. Tin không được phép mất thì dùng
RabbitMQ (hàng đợi bền) hoặc Kafka (nhật ký đọc lại được).

Ngược lại, mọi worker đang nghe đều nhận MỘT BẢN SAO — đúng thứ cần cho cập
nhật thời gian thực, xoá cache đồng loạt, thông báo nội bộ.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from pymodular.core.config import Settings
from pymodular.core.container import _REGISTRY, container, injectable, request_scope
from pymodular.core.context import new_request_id, reset_request_id, set_request_id
from pymodular.core.logging import get_logger
from pymodular.infrastructure.redis.client import RedisClient
from pymodular.infrastructure.redis.metrics import (
    redis_handler_failed,
    redis_received,
)

log = get_logger(__name__)

_SPEC_ATTR = "__redis_subscriber__"


@dataclass(slots=True)
class RedisSpec:
    channel: str
    cls: type | None = None
    fn: Callable | None = None
    model: type[BaseModel] | None = None
    wants_meta: bool = False

    @property
    def is_pattern(self) -> bool:
        """Có ký tự đại diện thì phải dùng PSUBSCRIBE thay vì SUBSCRIBE."""
        return any(ch in self.channel for ch in "*?[")

    @property
    def label(self) -> str:
        return f"{self.cls.__name__}.{self.fn.__name__}" if self.cls and self.fn else self.channel


def redis_subscriber(channel: str) -> Callable[[Callable], Callable]:
    """Gắn method vào một kênh Redis.

    Tham số duy nhất là tên kênh — và đó là chủ ý. Redis pub/sub không có hàng
    đợi để mà bền, không có ack để mà thử lại, nên không có gì khác để chỉnh.

    Kênh có `*` hoặc `?` thì tự chuyển sang PSUBSCRIBE: "gia.*" nhận mọi kênh
    bắt đầu bằng "gia.". Không có `*` thì khớp đúng tên.

    `key_prefix` trong cấu hình được ghép vào tên kênh y như với khoá, nên hai
    ứng dụng dùng chung một Redis không nghe nhầm của nhau.
    """

    def decorate(fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise RuntimeError(f"{fn.__name__} phải là `async def`")
        setattr(fn, _SPEC_ATTR, RedisSpec(channel=channel))
        return fn

    return decorate


def discover_redis_subscribers() -> list[RedisSpec]:
    """Quét mọi provider đã đăng ký để tìm method mang @redis_subscriber."""
    found: list[RedisSpec] = []
    for cls in _REGISTRY.values():
        for fn in vars(cls).values():
            spec: RedisSpec | None = getattr(fn, _SPEC_ATTR, None)
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
                RedisSpec(
                    channel=spec.channel,
                    cls=cls,
                    fn=fn,
                    model=model,
                    wants_meta=len(params) == 2,
                )
            )
    return sorted(found, key=lambda s: s.channel)


@injectable
class RedisRunner:
    """Một kết nối pub/sub duy nhất, phục vụ mọi @redis_subscriber."""

    def __init__(self, client: RedisClient, settings: Settings) -> None:
        self._client = client
        self._config = settings.redis
        self._specs: list[RedisSpec] = []
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    async def startup(self) -> None:
        if not self._config.enabled:
            return
        self._specs = discover_redis_subscribers()
        if not self._specs:
            return

        self._closing = False
        # Chạy lại sau MỖI lần nối được — kể cả lần đầu xảy ra muộn vì Redis
        # chưa lên lúc app khởi động.
        self._client.on_ready(self._setup)
        if self._client.connected:
            await self._setup()

    async def _setup(self) -> None:
        if self._task is not None and not self._task.done():
            return              # đã có vòng đọc đang chạy, đừng nhân đôi
        self._task = asyncio.create_task(self._vong_doc(), name="redis-pubsub")

    async def _vong_doc(self) -> None:
        """Đăng ký kênh rồi đọc mãi. Đứt thì tự đăng ký lại từ đầu.

        Vòng nối lại nằm ở đây chứ không dựa vào redis-py: pool của nó tự mở
        lại connection cho LỆNH kế tiếp, nhưng một pubsub đứt thì mất luôn danh
        sách kênh đã đăng ký — đọc tiếp sẽ không bao giờ có tin nào nữa.
        """
        delay = self._config.reconnect_delay_seconds
        while not self._closing:
            try:
                await self._nghe()
                delay = self._config.reconnect_delay_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - đứt kiểu gì cũng đăng ký lại
                if self._closing:
                    return
                log.warning("redis.pubsub_lost", error=f"{type(exc).__name__}: {exc}", retry=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    async def _nghe(self) -> None:
        pubsub = self._client.raw().pubsub(ignore_subscribe_messages=True)
        self._pubsub = pubsub
        try:
            kenh = [s for s in self._specs if not s.is_pattern]
            mau = [s for s in self._specs if s.is_pattern]
            if kenh:
                await pubsub.subscribe(*{self._client.key(s.channel) for s in kenh})
            if mau:
                await pubsub.psubscribe(*{self._client.key(s.channel) for s in mau})
            log.info(
                "redis.pubsub_started",
                channels=sorted({s.channel for s in kenh}),
                patterns=sorted({s.channel for s in mau}),
            )

            async for message in pubsub.listen():
                if self._closing:
                    return
                if message.get("type") not in ("message", "pmessage"):
                    continue
                await self._giao(message)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
            self._pubsub = None

    async def _giao(self, message: dict) -> None:
        kenh = str(message["channel"])
        mau = message.get("pattern")
        for spec in self._specs:
            dich = self._client.key(spec.channel)
            khop = dich == str(mau) if mau else dich == kenh
            if khop:
                await self._chay(spec, kenh, message["data"])

    async def _chay(self, spec: RedisSpec, kenh: str, raw: Any) -> None:
        redis_received.inc(channel=spec.channel)
        token = set_request_id(new_request_id())
        try:
            async with request_scope():
                payload: Any = json.loads(raw)
                if spec.model is not None:
                    try:
                        payload = spec.model.model_validate(payload)
                    except ValidationError as exc:
                        # Không có DLQ để mà đẩy vào: ghi log rồi bỏ tin. Đây
                        # là cái giá của pub/sub, nói thẳng ra hơn là giấu đi.
                        log.error("redis.payload_invalid", handler=spec.label, error=str(exc))
                        return

                instance = container.resolve(spec.cls)      # type: ignore[arg-type]
                if spec.wants_meta:
                    meta = {"channel": kenh, "pattern": spec.channel if spec.is_pattern else None}
                    await spec.fn(instance, payload, meta)  # type: ignore[misc]
                else:
                    await spec.fn(instance, payload)        # type: ignore[misc]
        except Exception as exc:
            # Một handler hỏng không được làm đứt vòng đọc của mọi handler khác.
            redis_handler_failed.inc(channel=spec.channel)
            log.exception("redis.handler_failed", handler=spec.label, error=str(exc))
        finally:
            reset_request_id(token)

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
            "listeners": [
                {
                    "handler": spec.label,
                    "channel": spec.channel,
                    "pattern": spec.is_pattern,
                }
                for spec in self._specs
            ],
            "running": self._task is not None and not self._task.done(),
        }
