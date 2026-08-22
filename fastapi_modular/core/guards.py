"""Guard — chặn request trước khi vào handler (tương đương @UseGuards của Nest).

Guard KHÔNG phải nơi chứa nghiệp vụ; nó chỉ trả lời một câu: request này có
được đi tiếp không. Muốn từ chối thì ném lỗi nghiệp vụ (UnauthorizedError /
ForbiddenError) — error_handlers sẽ dịch sang HTTP.

Gắn ở cấp controller (áp cho mọi route) hoặc cấp từng route; hai nơi cộng dồn:

    @controller(prefix="/devices", guards=[RequireHeader])
    class DeviceController:
        @delete("/{device_id}", guards=[AdminOnly])
        async def remove(self, device_id: str) -> None: ...

Guard là provider bình thường nên nhận được phụ thuộc qua __init__.

Template CHƯA có xác thực thật. Chỗ để cắm vào: viết một guard đọc header/token
rồi gọi `principal.assume(...)`, phần còn lại của hệ thống dùng ngay được.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from starlette.requests import HTTPConnection

from fastapi_modular.core.container import Scope, container, injectable
from fastapi_modular.core.exceptions import ForbiddenError, UnauthorizedError
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class Guard(Protocol):
    """Ném lỗi để chặn; trả về bình thường để cho đi tiếp.

    Tham số là `HTTPConnection` — lớp cha chung của `Request` (HTTP) và
    `WebSocket`. Nhờ vậy MỘT guard dùng được cho cả hai phía, miễn là nó chỉ
    đọc những thứ có ở cả hai: headers, query_params, cookies, client.

    Lưu ý khi viết guard cho WebSocket: trình duyệt KHÔNG cho đặt header tuỳ
    ý trên kết nối WebSocket, nên token thường đi qua query (?token=...) hoặc
    qua Sec-WebSocket-Protocol. Guard nào bắt buộc header sẽ không dùng được
    từ trình duyệt.
    """

    async def check(self, connection: HTTPConnection) -> None: ...


@injectable(scope=Scope.REQUEST)
@dataclass
class Principal:
    """Ai đang gọi request này.

    Mặc định là ẩn danh. Guard xác thực gọi `assume()` để điền vào; phần còn
    lại của ứng dụng đọc qua `current_principal()`.

    Vòng đời theo request nên không có chuyện dữ liệu của người này rò sang
    người khác — container chặn sẵn việc provider singleton giữ nó.
    """

    id: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_authenticated(self) -> bool:
        return self.id is not None

    def assume(self, *, id: str, roles: frozenset[str] | set[str] | None = None) -> None:
        self.id = id
        self.roles = frozenset(roles or ())

    def require_authenticated(self) -> None:
        if not self.is_authenticated:
            raise UnauthorizedError()

    def require_role(self, *required: str) -> None:
        self.require_authenticated()
        if not self.roles.intersection(required):
            raise ForbiddenError(
                f"Cần một trong các vai trò: {', '.join(sorted(required))}"
            )


def current_principal() -> Principal:
    """Lấy Principal của request đang chạy.

    Gọi trong thân method chứ không nhận qua __init__: service là singleton,
    còn Principal theo request — container sẽ chặn nếu bạn cố inject thẳng.
    """
    return container.resolve(Principal)


# --------------------------------------------------------------------- guard mẫu
@injectable
class RequireHeader:
    """Guard mẫu: bắt buộc có header `X-Client-Id`.

    Cố ý chọn ví dụ không phải xác thực, để bạn thấy khung guard mà không bị
    nhầm là template đã có bảo mật. Guard xác thực thật viết cùng khuôn: đọc
    request, quyết định, ném lỗi hoặc gọi `principal.assume(...)`.
    """

    HEADER = "x-client-id"
    QUERY = "client_id"

    async def check(self, connection: HTTPConnection) -> None:
        # Nhận cả qua query để dùng được từ WebSocket trong trình duyệt (nơi
        # không đặt được header).
        client_id = connection.headers.get(self.HEADER) or connection.query_params.get(self.QUERY)
        if not client_id:
            raise UnauthorizedError(f"Thiếu header {self.HEADER} (hoặc ?{self.QUERY}=)")

        # Guard xác thực thật sẽ kiểm tra chữ ký/token ở đây rồi mới assume.
        current_principal().assume(id=client_id, roles={"client"})
        log.debug("guard.client_identified", client_id=client_id)
