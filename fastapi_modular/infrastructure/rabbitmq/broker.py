"""Kết nối RabbitMQ: khai báo exchange, đăng tin, mở hàng đợi.

RabbitMQ là TUỲ CHỌN. Không cài `aio-pika` và để `APP_RABBITMQ__ENABLED=false` (mặc
định) thì cả lớp này nằm im: không import thư viện, không mở kết nối, không
route nào đổi. Giống hệt cách driver database được tách riêng.

    pip install 'fastapi-modular[rabbitmq]'   # cài aio-pika + ghi sẵn APP_RABBITMQ__* vào .env

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

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.compat import TimeoutErrors
from fastapi_modular.core.config import Settings
from fastapi_modular.core.container import injectable
from fastapi_modular.core.exceptions import ComponentNotEnabledError, ServiceUnavailableError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.rpc import (
    DEFAULT_RPC_TIMEOUT,
    RMQ_REPLY_QUEUE,
    PendingReplies,
    decode,
    event_packet,
    normalize_pattern,
    request_packet,
)
from fastapi_modular.infrastructure.rabbitmq.metrics import (
    rabbitmq_publish_failed,
    rabbitmq_published,
)
from fastapi_modular.infrastructure.rabbitmq.patterns import EXCHANGE_KINDS, ExchangeKind

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
            "Chạy `pip install 'fastapi-modular[rabbitmq]'`, hoặc đặt APP_RABBITMQ__ENABLED=false nếu "
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


def _mili_giay(giay: float, ten: str) -> int:
    """Đổi giây (đơn vị của thư viện) sang mili-giây (đơn vị của AMQP).

    Mọi thời lượng trong khung này tính bằng GIÂY. AMQP thì tính bằng mili-giây.
    Nhầm đơn vị ở đây không báo lỗi gì cả — chỉ là tin sống lâu gấp nghìn lần,
    hoặc chết ngay khi vừa tới. Quy đổi một chỗ duy nhất là ở đây.
    """
    if giay <= 0:
        raise ServiceUnavailableError(
            f"`{ten}` phải lớn hơn 0 giây (đang là {giay}). Không cần giới hạn thì bỏ hẳn tham số."
        )
    return max(1, round(giay * 1000))


@injectable
class RabbitBroker:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.rabbitmq
        self._connection: Any = None
        self._publish_channel: Any = None
        self._exchanges: dict[str, Any] = {}
        self._exchange_kinds: dict[str, str] = {}
        self._rpc_channel: Any = None
        self._so_cho = PendingReplies("RabbitMQ")
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
        self._exchange_kinds.clear()

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
        # Kênh RPC chết theo kết nối, và hàng đợi trả lời `amq.rabbitmq.reply-to`
        # KHÔNG sống sót qua lần nối lại — nó gắn với đúng một kênh. Ai đang chờ
        # thì câu trả lời của họ chắc chắn không bao giờ tới nữa; đánh thức ngay
        # thay vì để mỗi người đứng thêm đủ `timeout` giây.
        self._rpc_channel = None
        self._so_cho.fail_all("kết nối RabbitMQ đứt")

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
        self._rpc_channel = None
        self._so_cho.cancel_all()
        self._exchanges.clear()
        self._exchange_kinds.clear()

    def _ready(self) -> None:
        if not self._config.enabled:
            raise ComponentNotEnabledError(
                "RabbitMQ đang tắt (APP_RABBITMQ__ENABLED=false) nên không đăng tin được."
            )
        if not self.connected:
            raise ServiceUnavailableError("Chưa kết nối được RabbitMQ")

    # ---------------------------------------------------------- khai báo
    async def exchange(self, name: str, kind: ExchangeKind | None = None) -> Any:
        """Lấy (hoặc khai báo) một exchange. Kết quả được nhớ lại.

        `kind=None` nghĩa là "kiểu nào cũng được": exchange này đã khai trong
        tiến trình rồi thì dùng lại đúng kiểu đó, chưa có thì khai `topic`. Nhờ
        vậy bên đăng tin không phải nhắc lại kiểu mà consumer đã khai — quên
        nhắc là chuyện gần như chắc chắn xảy ra, và cái giá của nó rất đắt (xem
        dưới).

        Tên rỗng là exchange MẶC ĐỊNH của AMQP: có sẵn ở mọi broker, không khai
        báo được, không bind được, route thẳng theo tên hàng đợi.
        """
        self._ready()
        if name == "":
            return self._publish_channel.default_exchange

        da_khai = self._exchange_kinds.get(name)
        if da_khai is not None:
            if kind is not None and kind != da_khai:
                # Chặn TẠI CHỖ thay vì để broker chặn: khai lại exchange với
                # kiểu khác là lỗi giao thức, RabbitMQ đáp PRECONDITION_FAILED
                # rồi ĐÓNG kênh đăng tin — kéo theo mọi lời publish khác của
                # tiến trình này, không chỉ lời gọi sai.
                raise ServiceUnavailableError(
                    f"Exchange '{name}' đã khai kiểu '{da_khai}', giờ lại đòi kiểu "
                    f"'{kind}'. Một exchange chỉ có MỘT kiểu và RabbitMQ không cho "
                    "đổi — sửa cho khớp, hoặc dùng tên exchange khác."
                )
            return self._exchanges[name]

        if kind is not None and kind not in EXCHANGE_KINDS:
            raise ServiceUnavailableError(
                f"Kiểu exchange '{kind}' không có. Chọn: {', '.join(EXCHANGE_KINDS)}."
            )
        if kind == "default":
            raise ServiceUnavailableError(
                f"Kiểu 'default' là exchange tên rỗng có sẵn của AMQP, không đặt "
                f"tên được — bỏ tên '{name}' đi, hoặc chọn kiểu khác."
            )

        aio_pika = _require_aio_pika()
        kieu = kind or "topic"
        async with self._lock:
            if name in self._exchanges:
                # Một lời gọi khác vừa khai xong trong lúc ta chờ khoá. Kiểm lại
                # kiểu ở đây nữa, nếu không hai lời gọi ĐẦU TIÊN chạy song song
                # với hai kiểu khác nhau sẽ lọt qua chốt phía trên.
                if kind is not None and kind != self._exchange_kinds.get(name):
                    raise ServiceUnavailableError(
                        f"Exchange '{name}' đã khai kiểu "
                        f"'{self._exchange_kinds.get(name)}', giờ lại đòi kiểu "
                        f"'{kind}'. Một exchange chỉ có MỘT kiểu và RabbitMQ không "
                        "cho đổi — sửa cho khớp, hoặc dùng tên exchange khác."
                    )
            else:
                try:
                    self._exchanges[name] = await self._publish_channel.declare_exchange(
                        # Exchange luôn bền: nó chỉ là một bảng định tuyến, không
                        # giữ dữ liệu, mà mất nó thì mọi binding mất theo.
                        name, aio_pika.ExchangeType(kieu), durable=True
                    )
                except Exception as exc:
                    if "PRECONDITION_FAILED" not in str(exc):
                        raise
                    raise ServiceUnavailableError(
                        f"Exchange '{name}' đã tồn tại trên broker với kiểu KHÁC "
                        f"'{kieu}': {exc}. RabbitMQ không cho đổi kiểu của exchange "
                        f"đã có — xoá nó (rabbitmqctl delete_exchange {name}) rồi "
                        "khởi động lại, hoặc đổi tên exchange."
                    ) from exc
                self._exchange_kinds[name] = kieu
                log.debug("mq.exchange_declared", exchange=name, kind=kieu)
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
        message_ttl: float | None = None,
        queue_expires: float | None = None,
    ) -> Any:
        """Hàng đợi BỀN cho consumer nền: nhiều worker chia nhau xử lý.

        Mặc định khai ĐÚNG MỘT hàng đợi. `dead_letter=True` thì khai thêm
        `<name>.dlq` và trỏ hàng đợi này vào đó, để tin bị `reject` có chỗ nằm
        lại thay vì biến mất.

        `auto_delete=True` thì broker XOÁ hàng đợi khi consumer cuối cùng ngắt,
        và mọi tin còn nằm trong đó mất theo. Mặc định là giữ lại: app tắt (hoặc
        deploy) thì tin vẫn đọng ở broker, chạy lên là xử lý tiếp.

        Hai hạn dùng, tính bằng GIÂY, mặc định không có cái nào:

            message_ttl     tin nằm trong hàng đợi quá lâu thì bỏ (x-message-ttl)
            queue_expires   hàng đợi không ai dùng quá lâu thì broker xoá (x-expires)

        Kèm `dead_letter=True` thì tin hết hạn không bốc hơi mà rơi vào
        `<name>.dlq` — cách duy nhất để biết mình đã bỏ mất những gì.

        CẢNH BÁO: cả hai đi vào tham số khai báo hàng đợi, mà RabbitMQ không cho
        khai lại hàng đợi đã tồn tại với tham số khác. Đổi con số rồi khởi động
        lại mà chưa xoá hàng đợi cũ thì gặp PRECONDITION_FAILED.
        """
        self._ready()
        arguments: dict[str, Any] = {}
        if message_ttl is not None:
            arguments["x-message-ttl"] = _mili_giay(message_ttl, "message_ttl")
        if queue_expires is not None:
            arguments["x-expires"] = _mili_giay(queue_expires, "queue_expires")
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
        routing_key: str = "",
        payload: Any = None,
        *,
        exchange_type: ExchangeKind | None = None,
        headers: dict[str, Any] | None = None,
        persistent: bool = True,
        ttl: float | None = None,
        timeout: float | None = None,
        fire_and_forget: bool = False,
    ) -> bool:
        """Đăng một tin lên exchange. Trả về True nếu broker đã xác nhận.

        `fire_and_forget=True` thì RabbitMQ hỏng chỉ ghi cảnh báo thay vì ném
        lỗi — dùng cho thông báo phụ, nơi mất tin còn hơn hỏng cả request. Mặc
        định là ném lỗi, vì im lặng nuốt tin là thứ khó lần ra nhất.

        `exchange_type` chỉ cần khi tiến trình này CHỈ đăng tin, không có
        consumer nào khai exchange đó trước. Có consumer rồi thì bỏ trống, khung
        dùng lại đúng kiểu đã khai.

        `ttl` (giây) đặt hạn dùng cho RIÊNG tin này: quá hạn mà chưa ai lấy thì
        broker bỏ nó. Khác `message_ttl` của hàng đợi ở chỗ nó theo từng tin, nên
        đổi lúc nào cũng được — không dính PRECONDITION_FAILED.

        Với exchange `fanout`/`headers`/mặc định thì `routing_key` bị bỏ qua;
        `headers` chọn hàng đợi theo `headers`.
        """
        aio_pika = _require_aio_pika()
        try:
            target = await self.exchange(exchange, exchange_type)
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
                expiration=ttl,
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
        correlation_id: str | None = None,
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
            # Đây là chỗ NestJS đối chiếu câu trả lời với yêu cầu: client của nó
            # nghe hàng đợi trả lời rồi phát theo `msg.properties.correlationId`,
            # KHÔNG đọc `id` trong thân tin. Thiếu thuộc tính này thì câu trả lời
            # về tới nơi nhưng không ai nhận, và người gọi vẫn đợi tới hết giờ.
            correlation_id=correlation_id,
        )
        await asyncio.wait_for(
            self._publish_channel.default_exchange.publish(message, routing_key=queue),
            self._config.publish_timeout_seconds,
        )

    # ------------------------------------------------- khuôn NestJS: emit / send
    def _dia_chi(
        self, queue: str | None, exchange: str, routing_key: str | None
    ) -> tuple[str, str]:
        """Chốt (exchange, routing key) từ hai cách khai địa chỉ.

        Hai cách, cố ý không trộn lẫn:

            queue="math-queue"                 kiểu NestJS: gửi thẳng vào hàng
                                               đợi qua exchange mặc định, đúng
                                               như `ClientRMQ.sendToQueue`
            exchange="events", routing_key=…   kiểu AMQP: định tuyến như thường
        """
        if queue is not None:
            if exchange or routing_key is not None:
                raise ServiceUnavailableError(
                    "Khai `queue=` là gửi thẳng vào hàng đợi (kiểu NestJS), nên không "
                    "kèm `exchange=`/`routing_key=` được. Chọn một trong hai cách."
                )
            return "", queue
        if not exchange and routing_key is None:
            raise ServiceUnavailableError(
                "Chưa nói gửi đi đâu: khai `queue=\"tên-hàng-đợi\"` (kiểu NestJS), "
                "hoặc `exchange=`/`routing_key=` (kiểu AMQP)."
            )
        return exchange, routing_key or ""

    async def emit(
        self,
        pattern: Any,
        data: Any = None,
        *,
        queue: str | None = None,
        exchange: str = "",
        routing_key: str | None = None,
        exchange_type: ExchangeKind | None = None,
        headers: dict[str, Any] | None = None,
        persistent: bool = True,
        ttl: float | None = None,
        timeout: float | None = None,
        fire_and_forget: bool = False,
    ) -> bool:
        """Bắn một SỰ KIỆN theo khuôn NestJS — tương đương `client.emit()`.

        Khác `publish()` ở đúng một chỗ: thân tin là gói `{"pattern", "data"}`
        thay vì payload thô. Nhờ vậy một `@EventPattern` bên NestJS nhận được,
        và ngược lại.

        Không có `id` trong gói, nên bên kia biết là **không phải trả lời**.
        """
        ex, rk = self._dia_chi(queue, exchange, routing_key)
        return await self.publish(
            exchange=ex,
            routing_key=rk,
            payload=event_packet(pattern, data),
            exchange_type=exchange_type,
            headers=headers,
            persistent=persistent,
            ttl=ttl,
            timeout=timeout,
            fire_and_forget=fire_and_forget,
        )

    async def _kenh_rpc(self) -> Any:
        """Kênh riêng vừa nghe hàng đợi trả lời vừa gửi yêu cầu đi.

        Phải là MỘT kênh cho cả hai việc: `amq.rabbitmq.reply-to` là hàng đợi
        giả gắn liền với kênh đang nghe nó, và RabbitMQ chỉ định tuyến câu trả
        lời về đúng kênh đó. Gửi yêu cầu trên kênh khác thì `reply_to` trỏ vào
        một chỗ kênh này không nghe, và câu trả lời rơi vào hư không.

        Đổi lại ta không phải khai hàng đợi trả lời nào, không phải dọn, và
        không để lại rác trên broker sau mỗi lần gọi — đây là cơ chế có sẵn của
        RabbitMQ, cũng chính là thứ NestJS dùng.
        """
        kenh = self._rpc_channel
        if kenh is not None and not kenh.is_closed:
            return kenh

        self._ready()
        async with self._lock:
            if self._rpc_channel is not None and not self._rpc_channel.is_closed:
                return self._rpc_channel
            kenh = await self._connection.channel()
            # `ensure=False`: không khai báo thụ động: hàng đợi giả này không
            # tồn tại cho tới khi có người nghe, hỏi nó sẽ đóng luôn kênh.
            hang_doi = await kenh.get_queue(RMQ_REPLY_QUEUE, ensure=False)
            await hang_doi.consume(self._nhan_tra_loi, no_ack=True)
            self._rpc_channel = kenh
            log.debug("mq.rpc_channel_opened")
        return self._rpc_channel

    async def _nhan_tra_loi(self, message: Any) -> None:
        ma = message.correlation_id
        if not ma:
            log.warning("mq.reply_without_correlation_id")
            return
        if not self._so_cho.deliver(ma, decode(message.body)):
            # Tới sau khi người gọi đã bỏ cuộc. Không phải lỗi, nhưng thấy
            # nhiều dòng này nghĩa là `timeout` đang đặt ngắn hơn thực tế.
            log.debug("mq.reply_too_late", correlation_id=ma)

    async def send(
        self,
        pattern: Any,
        data: Any = None,
        *,
        queue: str | None = None,
        exchange: str = "",
        routing_key: str | None = None,
        exchange_type: ExchangeKind | None = None,
        headers: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Gửi một YÊU CẦU rồi **chờ trả lời** — tương đương `client.send()`.

        Trả về đúng thứ handler bên kia trả về. Bên kia ném lỗi thì ném lại
        `RpcRemoteError` kèm nguyên văn; quá `timeout` giây thì `RpcTimeoutError`.

        Dùng `amq.rabbitmq.reply-to` nên không mọc thêm hàng đợi nào trên broker,
        dù gọi bao nhiêu lần.

        Nhớ: hết giờ KHÔNG bảo đảm bên kia chưa làm gì — xem docs/rpc.md.
        """
        ex, rk = self._dia_chi(queue, exchange, routing_key)
        aio_pika = _require_aio_pika()
        kenh = await self._kenh_rpc()

        han = timeout or DEFAULT_RPC_TIMEOUT

        # Giữ chỗ TRƯỚC khi gửi: bên kia có thể trả lời xong trước khi lệnh gửi
        # của ta kịp trả về.
        ma, cho = self._so_cho.open()
        try:
            goi = request_packet(pattern, data, ma)
            message = aio_pika.Message(
                body=json.dumps(goi, ensure_ascii=False, default=str).encode(),
                content_type=CONTENT_TYPE,
                content_encoding="utf-8",
                message_id=uuid.uuid4().hex,
                timestamp=utcnow(),
                correlation_id=ma,
                reply_to=RMQ_REPLY_QUEUE,
                headers=headers or {},
                # Yêu cầu đang có người đứng chờ: quá `timeout` thì nó vô giá
                # trị, đừng để broker giữ lại rồi giao cho ai đó sau này.
                expiration=han,
            )
            if ex:
                dich_gui = await self._khai_tren_kenh(kenh, ex, exchange_type)
            else:
                dich_gui = kenh.default_exchange
            await asyncio.wait_for(
                dich_gui.publish(message, routing_key=rk),
                self._config.publish_timeout_seconds,
            )
        except BaseException:
            self._so_cho.deliver(ma, None)   # trả chỗ, đừng để sổ chờ phình
            raise

        rabbitmq_published.inc(exchange=ex, routing_key=rk)
        return await self._so_cho.wait(ma, cho, han, dich=normalize_pattern(pattern))

    async def _khai_tren_kenh(
        self, kenh: Any, name: str, kind: ExchangeKind | None
    ) -> Any:
        """Khai exchange trên ĐÚNG kênh RPC.

        Không dùng lại được sổ `_exchanges`: đối tượng exchange của aio-pika gắn
        với kênh đã khai ra nó, publish qua nó là publish trên kênh đó — tức là
        `reply_to` sẽ trỏ về một kênh khác kênh đang nghe.
        """
        aio_pika = _require_aio_pika()
        kieu = kind or self._exchange_kinds.get(name) or "topic"
        return await kenh.declare_exchange(name, aio_pika.ExchangeType(kieu), durable=True)

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "connected": self.connected,
            "url": self.url if self._config.enabled else None,
            "exchanges": sorted(self._exchanges),
        }
