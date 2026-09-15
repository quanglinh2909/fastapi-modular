"""Sinh khung một module nghiệp vụ mới.

    fam module alerts                    controller + service + dto + entity (CRUD)
    fam module alerts --entity Alert     đè tên entity khi đoán sai
    fam module devices --mqtt            CHỈ file MQTT: tạo module nếu chưa có, thêm vào nếu đã có
    fam module devices --redis --kafka   ghép nhiều cờ trong một lệnh

Cờ hạ tầng: --gateway (WebSocket) · --rabbitmq · --redis · --mqtt · --kafka.
Mỗi cờ sinh đúng phần của nó và KHÔNG kèm CRUD — muốn cả hai thì chạy
`fam module x` trước, `fam module x --mqtt` sau. Một khuôn cho cả tạo mới lẫn
thêm vào, nên không còn cặp cờ `--x` / `--x-only` để nhớ nhầm.

Tạo đúng cấu trúc của các module có sẵn — router / service / dto / entities —
với đầy đủ dây nối DI, decorator route và DTO tương ứng. Thân hàm để trống
kèm TODO, gọi vào sẽ trả 501 chứ không phải 500, để phân biệt "chưa viết" với
"có bug".
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path

DEFAULT_ROOT = Path("src/api")


# Đuôi kết thúc bằng "s" nhưng KHÔNG phải số nhiều: status, class, analysis...
_NOT_PLURAL = ("ss", "us", "is", "as", "os")


def singular(plural: str) -> str:
    """Đoán dạng số ít từ tên thư mục. Đoán sai thì truyền entity= để đè."""
    if plural.endswith("ies"):
        return plural[:-3] + "y"
    if plural.endswith(("ses", "xes", "zes", "ches", "shes")):
        return plural[:-2]
    if plural.endswith("s") and not plural.endswith(_NOT_PLURAL):
        return plural[:-1]
    return plural


def pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def render(module: str, entity: str) -> dict[str, str]:
    """Trả về {đường dẫn tương đối: nội dung}."""
    cls = pascal(entity)          # Alert
    var = entity                  # alert
    files: dict[str, str] = {}

    files["__init__.py"] = f'"""Module {cls}."""\n'
    files["entities/__init__.py"] = ""
    files["dto/__init__.py"] = ""

    files[f"entities/{var}_model.py"] = f'''"""Entity của module {cls} — biểu diễn nội bộ, không trả thẳng ra HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fastapi_modular import Entity, column, entity
from fastapi_modular.core.clock import utcnow


@entity(
    # TODO: khai báo ràng buộc duy nhất và index cho các trường hay lọc.
    #   unique=["ma_dinh_danh", ("owner_id", "name")]
    #   indexes=[("owner_id", "created_at"), "status"]
    # Ràng buộc duy nhất PHẢI khai ở đây; kiểm tra trong service là một cuộc đua.
)
@dataclass(slots=True)
class {cls}(Entity):
    # Kế thừa `Entity` để lọc bằng toán tử thường: `.where({cls}.name == "x")`.
    # Nó không thêm method nào và không làm đối tượng nặng thêm.
    #
    # `id: str` -> khung sinh UUID. Muốn số tự tăng 1, 2, 3 thì đổi thành
    # `id: int = 0` và sửa `{var}_id: str` thành `int` ở controller + service
    # (xem docs/entity.md).
    id: str

    # TODO: thêm các trường của bạn ở đây. Trường có giá trị mặc định thì bản
    # ghi cũ vẫn đọc được sau khi thêm cột (xem docs/database.md).
    #
    # `column(length=100)` khớp với `max_length=100` bên DTO: DTO chặn ở cửa
    # vào, cột chặn dưới database. Trường có thể rất dài thì dùng
    # `column(text=True)` (xem docs/entity.md).
    name: str = field(default="", metadata=column(length=100))

    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
'''

    files[f"dto/{var}_dto.py"] = f'''"""DTO vào/ra của module {cls}."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from fastapi_modular.core.schemas import InputSchema, OutputSchema, partial_of


class {cls}Base(InputSchema):
    """Trường client được phép ghi, khai báo đúng MỘT lần."""

    # TODO: thêm trường tương ứng với entity.
    name: str = Field(min_length=1, max_length=100)


class {cls}Create({cls}Base):
    """POST: mọi trường trong {cls}Base đều bắt buộc.

    Trường chỉ đặt được lúc tạo (bất biến về sau) thì khai ở ĐÂY, không phải ở
    {cls}Base — như vậy PATCH sẽ tự động từ chối chúng.
    """


class {cls}Update(partial_of({cls}Base)):
    """PATCH: mọi trường của {cls}Base thành optional, ràng buộc giữ nguyên.

    Trường chỉ sửa được chứ không đặt lúc tạo thì thêm ở đây.
    """


class {cls}Out(OutputSchema):
    """Cố ý liệt kê tường minh: entity về sau có thể thêm trường nội bộ mà
    không được lộ ra API."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime
'''

    files[f"{var}_service.py"] = f'''"""Nghiệp vụ của module {cls} (@Service).

Chỉ tầng này chứa business rule. Ném lỗi nghiệp vụ (NotFoundError/ConflictError)
chứ không biết gì về HTTP status code.

Các gợi ý bên dưới có dùng `NotFoundError` — bỏ chú thích thì thêm dòng import:

    from fastapi_modular import NotFoundError

(chưa import sẵn vì file mới sinh chưa dùng tới, và `fam lint` sẽ báo import thừa)
"""

from __future__ import annotations

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.database import Repository
from src.api.{module}.dto.{var}_dto import {cls}Create, {cls}Update
from src.api.{module}.entities.{var}_model import {cls}

log = get_logger(__name__)


@injectable
class {cls}Service:
    def __init__(self, repo: Repository[{cls}]) -> None:
        self._repo = repo

    async def list_{module}(self, *, limit: int, offset: int) -> tuple[list[{cls}], int]:
        # TODO: viết thân hàm. Gợi ý:
        #   return (
        #       await self._repo.find(limit=limit, offset=offset),
        #       await self._repo.count(),
        #   )
        raise NotImplementedError("{cls}Service.list_{module} chưa được viết")

    async def get_{var}(self, {var}_id: str) -> {cls}:
        # TODO: gợi ý:
        #   item = await self._repo.get({var}_id)
        #   if item is None:
        #       raise NotFoundError(f"Không tìm thấy {var} {{{var}_id}}")
        #   return item
        raise NotImplementedError("{cls}Service.get_{var} chưa được viết")

    async def create_{var}(self, payload: {cls}Create) -> {cls}:
        # TODO: gợi ý:
        #   return await self._repo.save({cls}(id="", **payload.model_dump()))
        raise NotImplementedError("{cls}Service.create_{var} chưa được viết")

    async def update_{var}(self, {var}_id: str, payload: {cls}Update) -> {cls}:
        # TODO: gợi ý — `update` nhận thẳng DTO, chỉ ghi field client gửi lên,
        # và trả về chính bản ghi đã sửa (một lượt đi database):
        #   item = await self._repo.update({var}_id, payload)
        #   if item is None:
        #       raise NotFoundError(f"Không tìm thấy {var} {{{var}_id}}")
        #   return item
        #
        # Cần đọc bản ghi cũ trước khi ghi (kiểm tra trùng, so giá trị cũ) thì
        # dùng `apply_changes(item, payload)` rồi `save(item)` — xem
        # src/api/users/user_service.py.
        raise NotImplementedError("{cls}Service.update_{var} chưa được viết")

    async def delete_{var}(self, {var}_id: str) -> None:
        # TODO: gợi ý:
        #   if not await self._repo.delete({var}_id):
        #       raise NotFoundError(f"Không tìm thấy {var} {{{var}_id}}")
        raise NotImplementedError("{cls}Service.delete_{var} chưa được viết")
'''

    files[f"{var}_controller.py"] = f'''"""HTTP layer của module {cls} (@Controller).

Controller chỉ khai báo đường dẫn, validate qua schema, và đổi entity thành DTO.
Không có business rule ở đây.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query, status

from fastapi_modular.core.controller import controller, delete, get, patch, post
from fastapi_modular.core.schemas import Page
from src.api.{module}.{var}_service import {cls}Service
from src.api.{module}.dto.{var}_dto import {cls}Create, {cls}Out, {cls}Update

{cls}Id = Annotated[str, Path(description="ID của {var}")]


@controller(prefix="/{module}", tags=["{module}"])
class {cls}Controller:
    def __init__(self, service: {cls}Service) -> None:
        self._service = service

    @get("", response_model=Page[{cls}Out], summary="Danh sách {module}")
    async def list_{module}(
        self,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Page[{cls}Out]:
        items, total = await self._service.list_{module}(limit=limit, offset=offset)
        return Page(
            items=[{cls}Out.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @get("/{{{var}_id}}", response_model={cls}Out, summary="Chi tiết {var}")
    async def get_{var}(self, {var}_id: {cls}Id) -> {cls}Out:
        return {cls}Out.model_validate(await self._service.get_{var}({var}_id))

    @post(
        "",
        response_model={cls}Out,
        status_code=status.HTTP_201_CREATED,
        summary="Tạo {var}",
    )
    async def create_{var}(self, payload: {cls}Create) -> {cls}Out:
        return {cls}Out.model_validate(await self._service.create_{var}(payload))

    @patch("/{{{var}_id}}", response_model={cls}Out, summary="Cập nhật {var}")
    async def update_{var}(self, {var}_id: {cls}Id, payload: {cls}Update) -> {cls}Out:
        return {cls}Out.model_validate(
            await self._service.update_{var}({var}_id, payload)
        )

    @delete(
        "/{{{var}_id}}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Xoá {var}",
    )
    async def delete_{var}(self, {var}_id: {cls}Id) -> None:
        await self._service.delete_{var}({var}_id)
'''
    return files


def render_gateway(module: str, entity: str) -> dict[str, str]:
    """Khung gateway WebSocket cho một module. Trả về {đường dẫn tương đối: nội dung}."""
    cls = pascal(entity)
    var = entity
    files: dict[str, str] = {}

    files[f"dto/{var}_ws_dto.py"] = f'''"""DTO cho các sự kiện WebSocket của module {cls}.

Payload WebSocket được validate bằng chính pydantic như body HTTP: sai thì
client nhận khung `error` mang code `validation_error`, không phải 500.
"""

from __future__ import annotations

from pydantic import Field

from fastapi_modular.core.schemas import InputSchema


class {cls}Event(InputSchema):
    """Dữ liệu client gửi lên kèm sự kiện."""

    # TODO: thêm trường của bạn.
    room: str = Field(min_length=1, max_length=128)
'''

    files[f"{var}_gateway.py"] = f'''"""Gateway WebSocket của module {cls} (@WebSocketGateway).

Kết nối:  ws://localhost:8000/ws/{module}?client_id=an

Không phải đăng ký ở đâu cả — app/app.py tự quét và gắn. Xem
docs/websocket.md để biết khuôn tin nhắn, cách gửi cho phòng / cho một người,
và ví dụ client Postman + Next.js.
"""

from __future__ import annotations

from typing import Any

from fastapi_modular.core.guards import RequireHeader
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.websocket import Socket, WebSocketServer, gateway, subscribe
from src.api.{module}.dto.{var}_ws_dto import {cls}Event

log = get_logger(__name__)


@gateway(
    path="/ws/{module}",
    guards=[RequireHeader],   # TODO: đổi sang guard xác thực thật của bạn
    # client_rooms=True thì client tự gửi được room.join/room.leave. Bật thì
    # NHỚ viết can_join() bên dưới, nếu không ai cũng vào được phòng của người khác.
    client_rooms=False,
)
class {cls}Gateway:
    def __init__(self, server: WebSocketServer) -> None:
        # Dùng để đẩy tin: server.to_room(...) / to_user(...) / to_socket(...)
        self._server = server

    # ------------------------------------------------------------ vòng đời
    async def on_connect(self, socket: Socket) -> None:
        """Chạy sau khi guard cho qua, trước khi client nhận khung `connected`."""
        # TODO: gợi ý — cho mỗi người một phòng riêng để gửi thông báo cá nhân:
        #   if socket.user_id:
        #       socket.join(f"user:{{socket.user_id}}")
        log.info("{module}.connected", socket_id=socket.id, user_id=socket.user_id)

    async def on_disconnect(self, socket: Socket, code: int) -> None:
        """Chạy khi kết nối đứt, dù vì lý do gì. Sổ phòng đã tự dọn."""
        log.info("{module}.disconnected", socket_id=socket.id, code=code)

    # def can_join(self, socket: Socket, room: str) -> bool:
    #     """Chốt chặn cho room.join do client gửi lên (cần client_rooms=True)."""
    #     return room == f"user:{{socket.user_id}}"

    # -------------------------------------------------------------- sự kiện
    @subscribe("{var}.subscribe")
    async def subscribe_{var}(self, socket: Socket, payload: {cls}Event) -> dict[str, Any]:
        """Giá trị trả về được gửi lại làm ack khi client có kèm `id`."""
        # TODO: gợi ý:
        #   socket.join(payload.room)
        #   return {{"room": payload.room, "size": socket.namespace.room_size(payload.room)}}
        raise NotImplementedError("{cls}Gateway.subscribe_{var} chưa được viết")

    @subscribe("{var}.ping")
    async def ping_{var}(self, socket: Socket) -> dict[str, Any]:
        """Handler không cần payload thì bỏ luôn tham số thứ hai."""
        # TODO: gợi ý:
        #   return {{"socket_id": socket.id, "rooms": sorted(socket.rooms)}}
        raise NotImplementedError("{cls}Gateway.ping_{var} chưa được viết")
'''
    return files


def _connection_hooks(prefix: str, module: str, label: str) -> str:
    """Cặp handler nối/đứt, giống nhau ở cả bốn hạ tầng — chỉ khác tiền tố."""
    return f'''
    # ------------------------------------------------------------ nối / đứt
    @{prefix}_on_connect
    async def online(self, info: dict[str, Any]) -> None:
        """Nối được {label} — lần đầu (`reconnect=False`) và mỗi lần nối lại."""
        log.info("{module}.{prefix}_online", reconnect=info["reconnect"])

    @{prefix}_on_disconnect
    async def offline(self, info: dict[str, Any]) -> None:
        """Đang nối thì đứt. Tắt app KHÔNG gọi hàm này."""
        log.warning("{module}.{prefix}_offline", error=info["error"])
'''


def render_rabbitmq(module: str, entity: str) -> dict[str, str]:
    """Khung RabbitMQ cho một module: đăng tin + consumer nền + nối/đứt."""
    cls = pascal(entity)
    var = entity

    return {
        f"{var}_rabbitmq.py": f'''"""RabbitMQ của module {cls}: đăng sự kiện, xử lý nền, biết khi nào mất kết nối.

Hàng đợi BỀN và có TÊN, nên nhiều worker chia nhau xử lý — mỗi tin đúng một
worker làm. Dùng cho việc phải làm ĐÚNG MỘT LẦN: gửi mail, ghi sổ, gọi dịch vụ
ngoài. Cần đẩy tin cho MỌI worker (ví dụ xuống WebSocket) thì dùng cầu nối
`event.subscribe`, không phải chỗ này — xem docs/rabbitmq.md.

RabbitMQ tắt (mặc định) thì file này nằm im, không tạo hàng đợi nào. Bật:
`fam install rabbitmq`.
"""

from __future__ import annotations

from typing import Any

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.rabbitmq import (
    RabbitBroker,
    rabbitmq_on_connect,
    rabbitmq_on_disconnect,
    rabbitmq_subscriber,
)

log = get_logger(__name__)


@injectable
class {cls}Rabbitmq:
    def __init__(self, mq: RabbitBroker) -> None:
        # Tiêm, đừng tự dựng RabbitBroker(settings): bản tự dựng chưa bao giờ được mở.
        self._mq = mq

    # ---------------------------------------------------------------- gửi
    async def publish(self, event: str, payload: dict[str, Any]) -> bool:
        """Đăng `{var}.<event>` lên exchange `events`. Broker đứt thì ném ServiceUnavailableError."""
        return await self._mq.publish("events", f"{var}.{{event}}", payload)

    def is_online(self) -> bool:
        """Đọc RAM, gọi liên tục cũng được."""
        return self._mq.connected

    # ---------------------------------------------------------------- nhận
    # Mọi chính sách của consumer khai ngay ở đây, không phải trong .env:
    #   max_retries=5, retry_delay=60, dead_letter=False, durable=False, prefetch=200
    # Mặc định: đúng MỘT hàng đợi. Thêm max_retries=3, dead_letter=True nếu tin
    # này đáng tiền (đơn hàng, thanh toán) — khi đó mới có <queue>.retry/.dlq.
    @rabbitmq_subscriber("events", "{var}.#", queue="{module}-worker")
    async def handle_{var}(self, payload: dict, meta: dict[str, Any]) -> None:
        """Nhận mọi sự kiện `{var}.*` trên exchange `events`.

        Tham số `meta` là tuỳ chọn: bỏ đi nếu không cần routing key thật, số
        lần đã thử, hay message id.

        Ném lỗi thường  -> thử lại tối đa `max_retries` lần rồi vào hàng đợi
                           chết `{module}-worker.dlq` (khi đã bật dead_letter).
        Ném PermanentMessageError -> vào thẳng hàng đợi chết, không thử lại.
        """
        # TODO: gợi ý:
        #   log.info("{var}.received", routing_key=meta["routing_key"])
        raise NotImplementedError("{cls}Rabbitmq.handle_{var} chưa được viết")
''' + _connection_hooks("rabbitmq", module, "RabbitMQ"),
    }


def render_redis(module: str, entity: str) -> dict[str, str]:
    """Khung Redis cho một module: cache + phát tin + nghe kênh + nối/đứt."""
    cls = pascal(entity)
    var = entity

    return {
        f"{var}_redis.py": f'''"""Redis của module {cls}: cache, phát tin tới mọi worker, biết khi nào mất kết nối.

Pub/sub của Redis KHÔNG LƯU GÌ: tin phát ra lúc không ai nghe là mất luôn. Tin
không được phép mất thì dùng RabbitMQ hoặc Kafka — xem docs/redis.md.

Redis tắt (mặc định) thì file này nằm im. Bật: `fam install redis`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.redis import (
    RedisClient,
    redis_on_connect,
    redis_on_disconnect,
    redis_subscriber,
)

log = get_logger(__name__)


@injectable
class {cls}Redis:
    def __init__(self, redis: RedisClient) -> None:
        # Tiêm, đừng tự dựng RedisClient(settings): bản tự dựng chưa bao giờ được mở.
        self._redis = redis

    # ---------------------------------------------------------------- cache
    async def cached(
        self, key: str, compute: Callable[[], Awaitable[Any]], *, ttl: float = 30
    ) -> Any:
        """Đọc cache `{module}:<key>`; trượt thì gọi `compute()` rồi ghi lại.

        Redis chết thì gọi thẳng `compute()` — request vẫn xong, chỉ chậm hơn.
        """
        return await self._redis.cached(f"{module}:{{key}}", compute, ttl=ttl)

    # ---------------------------------------------------------------- phát tin
    async def broadcast(self, payload: dict[str, Any]) -> int:
        """Phát lên kênh `{module}.changed`. Trả về số người nghe — 0 là tin mất luôn."""
        return await self._redis.publish("{module}.changed", payload)

    def is_online(self) -> bool:
        """Đọc RAM, gọi liên tục cũng được."""
        return self._redis.connected

    # ---------------------------------------------------------------- nghe
    @redis_subscriber("{module}.*")
    async def on_message(self, payload: Any, meta: dict[str, Any]) -> None:
        """Nhận mọi kênh `{module}.*`. MỌI worker đều nhận một bản sao."""
        # TODO: gợi ý:
        #   log.info("{var}.message", channel=meta["channel"], payload=payload)
        raise NotImplementedError("{cls}Redis.on_message chưa được viết")
''' + _connection_hooks("redis", module, "Redis"),
    }


def render_mqtt(module: str, entity: str) -> dict[str, str]:
    """Khung MQTT cho một module: gửi lệnh + nghe trạng thái + nối/đứt."""
    cls = pascal(entity)
    var = entity

    return {
        f"{var}_mqtt.py": f'''"""MQTT của module {cls}: gửi lệnh xuống thiết bị, nghe trạng thái, biết khi nào mất kết nối.

MQTT KHÔNG có hàng đợi thử lại hay DLQ: handler ném lỗi thì tin đó bỏ qua. Cần
chắc không mất việc thì trong handler đẩy tiếp sang RabbitMQ — xem docs/mqtt.md.

MQTT tắt (mặc định) thì file này nằm im. Bật: `fam install mqtt`.
"""

from __future__ import annotations

from typing import Any

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.mqtt import (
    MqttClient,
    mqtt_on_connect,
    mqtt_on_disconnect,
    mqtt_subscriber,
)

log = get_logger(__name__)


@injectable
class {cls}Mqtt:
    def __init__(self, mqtt: MqttClient) -> None:
        # Tiêm, đừng tự dựng MqttClient(settings): bản tự dựng chưa bao giờ được mở.
        self._mqtt = mqtt

    # ---------------------------------------------------------------- gửi
    async def send(self, {var}_id: str, payload: dict[str, Any]) -> bool:
        """Gửi xuống `{module}/<id>/cmd`. Broker đứt thì ném ServiceUnavailableError."""
        return await self._mqtt.publish(f"{module}/{{{var}_id}}/cmd", payload, qos=1)

    def is_online(self) -> bool:
        """Đọc RAM, gọi liên tục cũng được."""
        return self._mqtt.connected

    # ---------------------------------------------------------------- nghe
    @mqtt_subscriber("{module}/+/status", qos=1)
    async def on_status(self, payload: Any, meta: dict[str, Any]) -> None:
        """Nhận mọi `{module}/<id>/status`. QoS 1 có thể giao TRÙNG — handler phải chịu được."""
        # TODO: gợi ý:
        #   {var}_id = meta["topic"].split("/")[1]
        #   log.info("{var}.status", {var}_id={var}_id, payload=payload)
        raise NotImplementedError("{cls}Mqtt.on_status chưa được viết")
''' + _connection_hooks("mqtt", module, "broker MQTT"),
    }


def render_kafka(module: str, entity: str) -> dict[str, str]:
    """Khung Kafka cho một module: ghi sự kiện + đọc nhật ký + nối/đứt."""
    cls = pascal(entity)
    var = entity

    return {
        f"{var}_kafka.py": f'''"""Kafka của module {cls}: ghi sự kiện vào nhật ký, đọc lại, biết khi nào mất kết nối.

Tin KHÔNG mất sau khi xử lý: nó nằm lại theo thời gian giữ của topic, mỗi nhóm
consumer một con trỏ đọc riêng. Thử lại một tin làm ĐỨNG cả phân vùng — xem
docs/kafka.md trước khi bật `max_retries`.

Kafka tắt (mặc định) thì file này nằm im. Bật: `fam install kafka`.
"""

from __future__ import annotations

from typing import Any

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.kafka import (
    KafkaBroker,
    kafka_on_connect,
    kafka_on_disconnect,
    kafka_subscriber,
)

log = get_logger(__name__)


@injectable
class {cls}Kafka:
    def __init__(self, kafka: KafkaBroker) -> None:
        # Tiêm, đừng tự dựng KafkaBroker(settings): bản tự dựng chưa bao giờ được mở.
        self._kafka = kafka

    # ---------------------------------------------------------------- gửi
    async def publish(self, {var}_id: str, payload: dict[str, Any]) -> bool:
        """Ghi vào topic `{module}`, chờ cụm xác nhận.

        `key={var}_id`: mọi tin của cùng một {var} rơi vào cùng phân vùng, nên
        được xử lý ĐÚNG THỨ TỰ.
        """
        return await self._kafka.publish("{module}", payload, key={var}_id)

    def is_online(self) -> bool:
        """Đọc RAM, gọi liên tục cũng được."""
        return self._kafka.connected

    # ---------------------------------------------------------------- đọc
    # Chính sách khai ngay ở decorator: max_retries=3, retry_delay=0.5,
    # dead_letter=True (-> topic `{module}.dlt`). Mặc định KHÔNG thử lại và
    # KHÔNG giữ tin lỗi: chưa viết thân hàm mà đã bật Kafka thì tin tới bị BỎ.
    @kafka_subscriber("{module}", group="{module}-worker")
    async def on_event(self, payload: Any, meta: dict[str, Any]) -> None:
        """Đọc topic `{module}` dưới nhóm `{module}-worker` — các worker cùng nhóm chia nhau phân vùng."""
        # TODO: gợi ý:
        #   log.info("{var}.event", key=meta["key"], offset=meta["offset"], payload=payload)
        raise NotImplementedError("{cls}Kafka.on_event chưa được viết")
''' + _connection_hooks("kafka", module, "cụm Kafka"),
    }


#: Cờ hạ tầng -> (tên để in, hàm sinh, việc tiếp theo). Thứ tự ở đây là thứ tự
#: ghi file và in hướng dẫn.
INFRA: dict[str, tuple[str, Callable[[str, str], dict[str, str]], Callable[[str, str], list[str]]]] = {
    "gateway": (
        "gateway WebSocket",
        render_gateway,
        lambda module, entity: [
            f"Viết thân các handler trong {entity}_gateway.py",
            f"fam dev, rồi nối thử: ws://localhost:8000/ws/{module}?client_id=an",
            "Xem docs/websocket.md (có ví dụ Postman và Next.js)",
        ],
    ),
    "rabbitmq": (
        "RabbitMQ",
        render_rabbitmq,
        lambda module, entity: [
            f"Viết thân handler trong {entity}_rabbitmq.py",
            "fam install rabbitmq  (cài aio-pika, bật APP_RABBITMQ__ENABLED)",
            "Xem docs/rabbitmq.md",
        ],
    ),
    "redis": (
        "Redis",
        render_redis,
        lambda module, entity: [
            f"Viết thân handler trong {entity}_redis.py",
            "fam install redis  (cài redis, bật APP_REDIS__ENABLED)",
            "Xem docs/redis.md",
        ],
    ),
    "mqtt": (
        "MQTT",
        render_mqtt,
        lambda module, entity: [
            f"Viết thân handler trong {entity}_mqtt.py",
            "fam install mqtt  (cài aiomqtt, bật APP_MQTT__ENABLED)",
            "Xem docs/mqtt.md",
        ],
    ),
    "kafka": (
        "Kafka",
        render_kafka,
        lambda module, entity: [
            f"Viết thân handler trong {entity}_kafka.py",
            "fam install kafka  (cài aiokafka, bật APP_KAFKA__ENABLED)",
            "Xem docs/kafka.md",
        ],
    ),
}

#: Cờ đã bỏ -> cờ thay thế. Giữ lại chỉ để báo lỗi có hướng dẫn: argparse trần
#: sẽ in "unrecognized arguments" và người ta không biết phải gõ gì.
REMOVED_FLAGS = {
    "gateway_only": ("--gateway-only", "--gateway"),
    "consumer": ("--consumer", "--rabbitmq"),
    "consumer_only": ("--consumer-only", "--rabbitmq"),
}


def _write(target: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def add_infra_arguments(parser: argparse.ArgumentParser) -> None:
    """Khai cờ hạ tầng — dùng chung cho `fam module` và `python -m ...new_module`."""
    parser.add_argument("--gateway", action="store_true", help="gateway WebSocket")
    parser.add_argument("--rabbitmq", action="store_true", help="đăng tin + consumer RabbitMQ")
    parser.add_argument("--redis", action="store_true", help="cache + pub/sub Redis")
    parser.add_argument("--mqtt", action="store_true", help="gửi lệnh + nghe topic MQTT")
    parser.add_argument("--kafka", action="store_true", help="ghi + đọc nhật ký Kafka")
    for dest, (old, _new) in REMOVED_FLAGS.items():
        parser.add_argument(old, dest=dest, action="store_true", help=argparse.SUPPRESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sinh khung module nghiệp vụ")
    parser.add_argument("name", help="tên module, dạng số nhiều viết thường: alerts")
    parser.add_argument("--entity", help="tên entity dạng số ít; mặc định đoán từ name")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    add_infra_arguments(parser)
    args = parser.parse_args(argv)

    for dest, (old, new) in REMOVED_FLAGS.items():
        if getattr(args, dest):
            print(f"Cờ {old} đã bỏ. Dùng: fam module {args.name} {new}")
            print("Cờ hạ tầng giờ tự tạo module nếu chưa có, và thêm vào nếu đã có.")
            return 1

    module = args.name.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", module):
        print(f"Tên module không hợp lệ: {args.name!r}. Chỉ dùng chữ thường, số và _.")
        return 1

    entity = (args.entity or singular(module)).strip().lower().replace("-", "_")
    target = args.root / module
    chosen = [flag for flag in INFRA if getattr(args, flag)]

    if not chosen:
        return _create_crud(target, module, entity)
    return _add_infra(target, module, entity, chosen)


def _create_crud(target: Path, module: str, entity: str) -> int:
    if target.exists():
        print(f"Đã có {target} rồi. Xoá đi hoặc chọn tên khác.")
        return 1

    files = render(module, entity)
    _write(target, files)

    print(f"Đã tạo module '{module}' (entity {pascal(entity)}) tại {target}:")
    for relative in sorted(files):
        print(f"    {target / relative}")
    print()
    print("Việc tiếp theo:")
    print(f"  1. Thêm trường vào entities/{entity}_model.py và dto/{entity}_dto.py")
    print("  2. Khai unique/indexes trong @entity, độ dài cột chữ bằng column(length=...)")
    print(f"  3. Viết thân các hàm trong {entity}_service.py (đang raise NotImplementedError)")
    print("  4. fam dev — route đã tự xuất hiện, không phải đăng ký ở đâu cả")
    print(f"  Cần thêm hạ tầng: fam module {module} --mqtt (hoặc --gateway --rabbitmq --redis --kafka)")
    return 0


def _add_infra(target: Path, module: str, entity: str, chosen: list[str]) -> int:
    """Một khuôn cho cả tạo mới lẫn thêm vào: chưa có thì tạo, có rồi thì thêm."""
    created = not target.exists()
    files: dict[str, str] = {}
    if created:
        files["__init__.py"] = f'"""Module {pascal(entity)}."""\n'
    for flag in chosen:
        files.update(INFRA[flag][1](module, entity))
    if any(rel.startswith("dto/") for rel in files) and not (target / "dto" / "__init__.py").exists():
        files["dto/__init__.py"] = ""

    # Kiểm HẾT trước khi ghi file nào: dừng giữa chừng thì module nửa có nửa
    # không, và lần chạy lại sẽ vấp chính file vừa ghi.
    duplicate = [rel for rel in files if (target / rel).exists()]
    if duplicate:
        print(f"Đã có sẵn, không ghi đè: {', '.join(str(target / rel) for rel in duplicate)}")
        return 1
    _write(target, files)

    kinds = ", ".join(INFRA[flag][0] for flag in chosen)
    verb = "Đã tạo module" if created else "Đã thêm vào module"
    print(f"{verb} '{module}' ({kinds}) tại {target}:")
    for relative in sorted(files):
        print(f"    {target / relative}")
    print()
    print("Việc tiếp theo:")
    step = 1
    for flag in chosen:
        for line in INFRA[flag][2](module, entity):
            print(f"  {step}. {line}")
            step += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
