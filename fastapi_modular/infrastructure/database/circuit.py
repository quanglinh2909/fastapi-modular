"""Circuit breaker + hạn thời gian cho database.

Vấn đề khi không có nó: database chết, mỗi request vẫn đi tới tận nơi, chờ hết
`connect_timeout` rồi mới nhận 503. Với timeout 10 giây và 100 request/giây,
toàn bộ worker bị giữ chỗ chờ vô ích, và API chết theo database.

Cách chữa: đếm số lần hỏng liên tiếp. Quá ngưỡng thì "ngắt mạch" — mọi request
trả 503 NGAY, không chạm database. Sau `reset_seconds` cho đúng MỘT request đi
thử; thành công thì đóng mạch lại, hỏng thì mở tiếp.

Lớp bọc này còn áp một hạn thời gian cứng cho mọi lời gọi. Cần thiết vì
timeout của từng driver không phủ hết mọi tình huống: database bị đóng băng
giữa lúc đang trả lời thì connection vẫn "mở", `connect_timeout` không cứu
được, và request treo cho tới khi client bỏ cuộc. `asyncio.wait_for` ở đây chặn
được mọi trường hợp, bất kể driver nào bên dưới.

Ba trạng thái:

    closed  --(hỏng liên tiếp >= ngưỡng)-->  open
    open    --(hết reset_seconds)--------->  half_open
    half_open --(1 request thành công)---->  closed
              --(1 request hỏng)----------->  open
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, TypeVar

from fastapi_modular.core.compat import StrEnum, TimeoutErrors
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    Filters,
    Match,
    is_transient_error,
)

log = get_logger(__name__)

E = TypeVar("E")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Mạch đang ngắt — không thử database nữa cho tới khi hết thời gian nghỉ."""

    def __init__(self, backend: str, retry_after: float) -> None:
        self.backend = backend
        self.retry_after = retry_after
        super().__init__(
            f"Database '{backend}' đang bị ngắt mạch, thử lại sau {retry_after:.0f}s"
        )


class CircuitBreakerBackend(DatabaseBackend):
    """Bọc quanh một backend thật, đếm hỏng và ngắt mạch khi cần.

    Chỉ tính lỗi KẾT NỐI (`is_transient_error`). Lỗi nghiệp vụ như trùng khoá
    không được làm mạch ngắt — đó là database đang hoạt động tốt.
    """

    def __init__(
        self,
        inner: DatabaseBackend,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 10.0,
        call_timeout_seconds: float = 15.0,
        breaker_enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._call_timeout = call_timeout_seconds
        # Tắt breaker chỉ tắt phần ngắt mạch; HẠN THỜI GIAN vẫn áp dụng, vì
        # thiếu nó thì một database bị treo sẽ giữ chỗ mọi worker vô thời hạn.
        self._breaker_enabled = breaker_enabled

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    # ------------------------------------------------------------------ trạng thái
    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._breaker_enabled,
            "state": self._state.value,
            "failures": self._failures,
            "threshold": self._threshold,
            "call_timeout": self._call_timeout,
        }

    def _before_call(self) -> None:
        if not self._breaker_enabled or self._state is CircuitState.CLOSED:
            return

        waited = time.monotonic() - self._opened_at
        if self._state is CircuitState.OPEN:
            if waited < self._reset_seconds:
                raise CircuitOpenError(self.name, self._reset_seconds - waited)
            self._state = CircuitState.HALF_OPEN
            log.warning("db.circuit_half_open", backend=self.name)

    def _on_success(self) -> None:
        if self._state is not CircuitState.CLOSED:
            log.info("db.circuit_closed", backend=self.name)
        self._state = CircuitState.CLOSED
        self._failures = 0

    def _on_failure(self, exc: BaseException) -> None:
        if not self._breaker_enabled or not is_transient_error(exc):
            return  # lỗi nghiệp vụ: database vẫn khoẻ, không tính

        self._failures += 1
        if self._failures >= self._threshold or self._state is CircuitState.HALF_OPEN:
            if self._state is not CircuitState.OPEN:
                log.error(
                    "db.circuit_open",
                    backend=self.name,
                    failures=self._failures,
                    reset_in=self._reset_seconds,
                )
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._before_call()
        try:
            result = await asyncio.wait_for(
                getattr(self._inner, method)(*args, **kwargs), self._call_timeout
            )
        except TimeoutErrors as exc:
            # TimeoutError là lỗi tạm thời, nên nó tính vào số lần hỏng và sẽ
            # làm mạch ngắt nếu lặp lại — đúng ý đồ.
            log.warning("db.call_timeout", backend=self.name, method=method,
                        timeout=self._call_timeout)
            self._on_failure(exc)
            # Ném lại bằng TimeoutError DỰNG SẴN, không phải asyncio.TimeoutError:
            # trên 3.10 hai lớp đó khác nhau, nên `except TimeoutError` ở code
            # người dùng sẽ trượt trên 3.10 và trúng trên 3.11. Lỗi phụ thuộc
            # phiên bản là thứ tệ nhất một thư viện có thể để lọt ra ngoài.
            raise TimeoutError(
                f"{self.name}.{method} quá {self._call_timeout}s"
            ) from exc
        except Exception as exc:
            self._on_failure(exc)
            raise
        self._on_success()
        return result

    # ------------------------------------------------------------------ vòng đời
    async def startup(self) -> None:
        await self._inner.startup()

    async def shutdown(self) -> None:
        await self._inner.shutdown()

    async def ping(self) -> bool:
        return await self._call("ping")

    def __getattr__(self, item: str) -> Any:
        # create_schema và các method riêng của backend cụ thể đi thẳng.
        return getattr(self._inner, item)

    # ------------------------------------------------------------------ truy vấn
    async def get(self, entity: type[E], id_: str) -> E | None:
        return await self._call("get", entity, id_)

    async def find(self, entity: type[E], **kwargs: Any) -> list[E]:
        return await self._call("find", entity, **kwargs)

    async def find_one(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> E | None:
        return await self._call("find_one", entity, filters=filters, match=match)

    async def count(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        return await self._call("count", entity, filters=filters, match=match)

    async def save(self, entity: type[E], obj: E) -> E:
        return await self._call("save", entity, obj)

    async def delete(self, entity: type[E], id_: str) -> bool:
        return await self._call("delete", entity, id_)

    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        return await self._call("delete_where", entity, filters=filters, match=match)
