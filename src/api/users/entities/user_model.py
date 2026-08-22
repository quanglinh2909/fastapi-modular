"""Entity của module User — biểu diễn nội bộ, không bao giờ trả thẳng ra HTTP.

Tách khỏi schema để đổi tầng lưu trữ (dataclass → SQLAlchemy model) mà không
kéo theo thay đổi ở hợp đồng API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.container import entity


@entity(unique=["email"])
@dataclass(slots=True)
class User:
    id: str
    email: str
    full_name: str
    is_active: bool = True
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
