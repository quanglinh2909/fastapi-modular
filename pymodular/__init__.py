"""pymodular — FastAPI theo kiến trúc module kiểu NestJS.

    from pymodular import create_app
    app = create_app()

Những thứ hay dùng nhất được xuất thẳng ở đây; phần còn lại nằm trong
`pymodular.core`, `pymodular.infrastructure`.
"""

from __future__ import annotations

from pymodular.core.config import Settings, get_settings, use_settings
from pymodular.core.container import Lazy, Scope, container, entity, injectable
from pymodular.core.controller import controller, delete, get, patch, post, put
from pymodular.core.error_handlers import register_error_handlers
from pymodular.core.exceptions import (
    AppError,
    BadRequestError,
    ComponentNotEnabledError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from pymodular.core.lifespan import lifespan
from pymodular.core.logging import configure_logging, get_logger
from pymodular.core.schemas import Page
from pymodular.core.websocket import Socket, WebSocketServer, gateway, subscribe
from pymodular.discovery import DEFAULT_PACKAGE, register_routes
from pymodular.factory import add_middleware, bind_settings, create_app, new_fastapi

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PACKAGE",
    "AppError",
    "BadRequestError",
    "ComponentNotEnabledError",
    "ConflictError",
    "ForbiddenError",
    "Lazy",
    "NotFoundError",
    "Page",
    "Scope",
    "ServiceUnavailableError",
    "Settings",
    "Socket",
    "UnauthorizedError",
    "WebSocketServer",
    "__version__",
    "add_middleware",
    "bind_settings",
    "configure_logging",
    "container",
    "controller",
    "create_app",
    "delete",
    "entity",
    "gateway",
    "get",
    "get_logger",
    "get_settings",
    "injectable",
    "lifespan",
    "new_fastapi",
    "patch",
    "post",
    "put",
    "register_error_handlers",
    "register_routes",
    "subscribe",
    "use_settings",
]
