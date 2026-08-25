"""Kết nối Redis: cache, khoá/giá trị, đếm, và đăng tin lên kênh pub/sub.

Redis là TUỲ CHỌN. `APP_REDIS__ENABLED` mặc định false thì lớp này nằm im:
không import thư viện, không mở kết nối, không đổi hành vi nào.

    pip install 'fastapi-modular[redis]'   # cài thư viện + ghi sẵn APP_REDIS__* vào .env

Lớp này ĐỘC LẬP với adapter Redis của WebSocket (`APP_WS__ADAPTER=redis`). Hai
thứ dùng chung một loại server nhưng không dùng chung cấu hình, và tắt cái này
không ảnh hưởng cái kia — giống hệt cách RabbitMQ tách khỏi WebSocket.

Về tự nối lại: redis-py giữ một pool và tự mở lại connection ở LỆNH KẾ TIẾP khi
đứt, nên không cần vòng nối lại cho tình huống mất mạng giữa chừng. Thứ nó
KHÔNG lo là lúc khởi động mà Redis chưa lên — chỗ đó lớp này tự chạy vòng thử
lại có backoff, y như RabbitMQ, để app vẫn phục vụ HTTP bình thường.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

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
    reply_channel,
    request_packet,
)
from fastapi_modular.infrastructure.redis.metrics import (
    redis_error,
    redis_hit,
    redis_miss,
    redis_published,
)

log = get_logger(__name__)

DEFAULT_URL = "redis://localhost:6379/0"

#: Nhịp PING trên kết nối pub/sub. Cần vì kết nối đó không có `socket_timeout`
#: — xem `RedisClient.pubsub_client`. Đủ thưa để không tốn gì, đủ dày để phát
#: hiện được đầu kia chết trước khi ai đó chờ quá lâu.
PUBSUB_HEALTH_CHECK_SECONDS = 30

# Giá trị "không có gì" phải phân biệt được với None đã lưu thật.
_MISSING = object()


def _require_redis() -> Any:
    try:
        import redis.asyncio as redis_asyncio
    except ModuleNotFoundError as exc:
        raise ComponentNotEnabledError(
            "APP_REDIS__ENABLED=true nhưng chưa cài thư viện redis. "
            "Chạy `pip install 'fastapi-modular[redis]'`, hoặc đặt APP_REDIS__ENABLED=false nếu "
            "dự án này không dùng Redis."
        ) from exc
    return redis_asyncio


def safe_url(url: str) -> str:
    """Che mật khẩu trước khi đưa vào log."""
    parsed = urlparse(url)
    if not parsed.password:
        return url
    netloc = f"{parsed.username or ''}:***@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


@injectable
class RedisClient:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.redis
        self._client: Any = None
        self._healthy = False
        self._supervisor: asyncio.Task[None] | None = None
        self._closing = False
        self._ready_hooks: list[Callable[[], Awaitable[None]]] = []
        self._pending = PendingReplies("Redis")
        self._rpc_pubsub: Any = None
        self._rpc_task: asyncio.Task[None] | None = None
        self._rpc_channels: set[str] = set()
        self._rpc_lock = asyncio.Lock()
        self._pubsub_client: Any = None

    # ------------------------------------------------------------- vòng đời
    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def connected(self) -> bool:
        return self._client is not None and self._healthy

    @property
    def url(self) -> str:
        return safe_url(self._config.url)

    def key(self, name: str) -> str:
        """Khoá thật sự gửi xuống Redis, đã ghép `key_prefix`."""
        return f"{self._config.key_prefix}{name}"

    async def startup(self) -> None:
        if not self._config.enabled:
            log.debug("redis.disabled")
            return

        redis_asyncio = _require_redis()
        if self._config.url == DEFAULT_URL:
            log.info("redis.default_url", url=DEFAULT_URL, hint="chưa đặt APP_REDIS__URL?")

        self._closing = False
        self._client = redis_asyncio.from_url(
            self._config.url,
            socket_connect_timeout=self._config.connect_timeout_seconds,
            socket_timeout=self._config.command_timeout_seconds,
            decode_responses=True,
            health_check_interval=30,
        )
        if await self._try_ping():
            log.info("redis.connected", url=self.url)
            await self._run_hooks()
            return

        log.warning(
            "redis.starting_degraded",
            url=self.url,
            hint="app vẫn chạy; sẽ nối lại ngầm cho tới khi được",
        )
        self._supervisor = asyncio.create_task(self._reconnect_forever(), name="redis-reconnect")

    async def _try_ping(self) -> bool:
        try:
            await self._client.ping()
        except Exception as exc:  # noqa: BLE001 - mọi lỗi đều dẫn tới cùng một việc: thử lại
            log.warning("redis.connect_failed", url=self.url, error=f"{type(exc).__name__}: {exc}")
            self._healthy = False
            return False
        self._healthy = True
        return True

    async def _reconnect_forever(self) -> None:
        delay = self._config.reconnect_delay_seconds
        while not self._closing and not self._healthy:
            await asyncio.sleep(delay)
            if self._closing:
                return
            if await self._try_ping():
                log.info("redis.recovered", url=self.url)
                await self._run_hooks()
                return
            delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    def on_ready(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Việc cần làm lại sau mỗi lần nối được — pub/sub dùng để đăng ký lại kênh."""
        self._ready_hooks.append(hook)

    async def _run_hooks(self) -> None:
        for hook in list(self._ready_hooks):
            try:
                await hook()
            except Exception as exc:
                log.exception("redis.ready_hook_failed", error=str(exc))

    async def shutdown(self) -> None:
        self._closing = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            log.info("redis.disconnected")
        await self._close_rpc()
        if self._pubsub_client is not None:
            with contextlib.suppress(Exception):
                await self._pubsub_client.aclose()
            self._pubsub_client = None
        self._client = None
        self._healthy = False

    def _ready(self) -> Any:
        if not self._config.enabled:
            raise ComponentNotEnabledError(
                "Redis đang tắt (APP_REDIS__ENABLED=false) nên không dùng được."
            )
        if self._client is None:
            raise ServiceUnavailableError("Chưa kết nối được Redis")
        return self._client

    async def _call(self, op: str, run: Callable[[Any], Awaitable[Any]]) -> Any:
        """Chạy một lệnh, đổi lỗi thư viện thành lỗi của khung.

        Đánh dấu mất kết nối khi lệnh hỏng: redis-py sẽ tự mở lại ở lệnh kế
        tiếp, nhưng /health/ready cần biết ngay là đang có vấn đề.
        """
        client = self._ready()
        try:
            result = await run(client)
        except Exception as exc:
            redis_error.inc(command=op)
            self._healthy = False
            raise ServiceUnavailableError(f"Redis lỗi khi chạy {op}: {exc}") from exc
        self._healthy = True
        return result

    # ---------------------------------------------------------- khoá/giá trị
    async def get(self, key: str, default: Any = None) -> Any:
        """Đọc một khoá. Giá trị được giải mã JSON; không có khoá thì trả `default`.

        Lỗi kết nối thì NÉM lỗi chứ không lặng lẽ trả `default` — trả về mặc
        định khi Redis chết là cách biến một sự cố hạ tầng thành dữ liệu sai.
        Cần "hỏng thì bỏ qua" thì dùng `cached()`.
        """
        raw = await self._call("get", lambda c: c.get(self.key(key)))
        return default if raw is None else _decode(raw)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: float | None = None,
        if_not_exists: bool = False,
    ) -> bool:
        """Ghi một khoá. Trả về False khi `if_not_exists=True` mà khoá đã có.

        `ttl=None` nghĩa là KHÔNG BAO GIỜ HẾT HẠN. Với dữ liệu cache thì hầu
        như luôn nên đặt một con số: khoá không hạn chỉ có thể bị xoá bằng tay,
        và Redis đầy RAM là cả hệ thống dừng.
        """
        return bool(
            await self._call(
                "set",
                lambda c: c.set(
                    self.key(key),
                    json.dumps(value, ensure_ascii=False, default=str),
                    ex=int(ttl) if ttl else None,
                    nx=if_not_exists or None,
                ),
            )
        )

    async def delete(self, *keys: str) -> int:
        """Xoá một hoặc nhiều khoá. Trả về số khoá thật sự bị xoá."""
        if not keys:
            return 0
        return int(await self._call("delete", lambda c: c.delete(*(self.key(k) for k in keys))))

    async def delete_prefix(self, prefix: str) -> int:
        """Xoá mọi khoá bắt đầu bằng `prefix` — dùng để dọn một nhóm cache.

        Duyệt bằng SCAN chứ không phải KEYS: KEYS quét toàn bộ không gian khoá
        trong MỘT lệnh và khoá chặt server suốt lúc đó, trên Redis lớn là đủ để
        làm cả ứng dụng đứng hình.
        """
        client = self._ready()
        delete = 0
        try:
            batch: list[str] = []
            async for found in client.scan_iter(match=f"{self.key(prefix)}*", count=500):
                batch.append(found)
                if len(batch) >= 500:
                    delete += int(await client.delete(*batch))
                    batch.clear()
            if batch:
                delete += int(await client.delete(*batch))
        except Exception as exc:
            redis_error.inc(command="scan")
            self._healthy = False
            raise ServiceUnavailableError(f"Redis lỗi khi quét khoá: {exc}") from exc
        self._healthy = True
        return delete

    async def exists(self, key: str) -> bool:
        return bool(await self._call("exists", lambda c: c.exists(self.key(key))))

    async def ttl(self, key: str) -> int | None:
        """Còn sống bao nhiêu giây. `None` = không hết hạn, hoặc khoá không tồn tại."""
        remaining = int(await self._call("ttl", lambda c: c.ttl(self.key(key))))
        return None if remaining < 0 else remaining

    async def incr(self, key: str, amount: int = 1, *, ttl: float | None = None) -> int:
        """Cộng dồn nguyên tử. Trả về giá trị sau khi cộng.

        Nguyên tử ở đây là điểm mấu chốt: đọc-rồi-ghi từ nhiều worker sẽ đếm
        thiếu, còn INCR thì không bao giờ. Dùng cho đếm lượt xem, siết tần suất.

        `ttl` chỉ được đặt ở lần cộng ĐẦU TIÊN (lúc khoá vừa sinh ra), nên cửa
        sổ đếm không bị gia hạn vô hạn mỗi lần có thêm một lượt.
        """
        async def run(c: Any) -> Any:
            value = await c.incrby(self.key(key), amount)
            if ttl and value == amount:
                await c.expire(self.key(key), int(ttl))
            return value

        return int(await self._call("incr", run))

    # -------------------------------------------------------------- cache
    async def cached(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        ttl: float = 60.0,
    ) -> Any:
        """Đọc cache, trượt thì gọi `factory()` rồi ghi lại.

        Đây là hàm DUY NHẤT trong lớp này CHỊU HỎNG: Redis chết thì nó ghi
        cảnh báo và gọi thẳng `factory()`, request vẫn xong. Đúng vì cache chỉ
        để nhanh hơn — mất cache là chậm đi, không phải sai đi. Mọi hàm khác
        vẫn ném lỗi, vì ở đó Redis là nguồn dữ liệu chứ không phải bộ đệm.
        """
        try:
            raw = await self._call("get", lambda c: c.get(self.key(key)))
        except Exception as exc:  # noqa: BLE001 - cache hỏng thì đi đường vòng
            log.warning("redis.cache_bypass", key=key, error=str(exc))
            return await factory()

        if raw is not None:
            redis_hit.inc(key=key)
            return _decode(raw)

        redis_miss.inc(key=key)
        value = await factory()
        try:
            await self.set(key, value, ttl=ttl)
        except Exception as exc:  # noqa: BLE001 - tính xong rồi, ghi cache hỏng không sao
            log.warning("redis.cache_write_failed", key=key, error=str(exc))
        return value

    # -------------------------------------------------------------- pub/sub
    async def publish(self, channel: str, payload: Any = None) -> int:
        """Phát một tin lên kênh. Trả về SỐ NGƯỜI NGHE đã nhận.

        Trả về 0 nghĩa là lúc này không ai nghe, và tin đó **mất luôn**. Redis
        pub/sub không lưu gì, không ack, không thử lại. Cần tin không được mất
        thì dùng RabbitMQ hoặc Kafka, không phải chỗ này.
        """
        count = int(
            await self._call(
                "publish",
                lambda c: c.publish(
                    self.key(channel), json.dumps(payload, ensure_ascii=False, default=str)
                ),
            )
        )
        redis_published.inc(channel=channel)
        if count == 0:
            log.debug("redis.publish_no_listener", channel=channel)
        return count

    # ------------------------------------------------- khuôn NestJS: emit / send
    async def emit(self, pattern: Any, data: Any = None) -> int:
        """Phát một SỰ KIỆN theo khuôn NestJS — tương đương `client.emit()`.

        Khác `publish()` ở đúng một chỗ: thân tin là gói `{"pattern", "data"}`
        thay vì payload thô, nên một `@EventPattern` bên NestJS nhận được.
        """
        return await self.publish(normalize_pattern(pattern), event_packet(pattern, data))

    async def send(self, pattern: Any, data: Any = None, *, timeout: float | None = None) -> Any:
        """Gửi YÊU CẦU rồi chờ trả lời — tương đương `client.send()`.

        Kênh trả lời là `<pattern>.reply`, đúng quy ước của `ClientRedis`.

        Redis pub/sub **không lưu gì**: không ai nghe lúc gửi thì tin mất luôn
        và lời gọi này sẽ hết giờ. Khung nói ngay ra điều đó thay vì bắt bạn
        đợi đủ `timeout` giây — xem lỗi bên dưới.
        """
        op = normalize_pattern(pattern)
        deadline = timeout or DEFAULT_RPC_TIMEOUT
        await self._listen_for_replies(self.key(reply_channel(op)))

        # Giữ chỗ TRƯỚC khi gửi: bên kia có thể trả lời xong trước khi lệnh gửi
        # của ta kịp trả về.
        correlation_id, waiter = self._pending.open()
        try:
            listeners = await self.publish(op, request_packet(pattern, data, correlation_id))
        except BaseException:
            self._pending.deliver(correlation_id, None)
            raise

        if listeners == 0:
            # Redis đếm được người nghe, nên biết chắc không ai nhận. Đợi tiếp
            # là đợi một câu trả lời không bao giờ có.
            self._pending.deliver(correlation_id, None)
            raise ServiceUnavailableError(
                f"Redis: không có ai đang nghe kênh '{self.key(op)}' nên yêu cầu này "
                "mất luôn. Bên trả lời đã khởi động chưa, và có đúng `key_prefix` không?"
            )
        return await self._pending.wait(correlation_id, waiter, deadline, target=op)

    async def _listen_for_replies(self, channels: str) -> None:
        """Đăng ký nghe kênh trả lời TRƯỚC khi gửi yêu cầu.

        Sai thứ tự là mất câu trả lời: pub/sub không lưu gì, tin phát ra lúc
        chưa đăng ký xong là bay mất, và triệu chứng trông hệt như bên kia
        không trả lời.
        """
        self._ready()
        async with self._rpc_lock:
            if self._rpc_pubsub is None:
                self._rpc_pubsub = self.pubsub_client().pubsub(ignore_subscribe_messages=True)
                self._rpc_channels = set()
            if channels not in self._rpc_channels:
                await self._rpc_pubsub.subscribe(channels)
                self._rpc_channels.add(channels)
            if self._rpc_task is None or self._rpc_task.done():
                self._rpc_task = asyncio.create_task(
                    self._reply_loop(), name="redis-rpc-reply"
                )

    async def _reply_loop(self) -> None:
        pubsub = self._rpc_pubsub
        try:
            async for message in pubsub.listen():
                if self._closing:
                    return
                if message.get("type") != "message":
                    continue
                packet = decode(message["data"])
                correlation_id = packet.get("id") if isinstance(packet, dict) else None
                if not isinstance(correlation_id, str):
                    log.warning("redis.reply_without_id", channel=str(message.get("channel")))
                    continue
                if not self._pending.deliver(correlation_id, packet):
                    # Kênh trả lời dùng chung cho MỌI người gọi cùng pattern
                    # (đúng quy ước NestJS), nên phần lớn tin ở đây là của
                    # người khác. Chỉ đáng ngó khi thấy nhiều bất thường.
                    log.debug("redis.reply_not_mine", correlation_id=correlation_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - đứt thì đánh thức người đang chờ
            if not self._closing:
                log.warning("redis.rpc_reply_lost", error=f"{type(exc).__name__}: {exc}")
        finally:
            # Đăng ký mất theo connection: xoá sổ để lần `send` sau đăng ký lại,
            # và đánh thức ngay những người đang chờ thay vì để họ đứng đủ giờ.
            self._rpc_pubsub = None
            self._rpc_channels = set()
            if not self._closing:
                self._pending.fail_all("pubsub Redis đứt")

    async def _close_rpc(self) -> None:
        self._pending.cancel_all()
        if self._rpc_task is not None:
            self._rpc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._rpc_task
            self._rpc_task = None
        if self._rpc_pubsub is not None:
            with contextlib.suppress(Exception):
                await self._rpc_pubsub.aclose()
            self._rpc_pubsub = None
        self._rpc_channels = set()

    def pubsub_client(self) -> Any:
        """Client RIÊNG cho pub/sub — cố ý KHÔNG đặt `socket_timeout`.

        `socket_timeout` là trần cho MỘT LỆNH: hợp lý với `get`/`set`, vì Redis
        chậm còn tệ hơn Redis chết. Nhưng một kết nối pub/sub thì phần lớn thời
        gian NGỒI IM chờ tin, nên cùng cái trần đó biến sự yên tĩnh bình thường
        thành lỗi — cứ `command_timeout_seconds` giây không có tin là đứt, đăng
        ký lại, rồi lại đứt. Và mọi tin phát ra trong lúc đăng ký lại thì MẤT
        LUÔN, vì pub/sub không lưu gì.

        Đổi lại bằng `health_check_interval`: redis-py tự gửi PING định kỳ, nên
        kết nối chết thật vẫn bị phát hiện — chỉ là không còn nhầm "im lặng" với
        "chết" nữa.
        """
        self._ready()
        if self._pubsub_client is None:
            redis_asyncio = _require_redis()
            self._pubsub_client = redis_asyncio.from_url(
                self._config.url,
                socket_connect_timeout=self._config.connect_timeout_seconds,
                health_check_interval=PUBSUB_HEALTH_CHECK_SECONDS,
                decode_responses=True,
            )
        return self._pubsub_client

    def raw(self) -> Any:
        """Client redis-py thô, cho những lệnh khung chưa bọc (ZSET, stream...)."""
        return self._ready()

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "url": self.url if self._config.enabled else None,
            "key_prefix": self._config.key_prefix or None,
        }


def _decode(raw: str) -> Any:
    """Giá trị do người khác ghi vào có thể không phải JSON — trả nguyên chuỗi."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw
