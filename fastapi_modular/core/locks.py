"""Khoá "chỉ một người chạy" — để việc định kỳ không chạy nhiều lần.

Đây là lý do chính khiến `@interval` phải nằm trong khung chứ không phải tự
viết trong dự án. `fam run` mặc định bật **4 worker**, tức 4 tiến trình Python
độc lập, mỗi tiến trình nạp đủ code của bạn. Một vòng `while True: sleep(5)`
viết tay sẽ chạy **bốn lần mỗi 5 giây**: ghi log thành 4 bản, gọi API tốn 4 lần
quota, cập nhật trạng thái camera thì bốn tiến trình ghi đè nhau.

## Giành quyền rồi GIỮ, không phải khoá từng lượt

Chỗ này dễ làm sai, và tôi đã làm sai một lần: nếu chỉ khoá quanh mỗi lượt
chạy rồi nhả ngay, khoá chỉ ngăn hai tiến trình chạy **cùng một khoảnh khắc**.
Bốn worker có bốn đồng hồ riêng, nên chúng vẫn lần lượt giành được khoá và
việc vẫn chạy bốn lần mỗi nhịp — đo được 14 lượt trong 1,1 giây thay vì 5.

Cách đúng là **giành quyền một lần rồi giữ suốt**: một tiến trình thắng và
chạy vòng lặp, ba tiến trình kia đứng ngoài chờ. Tiến trình thắng chết thì
khoá nhả và một tiến trình khác lên thay.

Hai bản hiện thực, chọn tự động theo thứ tự này:

    RedisLock    khi đã bật Redis — khoá được GIỮA CÁC MÁY
    FileLock     mặc định — khoá giữa các tiến trình TRÊN CÙNG MỘT MÁY

`FileLock` dùng `flock` của hệ điều hành, và điều đó cho một tính chất mà khoá
trên Redis không có: **tiến trình chết là khoá tự nhả**, vì nhân hệ điều hành
đóng file descriptor hộ. Không có khoá kẹt, không cần đoán TTL. Đổi lại nó chỉ
biết tới một máy — chạy nhiều máy thì phải có Redis.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)

#: Khoá Redis phải có hạn, nếu không tiến trình chết là khoá kẹt vĩnh viễn.
#: Đặt theo bội số thời gian chạy dự kiến của việc, không phải theo chu kỳ lặp.
REDIS_LOCK_MARGIN = 3.0


class SingleFlight(Protocol):
    """Chỉ một tiến trình giữ được `name`, và giữ cho tới khi nhả hoặc chết."""

    async def acquire(self, name: str) -> bool: ...

    async def renew(self, name: str) -> bool:
        """Giữ tiếp. Redis cần (khoá có hạn); `flock` thì không cần làm gì."""
        ...

    async def release(self, name: str) -> None: ...

    @property
    def scope(self) -> str:
        """Mô tả tầm với của khoá, để in ra lúc khởi động."""
        ...


class NoLock:
    """Không khoá gì cả — ai gọi cũng chạy.

    Dùng khi bạn tự bảo đảm chỉ có MỘT tiến trình chạy việc định kỳ (`fam
    worker`, hoặc một replica trên k8s). Khung sẽ nói rõ ở log khởi động rằng
    nó đang không khoá, để không ai tưởng nhầm là có.
    """

    async def acquire(self, name: str) -> bool:
        return True

    async def renew(self, name: str) -> bool:
        return True

    async def release(self, name: str) -> None:
        return None

    @property
    def scope(self) -> str:
        return "không khoá"


class FileLock:
    """Khoá bằng `flock` — giữa các tiến trình trên CÙNG một máy.

    Mỗi việc một file trong thư mục tạm. Không xoá file sau khi nhả: xoá rồi
    tạo lại tạo ra một khe hở mà hai tiến trình cùng lọt qua. File rỗng vài
    byte nằm đó là cái giá rẻ hơn nhiều.
    """

    def __init__(self, directory: str | os.PathLike[str] | None = None, *, prefix: str = "fam") -> None:
        self._dir = Path(directory) if directory else Path(tempfile.gettempdir())
        self._prefix = prefix
        self._held: dict[str, Any] = {}

    def _path(self, name: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        return self._dir / f"{self._prefix}-{safe}.lock"

    async def acquire(self, name: str) -> bool:
        if name in self._held:
            return True                      # đã là chủ, giữ nguyên
        try:
            import fcntl
        except ImportError:                  # pragma: no cover - Windows
            return True

        self._dir.mkdir(parents=True, exist_ok=True)
        handle = self._path(name).open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False                     # tiến trình khác đang giữ
        self._held[name] = handle
        return True

    async def renew(self, name: str) -> bool:
        # `flock` gắn với file descriptor: còn giữ fd là còn giữ khoá, và tiến
        # trình chết thì nhân hệ điều hành đóng hộ. Không có gì để gia hạn.
        return name in self._held

    async def release(self, name: str) -> None:
        handle = self._held.pop(name, None)
        if handle is None:
            return
        with contextlib.suppress(Exception):
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        with contextlib.suppress(Exception):
            handle.close()

    @property
    def scope(self) -> str:
        return f"một máy (flock trong {self._dir})"


class RedisLock:
    """Khoá qua Redis — giữa NHIỀU máy.

    `SET key value EX n NX` là toàn bộ cơ chế, và `RedisClient.set` đã làm đúng
    việc đó sẵn.

    Khoá có HẠN, khác `FileLock`: tiến trình chết giữa chừng thì Redis không
    biết, nên phải đợi hết hạn mới có người khác chạy được. Vì vậy hạn đặt theo
    thời gian chạy dự kiến của việc chứ không theo chu kỳ lặp — đặt ngắn quá
    thì hai máy cùng chạy một việc đang dở.
    """

    def __init__(self, client: Any, *, prefix: str = "fam:lock:", ttl: float = 30.0) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl
        self._tokens: dict[str, str] = {}

    async def acquire(self, name: str) -> bool:
        if name in self._tokens:
            return True
        token = f"{os.getpid()}@{sys.argv[0]}"
        got = await self._client.set(
            f"{self._prefix}{name}", token, ttl=self._ttl, if_not_exists=True
        )
        if got:
            self._tokens[name] = token
        return bool(got)

    async def renew(self, name: str) -> bool:
        """Gia hạn khoá.

        Bắt buộc phải có, và đây là khác biệt lớn nhất với `flock`: khoá Redis
        có HẠN, nên chủ đang sống mà quên gia hạn thì máy khác giành mất giữa
        chừng. Đổi lại chính cái hạn đó là thứ dọn khoá khi chủ chết đột ngột —
        Redis không biết tiến trình nào còn sống.
        """
        token = self._tokens.get(name)
        if token is None:
            return False
        with contextlib.suppress(Exception):
            await self._client.set(f"{self._prefix}{name}", token, ttl=self._ttl)
            return True
        return False

    async def release(self, name: str) -> None:
        if self._tokens.pop(name, None) is None:
            return
        # Không kiểm token trước khi xoá: cần một lệnh Lua mới làm được nguyên
        # tử, mà cái giá của việc xoá nhầm ở đây rất nhỏ — cùng lắm là một lượt
        # chạy sớm hơn dự kiến. Việc định kỳ vốn phải lặp lại được.
        with contextlib.suppress(Exception):
            await self._client.delete(f"{self._prefix}{name}")

    @property
    def scope(self) -> str:
        return "nhiều máy (Redis)"


def build_lock(settings: Any, *, directory: str = "") -> SingleFlight:
    """Chọn khoá phù hợp với những gì dự án đang bật.

    Bật Redis thì dùng Redis (khoá được giữa các máy); không thì `flock` trên
    một máy. Muốn tắt hẳn thì `APP_SCHEDULER__SINGLE=false`.
    """
    if getattr(getattr(settings, "redis", None), "enabled", False):
        from fastapi_modular.core.container import container
        from fastapi_modular.infrastructure.redis import RedisClient

        with contextlib.suppress(Exception):
            return RedisLock(container.resolve(RedisClient))
    return FileLock(directory or None)
