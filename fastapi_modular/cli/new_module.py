"""Sinh khung một module nghiệp vụ mới.

Chạy qua: fam module alerts [--entity Alert] [--gateway] [--consumer]
Thêm gateway cho module đã có:  fam module alerts --gateway-only
Thêm consumer cho module đã có: fam module alerts --consumer-only

Tạo đúng cấu trúc của các module có sẵn — router / service / dto / entities —
với đầy đủ dây nối DI, decorator route và DTO tương ứng. Thân hàm để trống
kèm TODO, gọi vào sẽ trả 501 chứ không phải 500, để phân biệt "chưa viết" với
"có bug".
"""

from __future__ import annotations

import argparse
import re
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

from fastapi_modular import Entity, entity
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
    id: str

    # TODO: thêm các trường của bạn ở đây. Trường có giá trị mặc định thì bản
    # ghi cũ vẫn đọc được sau khi thêm cột (xem docs/database.md).
    name: str = ""

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
        # TODO: gợi ý — `update` nhận thẳng DTO, chỉ ghi field client gửi lên:
        #   if not await self._repo.update({var}_id, payload):
        #       raise NotFoundError(f"Không tìm thấy {var} {{{var}_id}}")
        #   return await self.get_{var}({var}_id)
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


def _write(target: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def render_consumer(module: str, entity: str) -> dict[str, str]:
    """Khung consumer RabbitMQ cho một module."""
    cls = pascal(entity)
    var = entity

    return {
        f"{var}_consumer.py": f'''"""Consumer nền của module {cls} (@EventPattern).

Hàng đợi BỀN và có TÊN, nên nhiều worker chia nhau xử lý — mỗi tin đúng một
worker làm. Dùng cho việc phải làm ĐÚNG MỘT LẦN: gửi mail, ghi sổ, gọi dịch vụ
ngoài. Cần đẩy tin cho MỌI worker (ví dụ xuống WebSocket) thì dùng cầu nối
`event.subscribe`, không phải chỗ này — xem docs/rabbitmq.md.

RabbitMQ tắt (mặc định) thì file này nằm im, không tạo hàng đợi nào.
"""

from __future__ import annotations

from typing import Any

from fastapi_modular.core.container import injectable
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.rabbitmq import rabbitmq_subscriber
from src.api.{module}.{var}_service import {cls}Service

log = get_logger(__name__)


@injectable
class {cls}Consumer:
    def __init__(self, service: {cls}Service) -> None:
        self._service = service

    # Mọi chính sách của consumer khai ngay ở đây, không phải trong .env:
    #   max_retries=5, retry_delay=60, dead_letter=False, durable=False, prefetch=200
    # Mặc định: đúng MỘT hàng đợi. Thêm max_retries=3, dead_letter=True nếu tin
    # này đáng tiền (đơn hàng, thanh toán) — khi đó mới có <queue>.retry/.dlq.
    @rabbitmq_subscriber("events", "{var}.#", queue="{module}-worker")
    async def handle_{var}(self, payload: dict, meta: dict[str, Any]) -> None:
        """Nhận mọi sự kiện `{var}.*` trên exchange `events`.

        Tham số `meta` là tuỳ chọn: bỏ đi nếu không cần routing key thật, số
        lần đã thử, hay message id.

        Ném lỗi thường  -> thử lại tối đa `max_retries` lần (mặc định 3) rồi
                           vào hàng đợi chết `{module}-worker.dlq`.
        Ném PermanentMessageError -> vào thẳng hàng đợi chết, không thử lại.
        """
        # TODO: gợi ý:
        #   log.info("{var}.received", routing_key=meta["routing_key"])
        #   await self._service.get_{var}(payload["id"])
        raise NotImplementedError("{cls}Consumer.handle_{var} chưa được viết")
'''
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sinh khung module nghiệp vụ")
    parser.add_argument("name", help="tên module, dạng số nhiều viết thường: alerts")
    parser.add_argument("--entity", help="tên entity dạng số ít; mặc định đoán từ name")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--gateway", action="store_true", help="tạo kèm gateway WebSocket"
    )
    parser.add_argument(
        "--gateway-only",
        action="store_true",
        help="chỉ thêm gateway WebSocket vào module đã có",
    )
    parser.add_argument(
        "--consumer", action="store_true", help="tạo kèm consumer RabbitMQ"
    )
    parser.add_argument(
        "--consumer-only",
        action="store_true",
        help="chỉ thêm consumer RabbitMQ vào module đã có",
    )
    args = parser.parse_args(argv)

    module = args.name.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", module):
        print(f"Tên module không hợp lệ: {args.name!r}. Chỉ dùng chữ thường, số và _.")
        return 1

    entity = (args.entity or singular(module)).strip().lower().replace("-", "_")

    target = args.root / module

    if args.gateway_only or args.consumer_only:
        if not target.exists():
            print(f"Chưa có module {target}. Tạo trước bằng: fam module {module}")
            return 1
        files = render_gateway(module, entity) if args.gateway_only else render_consumer(module, entity)
        duplicate = [rel for rel in files if (target / rel).exists()]
        if duplicate:
            print(f"Đã có sẵn: {', '.join(str(target / rel) for rel in duplicate)}")
            return 1
        _write(target, files)
        kind = "gateway WebSocket" if args.gateway_only else "consumer RabbitMQ"
        print(f"Đã thêm {kind} vào module '{module}':")
        for relative in sorted(files):
            print(f"    {target / relative}")
        print()
        print("Việc tiếp theo:")
        if args.gateway_only:
            print(f"  1. Viết thân các handler trong {entity}_gateway.py")
            print(f"  2. fam dev, rồi nối thử: ws://localhost:8000/ws/{module}?client_id=an")
            print("  3. Xem docs/websocket.md (có ví dụ Postman và Next.js)")
        else:
            print(f"  1. Viết thân handler trong {entity}_consumer.py")
            print("  2. pip install \'fastapi-modular[rabbitmq]\' rồi APP_RABBITMQ__ENABLED=true")
            print("  3. Xem docs/rabbitmq.md")
        return 0

    if target.exists():
        print(f"Đã có {target} rồi. Xoá đi hoặc chọn tên khác.")
        return 1

    files = render(module, entity)
    if args.gateway:
        files.update(render_gateway(module, entity))
    if args.consumer:
        files.update(render_consumer(module, entity))
    _write(target, files)

    print(f"Đã tạo module '{module}' (entity {pascal(entity)}) tại {target}:")
    for relative in sorted(files):
        print(f"    {target / relative}")
    print()
    print("Việc tiếp theo:")
    print(f"  1. Thêm trường vào entities/{entity}_model.py và dto/{entity}_dto.py")
    print("  2. Khai unique/indexes trong @entity nếu cần")
    print(f"  3. Viết thân các hàm trong {entity}_service.py (đang raise NotImplementedError)")
    print("  4. fam dev — route đã tự xuất hiện, không phải đăng ký ở đâu cả")
    if args.gateway:
        print(f"  5. Viết thân handler trong {entity}_gateway.py (xem docs/websocket.md)")
    if args.consumer:
        print(f"  6. Viết thân handler trong {entity}_consumer.py (xem docs/rabbitmq.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
