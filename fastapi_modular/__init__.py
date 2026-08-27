"""fastapi_modular — FastAPI theo kiến trúc module kiểu NestJS.

    from fastapi_modular import create_app
    app = create_app()

Những thứ hay dùng nhất được xuất thẳng ở đây; phần còn lại nằm trong
`fastapi_modular.core`, `fastapi_modular.infrastructure`.
"""

from __future__ import annotations

from fastapi_modular.core.config import Settings, get_settings, use_settings
from fastapi_modular.core.container import Lazy, Scope, container, entity, injectable
from fastapi_modular.core.controller import controller, delete, get, patch, post, put
from fastapi_modular.core.error_handlers import register_error_handlers
from fastapi_modular.core.events import EventBus, on_event
from fastapi_modular.core.exceptions import (
    AppError,
    BadRequestError,
    ComponentNotEnabledError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from fastapi_modular.core.jobs import JobQueue, JobRunner, job
from fastapi_modular.core.lifespan import lifespan
from fastapi_modular.core.logging import configure_logging, get_logger
from fastapi_modular.core.providers import (
    CapabilityNotSupportedError,
    ProviderNotFoundError,
    Providers,
    provider,
    register_providers,
)
from fastapi_modular.core.scheduler import SchedulerRunner, cron, interval, timeout
from fastapi_modular.core.schemas import Page
from fastapi_modular.core.websocket import Socket, WebSocketServer, gateway, subscribe
from fastapi_modular.core.workers import WorkerContext, WorkerPool, worker
from fastapi_modular.discovery import DEFAULT_PACKAGE, register_routes
from fastapi_modular.factory import add_middleware, bind_settings, create_app, new_fastapi
from fastapi_modular.infrastructure.database.base import Entity, reference

__version__ = "0.3.1"

__all__ = [
    "DEFAULT_PACKAGE",
    "AppError",
    "BadRequestError",
    "CapabilityNotSupportedError",
    "ComponentNotEnabledError",
    "ConflictError",
    "Entity",
    "EventBus",
    "ForbiddenError",
    "JobQueue",
    "JobRunner",
    "Lazy",
    "NotFoundError",
    "Page",
    "ProviderNotFoundError",
    "Providers",
    "SchedulerRunner",
    "Scope",
    "ServiceUnavailableError",
    "Settings",
    "Socket",
    "UnauthorizedError",
    "WebSocketServer",
    "WorkerContext",
    "WorkerPool",
    "__version__",
    "add_middleware",
    "bind_settings",
    "configure_logging",
    "container",
    "controller",
    "create_app",
    "cron",
    "delete",
    "entity",
    "gateway",
    "get",
    "get_logger",
    "get_settings",
    "injectable",
    "interval",
    "job",
    "lifespan",
    "new_fastapi",
    "on_event",
    "patch",
    "post",
    "provider",
    "put",
    "reference",
    "register_error_handlers",
    "register_providers",
    "register_routes",
    "subscribe",
    "timeout",
    "use_settings",
    "worker",
]
