"""Vá những khác biệt giữa các phiên bản Python được hỗ trợ (3.10+).

Gom vào một chỗ để phần còn lại của khung viết như thể chỉ có một phiên bản.
Bỏ hỗ trợ 3.10 thì xoá file này và sửa hai chỗ import — không phải lần mò khắp
nơi tìm xem cái gì cần bao nhiêu.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timezone

# `datetime.UTC` chỉ có từ 3.11; `timezone.utc` là CÙNG một đối tượng và có ở
# mọi phiên bản. (ruff sẽ đòi đổi ngược lại nếu target-version trong ruff.toml
# bị nâng lên py311 — đừng nâng khi còn hỗ trợ 3.10.)
UTC = timezone.utc

# `asyncio.wait_for` ném `asyncio.TimeoutError`: từ 3.11 nó CHÍNH LÀ
# `TimeoutError` dựng sẵn, còn 3.10 thì là một lớp khác hẳn. Bắt bằng tên này
# thì đúng ở cả hai; bắt bằng `TimeoutError` trần sẽ trượt trên 3.10.
TimeoutErrors: tuple[type[BaseException], ...] = (asyncio.TimeoutError, TimeoutError)

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        """Bản 3.10 của `enum.StrEnum`.

        `__str__` phải trỏ về `str.__str__`: mặc định của Enum trả về
        "Scope.REQUEST" chứ không phải "request", và đó là kiểu khác biệt chỉ lộ
        ra khi ai đó nội suy giá trị vào log hay JSON.
        """

        __str__ = str.__str__

__all__ = ["UTC", "StrEnum", "TimeoutErrors"]
