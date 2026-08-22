"""Cầu nối giữa các tiến trình worker.

Vấn đề: sổ kết nối nằm trong BỘ NHỚ của một tiến trình. `pym run --workers 4` chạy 4
worker, mỗi worker là một tiến trình riêng. Client A nối vào worker 1, client B
nối vào worker 3; A gửi tin cho phòng "alerts" thì worker 1 chỉ thấy các kết
nối của chính nó — B không nhận được gì. Bệnh này chỉ lộ ra khi lên nhiều
worker/nhiều máy, nên rất hay bị phát hiện muộn.

Cách chữa: mỗi lần phát tin, ngoài việc gửi cho kết nối tại chỗ thì còn đăng
tin lên một kênh chung; các worker khác nghe kênh đó và gửi tiếp cho kết nối
của mình. Đây đúng là vai trò của Redis adapter trong NestJS.

Hai lựa chọn:

- `local` (mặc định): không có kênh chung. Đúng khi chạy MỘT worker. Nhanh
  nhất, không phụ thuộc gì thêm.
- `redis`: dùng Redis pub/sub. Bật bằng `APP_WS__ADAPTER=redis`, cài bằng
  `pip install 'pymodular[redis]'`. Thư viện redis chỉ được import khi thật sự chọn nó.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from pymodular.core.exceptions import ComponentNotEnabledError
from pymodular.core.logging import get_logger

log = get_logger(__name__)

OnMessage = Callable[[dict[str, Any]], None]


def new_origin_id() -> str:
    """Mã nhận dạng worker này — để không nhận lại chính tin mình vừa đăng."""
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def envelope(
    origin: str,
    namespace: str,
    event: str,
    data: Any,
    *,
    room: str | None = None,
    user: str | None = None,
    socket: str | None = None,
    exclude: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "origin": origin,
        "ns": namespace,
        "event": event,
        "data": data,
        "room": room,
        "user": user,
        "socket": socket,
        "exclude": list(exclude),
    }


class BroadcastAdapter(Protocol):
    name: str

    async def start(self, on_message: OnMessage) -> None: ...
    async def publish(self, payload: dict[str, Any]) -> None: ...
    async def stop(self) -> None: ...


class LocalAdapter:
    """Không làm gì cả — mọi kết nối đều nằm trong tiến trình này."""

    name = "local"

    async def start(self, on_message: OnMessage) -> None:
        return None

    async def publish(self, payload: dict[str, Any]) -> None:
        return None

    async def stop(self) -> None:
        return None


class RedisAdapter:
    """Phát tin xuyên worker qua Redis pub/sub.

    Cố ý dùng pub/sub chứ không phải stream: tin nhắn realtime hết giá trị sau
    vài giây, không đáng để lưu lại. Worker vừa khởi động sẽ không nhận được
    tin phát lúc nó chưa lên — đúng như mong đợi.
    """

    name = "redis"

    def __init__(self, url: str, channel: str, *, origin: str | None = None) -> None:
        self.url = url
        self.channel = channel
        self.origin = origin or new_origin_id()
        self._client: Any = None
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        self._on_message: OnMessage | None = None
        self._stopping = False

    async def start(self, on_message: OnMessage) -> None:
        try:
            import redis.asyncio as redis
        except ModuleNotFoundError as exc:
            raise ComponentNotEnabledError(
                "APP_WS__ADAPTER=redis nhưng chưa cài thư viện redis. "
                "Chạy `pip install 'pymodular[redis]'`, hoặc đổi về APP_WS__ADAPTER=local nếu "
                "chỉ chạy một worker."
            ) from exc

        self._on_message = on_message
        self._client = redis.from_url(self.url, decode_responses=True)
        await self._client.ping()   # hỏng cấu hình thì báo ngay lúc boot
        self._task = asyncio.create_task(self._listen(), name="ws-redis-rabbitmq_subscriber")
        log.info("ws.adapter_started", adapter=self.name, channel=self.channel, origin=self.origin)

    async def publish(self, payload: dict[str, Any]) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(self.channel, json.dumps(payload, default=str))
        except Exception as exc:  # noqa: BLE001 - Redis hỏng không được làm hỏng request
            # Tin đã tới được các kết nối tại chỗ; mất phần xuyên worker thôi.
            log.warning("ws.publish_failed", error=f"{type(exc).__name__}: {exc}")

    async def _listen(self) -> None:
        delay = 0.5
        while not self._stopping:
            try:
                self._pubsub = self._client.pubsub()
                await self._pubsub.subscribe(self.channel)
                delay = 0.5
                async for message in self._pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    self._dispatch(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - mất Redis phải tự nối lại, không được chết task
                if self._stopping:
                    return
                log.warning(
                    "ws.adapter_reconnecting",
                    error=f"{type(exc).__name__}: {exc}",
                    retry_in=delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    def _dispatch(self, raw: Any) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("ws.adapter_bad_payload")
            return
        if payload.get("origin") == self.origin:
            return   # tin của chính mình, đã gửi tại chỗ rồi
        if self._on_message is not None:
            self._on_message(payload)

    async def stop(self) -> None:
        # Đang tắt tiến trình: mọi lỗi dọn dẹp đều nuốt, vì báo lỗi lúc này
        # chẳng ai xử lý được mà lại che mất phần shutdown còn lại.
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
            self._task = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None


def build_adapter(kind: str, *, url: str, channel: str, origin: str) -> BroadcastAdapter:
    if kind == "redis":
        return RedisAdapter(url, channel, origin=origin)
    return LocalAdapter()
