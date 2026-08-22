"""Kết nối RabbitMQ: khai báo exchange, đăng tin, mở hàng đợi.

RabbitMQ là TUỲ CHỌN. Không cài `aio-pika` và để `APP_RABBITMQ__ENABLED=false` (mặc
định) thì cả lớp này nằm im: không import thư viện, không mở kết nối, không
route nào đổi. Giống hệt cách driver database được tách riêng.

    pip install 'pymodular[rabbitmq]'   # cài aio-pika + ghi sẵn APP_RABBITMQ__* vào .env

Hai điều đáng nói về cách dùng kênh (channel):

1. **Kênh đăng tin tách riêng kênh nhận tin.** Một lỗi giao thức trên kênh
   (bind vào exchange không tồn tại, ack sai) sẽ ĐÓNG cả kênh đó. Dùng chung
   thì một lệnh publish sai làm chết luôn mọi consumer đang chạy.

2. **`connect_robust`, không phải `connect`.** Bản robust tự nối lại khi mạng
   rớt hoặc broker restart, và khai báo lại exchange/queue/consumer/binding đã
   đăng ký qua nó. Bản thường thì mất kết nối là mất hẳn.

Về việc tự nối lại, có ba tình huống khác nhau và phải xử lý khác nhau:

    (a) Broker CHƯA LÊN lúc app khởi động  -> `connect_robust` ném lỗi ngay
                                              (nó chỉ tự nối lại sau khi ĐÃ
                                              từng kết nối được). Lớp này tự
                                              chạy vòng nối lại có backoff.
    (b) Mất kết nối giữa chừng             -> aio-pika lo, kèm khôi phục
                                              exchange/queue/binding/consumer.
    (c) Broker restart hẳn                 -> cũng là (b) dưới góc nhìn client.

Với (a), app KHÔNG chết theo: HTTP và WebSocket vẫn phục vụ, chỉ những thao tác
cần RabbitMQ mới trả 503. Không có lựa chọn nào để tắt hành vi này — một dịch
vụ phụ chưa sẵn sàng không bao giờ đáng để cả API nằm im, và thứ tự khởi động
trong docker compose hay k8s vốn không bảo đảm.

Thiếu THƯ VIỆN thì khác hẳn thiếu KẾT NỐI: đó là lỗi cấu hình, không phải sự cố
tạm thời, nên báo ngay lúc boot chứ không thử lại.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pymodular.core.clock import utcnow
from pymodular.core.compat import TimeoutErrors
from pymodular.core.config import Settings
from pymodular.core.container import injectable
from pymodular.core.exceptions import ComponentNotEnabledError, ServiceUnavailableError
from pymodular.core.logging import get_logger
from pymodular.infrastructure.rabbitmq.metrics import rabbitmq_publish_failed, rabbitmq_published

log = get_logger(__name__)

CONTENT_TYPE = "application/json"

# Giữ đúng một chỗ, để config và cảnh báo không lệch nhau.
DEFAULT_URL = "amqp://guest:guest@localhost:5672/"

# Số tin broker giao trước khi consumer ack. Đây là mặc định của MỘT consumer,
# không phải của cả ứng dụng: giá trị đúng phụ thuộc handler chạy nhanh hay
# chậm, payload nặng hay nhẹ. Đè bằng @rabbitmq_subscriber(prefetch=...).
DEFAULT_PREFETCH = 20


def _require_aio_pika() -> Any:
    try:
        import aio_pika
    except ModuleNotFoundError as exc:
        raise ComponentNotEnabledError(
            "APP_RABBITMQ__ENABLED=true nhưng chưa cài thư viện aio-pika. "
            "Chạy `pip install 'pymodular[rabbitmq]'`, hoặc đặt APP_RABBITMQ__ENABLED=false nếu "
            "dự án này không dùng RabbitMQ."
        ) from exc
    return aio_pika


def with_heartbeat(url: str, seconds: int) -> str:
    """Thêm `?heartbeat=` vào DSN nếu người dùng chưa tự đặt.

    Không đặt thì lấy theo mặc định của server (RabbitMQ là 60s), tức mất tới
    ~120 giây mới phát hiện được một máy chủ bị rút điện. Cho tới lúc đó, mọi
    lệnh publish đều treo rồi hết giờ.
    """
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    if "heartbeat" in params or seconds <= 0:
        return url
    params["heartbeat"] = str(seconds)
    return urlunparse(parsed._replace(query=urlencode(params)))


def safe_url(url: str) -> str:
    """Che mật khẩu trước khi đưa vào log."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    credentials, _, host = rest.rpartition("@")
    user = credentials.split(":")[0] if credentials else ""
    return f"{scheme}://{user}:***@{host}" if user else f"{scheme}://{host}"


def _doc_body(body: bytes) -> Any:
    """Tin trong DLQ có thể sai khuôn — đó thường là lý do nó nằm ở đó."""
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return body.decode("utf-8", errors="replace")


@injectable
class RabbitBroker:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.rabbitmq
        self._connection: Any = None
        self._publish_channel: Any = None
        self._exchanges: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._ready_hooks: list[Callable[[], Awaitable[None]]] = []
        self._supervisor: asyncio.Task[None] | None = None
        self._closing = False

    # ------------------------------------------------------------- vòng đời
    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def connected(self) -> bool:
        """Đang thật sự nói chuyện được với broker.

        `is_closed` KHÔNG đủ: một RobustConnection đang trong vòng nối lại thì
        chưa đóng nhưng cũng chưa dùng được. Chỉ dựa vào nó thì /health/ready
        sẽ báo "ổn" trong lúc broker đã chết — đúng kiểu cảnh báo im lặng mà
        readiness sinh ra để tránh.
        """
        if self._connection is None or self._connection.is_closed:
            return False
        if getattr(self._connection, "reconnecting", False):
            return False
        ready = getattr(self._connection, "connected", None)
        return bool(ready.is_set()) if ready is not None else True

    @property
    def url(self) -> str:
        return safe_url(self._config.url)

    async def startup(self) -> None:
        """Nối tới broker. Không nối được thì lui về chế độ nối lại ngầm."""
        if not self._config.enabled:
            log.debug("mq.disabled")
            return

        # Thiếu thư viện là lỗi cấu hình -> báo ngay, thử lại cũng vô ích.
        _require_aio_pika()

        # Bật RabbitMQ mà vẫn dùng URL mặc định gần như chắc chắn là quên đặt
        # biến, hoặc đặt sai tên biến. Không kêu ở đây thì log chỉ hiện
        # "Connection refused localhost:5672" — người đọc tưởng broker chết,
        # trong khi thật ra app chưa từng đọc được URL họ đã điền.
        if self._config.url == DEFAULT_URL:
            log.warning(
                "rabbitmq.default_url",
                url=DEFAULT_URL,
                hint="chưa đặt APP_RABBITMQ__URL? Kiểm tra tên biến trong .env",
            )

        self._closing = False
        if await self._try_connect():
            return

        log.warning(
            "mq.starting_degraded",
            url=self.url,
            hint="app vẫn chạy; sẽ nối lại ngầm cho tới khi được",
        )
        self._supervisor = asyncio.create_task(self._reconnect_forever(), name="mq-reconnect")

    async def _try_connect(self) -> bool:
        try:
            await self._connect()
        except Exception as exc:  # noqa: BLE001 - mọi lỗi kết nối đều dẫn tới cùng một việc: thử lại
            log.warning("mq.connect_failed", url=self.url, error=f"{type(exc).__name__}: {exc}")
            return False
        return True

    async def _connect(self) -> None:
        """Nối xong xuôi HẲN mới ghi vào self — hỏng giữa chừng thì dọn sạch.

        Nếu gán `self._connection` ngay rồi mới mở kênh, một lỗi ở bước sau sẽ
        để lại kết nối nửa vời: `connected` trả về True nên vòng nối lại nghĩ
        mọi thứ ổn và bỏ cuộc, trong khi log vừa báo "degraded". Tôi đã thấy
        đúng cảnh đó khi thử khai một exchange sai kiểu.
        """
        aio_pika = _require_aio_pika()
        connection = await aio_pika.connect_robust(
            with_heartbeat(self._config.url, self._config.heartbeat_seconds),
            timeout=self._config.connect_timeout_seconds,
            reconnect_interval=self._config.reconnect_delay_seconds,
        )
        try:
            publish_channel = await connection.channel(publisher_confirms=True)
        except BaseException:
            await connection.close()
            raise

        self._connection = connection
        self._publish_channel = publish_channel
        self._exchanges.clear()

        # aio-pika gọi lại khi nó tự nối lại xong. Không tự khai báo lại gì ở
        # đây: RobustChannel/RobustQueue đã khôi phục exchange, hàng đợi,
        # binding và consumer. Khai lại lần nữa sẽ thành hai consumer trên cùng
        # một hàng đợi, tức mỗi tin xử lý hai lần.
        connection.reconnect_callbacks.add(self._on_reconnect)
        connection.close_callbacks.add(self._on_close)

        log.info("mq.connected", url=self.url)
        await self._run_hooks()

    async def _reconnect_forever(self) -> None:
        """Vòng nối lại cho tình huống (a): chưa từng kết nối được lần nào."""
        delay = self._config.reconnect_delay_seconds
        while not self._closing and not self.connected:
            await asyncio.sleep(delay)
            if self._closing:
                return
            if await self._try_connect():
                log.info("mq.recovered", url=self.url)
                return
            delay = min(delay * 2, self._config.max_reconnect_delay_seconds)

    def _on_reconnect(self, _sender: Any) -> Any:
        log.info("mq.reconnected", url=self.url)
        # Trả về coroutine: aio-pika await nếu callback là bất đồng bộ.
        return self._run_hooks()

    def _on_close(self, _sender: Any, exc: BaseException | None = None) -> None:
        if self._closing:
            return
        log.warning("mq.connection_lost", url=self.url, error=str(exc) if exc else None)

    # ------------------------------------------------------------------ hook
    def on_ready(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Đăng ký việc cần làm lại sau MỖI lần kết nối thành công.

        Hook phải chạy lại được nhiều lần mà không nhân đôi trạng thái —
        consumer dùng nó để dựng lại phần aio-pika không biết (ví dụ hàng đợi
        thử lại, hoặc binding sinh ra trong lúc broker đang rớt).
        """
        self._ready_hooks.append(hook)

    async def _run_hooks(self) -> None:
        for hook in list(self._ready_hooks):
            try:
                await hook()
            except Exception as exc:
                log.exception("mq.ready_hook_failed", error=str(exc))

    async def shutdown(self) -> None:
        self._closing = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
            log.info("mq.disconnected")
        self._connection = None
        self._publish_channel = None
        self._exchanges.clear()

    def _ready(self) -> None:
        if not self._config.enabled:
            raise ComponentNotEnabledError(
                "RabbitMQ đang tắt (APP_RABBITMQ__ENABLED=false) nên không đăng tin được."
            )
        if not self.connected:
            raise ServiceUnavailableError("Chưa kết nối được RabbitMQ")

    # ---------------------------------------------------------- khai báo
    async def exchange(self, name: str) -> Any:
        """Lấy (hoặc khai báo) một topic exchange. Kết quả được nhớ lại."""
        found = self._exchanges.get(name)
        if found is not None:
            return found

        self._ready()
        aio_pika = _require_aio_pika()
        async with self._lock:
            if name not in self._exchanges:
                self._exchanges[name] = await self._publish_channel.declare_exchange(
                    # Exchange luôn bền: nó chỉ là một bảng định tuyến, không
                    # giữ dữ liệu, mà mất nó thì mọi binding mất theo.
                    name, aio_pika.ExchangeType.TOPIC, durable=True
                )
                log.debug("mq.exchange_declared", exchange=name)
        return self._exchanges[name]

    async def new_channel(self, *, prefetch: int = DEFAULT_PREFETCH) -> Any:
        """Mở một kênh RIÊNG.

        Vì sao mỗi consumer cần kênh riêng: một lỗi giao thức (khai lại hàng
        đợi với tham số khác, ack sai số hiệu) làm RabbitMQ ĐÓNG cả kênh. Dùng
        chung một kênh thì một consumer khai sai sẽ kéo sập mọi consumer khác
        — đúng cảnh đã xảy ra khi tôi thử tắt/bật broker.
        """
        self._ready()
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        return channel

    async def worker_queue(self, channel: Any, hint: str) -> Any:
        """Hàng đợi RIÊNG của tiến trình này: tự sinh tên, tự xoá khi ngắt.

        Dùng cho fan-out: mỗi worker cần MỘT BẢN SAO của mọi tin. Nếu nhiều
        worker cùng nghe MỘT tên hàng đợi thì RabbitMQ chia lượt cho từng
        worker — đúng cho xử lý nền (`@rabbitmq_subscriber`), sai cho fan-out.
        """
        self._ready()
        return await channel.declare_queue(
            name="",              # để broker tự đặt tên, chắc chắn không đụng nhau
            exclusive=True,       # không ai khác nối vào được
            auto_delete=True,     # rớt kết nối là biến mất, không để rác lại
            arguments={"x-queue-type": "classic"},
        )

    async def durable_queue(
        self,
        channel: Any,
        name: str,
        *,
        durable: bool = True,
        dead_letter: bool = False,
        auto_delete: bool = False,
    ) -> Any:
        """Hàng đợi BỀN cho consumer nền: nhiều worker chia nhau xử lý.

        Mặc định khai ĐÚNG MỘT hàng đợi. `dead_letter=True` thì khai thêm
        `<name>.dlq` và trỏ hàng đợi này vào đó, để tin bị `reject` có chỗ nằm
        lại thay vì biến mất.

        `auto_delete=True` thì broker XOÁ hàng đợi khi consumer cuối cùng ngắt,
        và mọi tin còn nằm trong đó mất theo. Mặc định là giữ lại: app tắt (hoặc
        deploy) thì tin vẫn đọng ở broker, chạy lên là xử lý tiếp.
        """
        self._ready()
        arguments: dict[str, Any] = {}
        if dead_letter:
            dlx = await self._declare_dead_letter(channel, name, durable=durable)
            arguments["x-dead-letter-exchange"] = dlx
            arguments["x-dead-letter-routing-key"] = name

        return await self._declare(
            channel,
            name,
            durable=durable,
            auto_delete=auto_delete,
            arguments=arguments or None,
        )

    async def _declare_dead_letter(
        self, channel: Any, queue_name: str, *, durable: bool = True
    ) -> str:
        aio_pika = _require_aio_pika()
        dlx_name = self._config.dead_letter_exchange
        dlx = await channel.declare_exchange(
            dlx_name, aio_pika.ExchangeType.DIRECT, durable=True
        )
        dlq = await self._declare(channel, f"{queue_name}.dlq", durable=durable)
        await dlq.bind(dlx, routing_key=queue_name)
        return dlx_name

    async def _declare(self, channel: Any, name: str, **kwargs: Any) -> Any:
        """Khai báo hàng đợi, đổi lỗi khó hiểu của RabbitMQ thành lời nói người.

        PRECONDITION_FAILED xảy ra khi hàng đợi đã tồn tại với tham số khác
        (đổi thời gian thử lại, bật/tắt dead-letter...). RabbitMQ KHÔNG cho sửa
        tham số của hàng đợi đã có — phải xoá rồi tạo lại, và thông báo gốc thì
        không hề nói vậy.
        """
        try:
            return await channel.declare_queue(name, **kwargs)
        except Exception as exc:
            if "PRECONDITION_FAILED" not in str(exc):
                raise
            raise ServiceUnavailableError(
                f"Hàng đợi '{name}' đã tồn tại với tham số khác: {exc}. "
                "RabbitMQ không cho đổi tham số của hàng đợi đã có — xoá hàng đợi cũ "
                f"(rabbitmqctl delete_queue {name}) rồi khởi động lại, hoặc đổi tên hàng đợi."
            ) from exc

    async def queue_info(self, name: str) -> dict[str, int] | None:
        """Số tin đang chờ và số consumer đang nghe. `None` nếu hàng đợi không có.

        Hỏi bằng cách khai báo `passive` trên một kênh DÙNG-MỘT-LẦN: hàng đợi
        không tồn tại thì RabbitMQ trả NOT_FOUND và ĐÓNG luôn kênh đó. Hỏi trên
        kênh đang có việc thì mỗi câu hỏi hụt là một kênh chết.
        """
        if not self.connected:
            return None
        channel = await self._connection.channel()
        try:
            queue = await channel.declare_queue(name, passive=True)
            result = queue.declaration_result
            return {
                "messages": int(result.message_count or 0),
                "consumers": int(result.consumer_count or 0),
            }
        except Exception:  # noqa: BLE001 - không tồn tại, hoặc kênh vừa bị đóng
            return None
        finally:
            with contextlib.suppress(Exception):
                await channel.close()

    async def queue_exists(self, name: str) -> bool:
        return await self.queue_info(name) is not None

    async def peek(self, name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Xem tin trong hàng đợi mà KHÔNG lấy đi — để soi `<queue>.dlq`.

        Giữ tất cả tin lấy được rồi mới trả lại một lượt, chứ không trả từng
        tin: trả ngay thì tin quay về đầu hàng đợi và lần lấy kế tiếp vớ đúng
        nó, xem 10 tin trên hàng đợi 2 tin sẽ ra 10 dòng trùng nhau.

        Đây là công cụ để NHÌN, không phải để xử lý. Tin đang nằm trong tay một
        consumer khác thì không hiện ra ở đây, và thứ tự sau khi trả lại có thể
        đổi.
        """
        if not self.connected:
            return []
        channel = await self._connection.channel()
        giu: list[Any] = []
        found: list[dict[str, Any]] = []
        try:
            queue = await channel.declare_queue(name, passive=True)
            for _ in range(max(1, limit)):
                message = await queue.get(no_ack=False, fail=False)
                if message is None:
                    break
                giu.append(message)
                found.append(
                    {
                        "message_id": message.message_id,
                        "routing_key": message.routing_key,
                        "headers": dict(message.headers or {}),
                        "body": _doc_body(message.body),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - soi hàng đợi không được thì trả rỗng
            log.debug("mq.peek_failed", queue=name, error=f"{type(exc).__name__}: {exc}")
        finally:
            for message in giu:
                with contextlib.suppress(Exception):
                    await message.nack(requeue=True)
            with contextlib.suppress(Exception):
                await channel.close()
        return found

    async def delete_queue(self, name: str, *, if_unused: bool = True) -> bool:
        """Xoá một hàng đợi. Trả về False nếu không có, hoặc còn người đang nghe.

        `if_unused=True` là chốt an toàn: hàng đợi nào còn consumer thì broker
        từ chối xoá, nên không cắt ngang worker khác đang chạy.
        """
        if not self.connected:
            return False
        channel = await self._connection.channel()
        try:
            await channel.queue_delete(name, if_unused=if_unused)
            return True
        except Exception as exc:  # noqa: BLE001 - dọn dẹp: không xoá được thì thôi
            log.debug("mq.queue_delete_failed", queue=name, error=f"{type(exc).__name__}: {exc}")
            return False
        finally:
            with contextlib.suppress(Exception):
                await channel.close()

    async def retry_queue(
        self, channel: Any, name: str, target_queue: str, *, durable: bool = True
    ) -> Any:
        """Hàng đợi tạm để thử lại sau một khoảng chờ.

        Mẹo quen thuộc của RabbitMQ, không cần plugin: hàng đợi này KHÔNG có ai
        nghe. Tin nằm đó tới khi hết hạn thì "chết" và được đẩy ngược về hàng
        đợi chính. Nhờ vậy có chờ giữa các lần thử mà không phải `sleep` trong
        consumer — sleep sẽ giữ luôn suất prefetch và làm nghẽn cả hàng đợi.

        Thời gian chờ đặt trên TỪNG TIN (`expiration`) chứ không phải trên hàng
        đợi (`x-message-ttl`). Lý do rất thực tế: RabbitMQ KHÔNG cho khai lại
        hàng đợi đã tồn tại với tham số khác, nên chỉ cần đổi
        APP_RABBITMQ__CONSUMER_RETRY_DELAY_SECONDS là lần khởi động sau chết với
        PRECONDITION_FAILED. Tôi đã dính đúng lỗi này khi chạy test với thời
        gian chờ khác lúc chạy dev.
        """
        self._ready()
        return await self._declare(
            channel,
            name,
            durable=durable,
            arguments={
                "x-dead-letter-exchange": "",           # exchange mặc định
                "x-dead-letter-routing-key": target_queue,
            },
        )

    # ------------------------------------------------------------- đăng tin
    async def publish(
        self,
        exchange: str,
        routing_key: str,
        payload: Any = None,
        *,
        headers: dict[str, Any] | None = None,
        persistent: bool = True,
        timeout: float | None = None,
        fire_and_forget: bool = False,
    ) -> bool:
        """Đăng một tin lên exchange. Trả về True nếu broker đã xác nhận.

        `fire_and_forget=True` thì RabbitMQ hỏng chỉ ghi cảnh báo thay vì ném
        lỗi — dùng cho thông báo phụ, nơi mất tin còn hơn hỏng cả request. Mặc
        định là ném lỗi, vì im lặng nuốt tin là thứ khó lần ra nhất.
        """
        aio_pika = _require_aio_pika()
        try:
            target = await self.exchange(exchange)
            message = aio_pika.Message(
                body=json.dumps(payload, ensure_ascii=False, default=str).encode(),
                content_type=CONTENT_TYPE,
                content_encoding="utf-8",
                message_id=uuid.uuid4().hex,
                timestamp=utcnow(),
                delivery_mode=(
                    aio_pika.DeliveryMode.PERSISTENT
                    if persistent
                    else aio_pika.DeliveryMode.NOT_PERSISTENT
                ),
                headers=headers or {},
            )
            await asyncio.wait_for(
                target.publish(message, routing_key=routing_key),
                timeout or self._config.publish_timeout_seconds,
            )
        except TimeoutErrors as exc:
            # Broker chết kiểu "im lặng" (rút điện, mất mạng): kết nối vẫn mở
            # trên giấy tờ nên lệnh gửi treo tới hết hạn. Trả 503 với đúng tên
            # thành phần thay vì để lỗi chung chung nổi lên.
            rabbitmq_publish_failed.inc(exchange=exchange)
            if fire_and_forget:
                log.warning("mq.publish_timeout", exchange=exchange, routing_key=routing_key)
                return False
            raise ServiceUnavailableError(
                f"RabbitMQ không phản hồi trong {self._config.publish_timeout_seconds}s"
            ) from exc
        except Exception as exc:
            rabbitmq_publish_failed.inc(exchange=exchange)
            if fire_and_forget:
                log.warning(
                    "mq.publish_failed",
                    exchange=exchange,
                    routing_key=routing_key,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return False
            raise

        rabbitmq_published.inc(exchange=exchange, routing_key=routing_key)
        log.debug("mq.published", exchange=exchange, routing_key=routing_key)
        return True

    async def publish_to_queue(
        self,
        queue: str,
        body: bytes,
        *,
        headers: dict[str, Any] | None = None,
        expiration: float | None = None,
        persistent: bool = True,
    ) -> None:
        """Gửi thẳng vào MỘT hàng đợi, không qua exchange nào.

        Dùng exchange mặc định (tên rỗng) của AMQP: nó route theo đúng tên hàng
        đợi. Cần cho việc hẹn xử lý lại — tin phải vào đúng `<queue>.retry`,
        không được phát tán theo mẫu như exchange topic.
        """
        aio_pika = _require_aio_pika()
        self._ready()
        message = aio_pika.Message(
            body=body,
            content_type=CONTENT_TYPE,
            content_encoding="utf-8",
            delivery_mode=(
                aio_pika.DeliveryMode.PERSISTENT
                if persistent
                else aio_pika.DeliveryMode.NOT_PERSISTENT
            ),
            headers=headers or {},
            expiration=expiration,
        )
        await asyncio.wait_for(
            self._publish_channel.default_exchange.publish(message, routing_key=queue),
            self._config.publish_timeout_seconds,
        )

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "url": self.url if self._config.enabled else None,
            "exchanges": sorted(self._exchanges),
        }
