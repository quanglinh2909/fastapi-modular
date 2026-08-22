"""Chuẩn hoá mọi lỗi HTTP về cùng một hình dạng JSON.

{"code": "...", "message": "...", "details": ..., "request_id": "..."}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from fastapi_modular.core.context import get_request_id, get_trace_id
from fastapi_modular.core.exceptions import AppError
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)


def _response(status_code: int, payload: dict[str, object]) -> JSONResponse:
    payload["request_id"] = get_request_id()
    # Trả cả trace_id để người dùng báo lỗi kèm mã này là tra được toàn bộ
    # hành trình qua các dịch vụ, không chỉ log của riêng dịch vụ này.
    trace_id = get_trace_id()
    if trace_id:
        payload["trace_id"] = trace_id
    return JSONResponse(status_code=status_code, content=payload)


def _unavailable(exc: Exception, *, debug: bool) -> JSONResponse:
    """Database không với tới được — lỗi vận hành, không phải bug.

    Trả 503 chứ không phải 500: load balancer và client biết đây là tình trạng
    tạm thời và nên thử lại, còn 500 nghĩa là "gửi lại cũng vô ích".
    """
    log.warning("db.unavailable", error=f"{type(exc).__name__}: {exc}")
    return _response(
        503,
        {
            "code": "database_unavailable",
            "message": "Không kết nối được cơ sở dữ liệu, vui lòng thử lại",
            **({"details": f"{type(exc).__name__}: {exc}"} if debug else {}),
        },
    )


def _register_circuit_handler(app: FastAPI, *, debug: bool) -> None:
    """Mạch đang ngắt -> 503 kèm Retry-After, không chạm database."""
    from fastapi_modular.infrastructure.database.circuit import CircuitOpenError

    @app.exception_handler(CircuitOpenError)
    async def _circuit_open(_: Request, exc: CircuitOpenError) -> JSONResponse:
        log.warning("db.circuit_rejected", backend=exc.backend, retry_after=exc.retry_after)
        response = _response(
            503,
            {
                "code": "database_unavailable",
                "message": "Không kết nối được cơ sở dữ liệu, vui lòng thử lại",
                **({"details": str(exc)} if debug else {}),
            },
        )
        response.headers["Retry-After"] = str(max(1, int(exc.retry_after)))
        return response


def _register_duplicate_handler(app: FastAPI, *, debug: bool) -> None:
    """Trùng khoá do backend memory phát hiện -> 409, giống SQL và Mongo."""
    from fastapi_modular.infrastructure.database.base import DuplicateKeyViolation

    @app.exception_handler(DuplicateKeyViolation)
    async def _duplicate(_: Request, exc: DuplicateKeyViolation) -> JSONResponse:
        log.warning("db.duplicate_key", storage=exc.storage, columns=list(exc.columns))
        return _response(
            409,
            {
                "code": "integrity_error",
                "message": "Thao tác vi phạm ràng buộc dữ liệu",
                **({"details": str(exc)} if debug else {}),
            },
        )


def _register_connection_handlers(app: FastAPI, *, debug: bool) -> None:
    """Lỗi socket thuần (ConnectionRefused/Reset/Aborted) -> 503."""

    @app.exception_handler(ConnectionError)
    async def _connection_error(_: Request, exc: ConnectionError) -> JSONResponse:
        return _unavailable(exc, debug=debug)

    @app.exception_handler(TimeoutError)
    async def _timeout_error(_: Request, exc: TimeoutError) -> JSONResponse:
        # Quá hạn khi gọi database cũng là "tạm thời không phục vụ được".
        return _unavailable(exc, debug=debug)


def _register_mongo_handlers(app: FastAPI, *, debug: bool) -> None:
    """Handler cho lỗi MongoDB — chỉ khi project thực sự cài driver."""
    try:
        from pymongo.errors import ConnectionFailure, DuplicateKeyError, PyMongoError
    except ModuleNotFoundError:
        log.debug("error_handlers.pymongo_missing")
        return

    @app.exception_handler(ConnectionFailure)
    async def _mongo_unavailable(_: Request, exc: Exception) -> JSONResponse:
        # ConnectionFailure là cha của AutoReconnect, NetworkTimeout,
        # ServerSelectionTimeoutError — tức mọi tình huống "Mongo không với tới được".
        return _unavailable(exc, debug=debug)

    @app.exception_handler(DuplicateKeyError)
    async def _mongo_duplicate(_: Request, exc: Exception) -> JSONResponse:
        log.warning("db.duplicate_key", error=str(exc))
        return _response(
            409,
            {
                "code": "integrity_error",
                "message": "Thao tác vi phạm ràng buộc dữ liệu",
                **({"details": str(exc)} if debug else {}),
            },
        )

    @app.exception_handler(PyMongoError)
    async def _mongo_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("db.error", error=str(exc))
        return _response(
            500,
            {
                "code": "database_error",
                "message": "Lỗi truy cập cơ sở dữ liệu",
                **({"details": str(exc)} if debug else {}),
            },
        )


def _register_amqp_handlers(app: FastAPI, *, debug: bool) -> None:
    """Handler cho lỗi RabbitMQ — chỉ khi project thực sự cài aio-pika."""
    try:
        from aiormq.exceptions import AMQPError, ChannelInvalidStateError
    except ModuleNotFoundError:
        log.debug("error_handlers.aio_pika_missing")
        return

    # ChannelInvalidStateError KHÔNG kế thừa AMQPError mà kế thừa RuntimeError
    # — đăng ký thiếu nó thì "gửi tin lúc broker vừa chết" rơi vào handler 500
    # chung thay vì 503. Đây là lỗi tôi gặp khi thử tắt broker giữa chừng.
    @app.exception_handler(AMQPError)
    @app.exception_handler(ChannelInvalidStateError)
    async def _amqp_error(_: Request, exc: Exception) -> JSONResponse:
        # Mọi lỗi AMQP đều là "hạ tầng nhắn tin đang trục trặc" dưới góc nhìn
        # client: kết nối rớt, kênh bị đóng, broker từ chối. Trả 503 để client
        # biết thử lại sau, thay vì 500 nghĩa là gửi lại cũng vô ích.
        log.warning("mq.unavailable", error=f"{type(exc).__name__}: {exc}")
        return _response(
            503,
            {
                "code": "rabbitmq_unavailable",
                "message": "RabbitMQ không sẵn sàng, vui lòng thử lại",
                **({"details": f"{type(exc).__name__}: {exc}"} if debug else {}),
            },
        )


def _register_db_handlers(app: FastAPI, *, debug: bool) -> None:
    """Đăng ký handler cho lỗi SQLAlchemy — chỉ khi project thực sự cài SQLAlchemy.

    Template này không bắt buộc dùng DB; khi chưa cài (chưa dùng Postgres/SQLite)
    thì bỏ qua, các lỗi khác vẫn rơi vào handler Exception chung.
    """
    try:
        from sqlalchemy.exc import (
            IntegrityError,
            InterfaceError,
            OperationalError,
            SQLAlchemyError,
        )
    except ModuleNotFoundError:
        log.debug("error_handlers.sqlalchemy_missing")
        return

    # Starlette chọn handler khớp nhất theo MRO, nên hai handler dưới đây thắng
    # handler SQLAlchemyError chung ở cuối hàm.
    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    async def _sql_unavailable(_: Request, exc: Exception) -> JSONResponse:
        return _unavailable(exc, debug=debug)

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        log.warning("db.integrity_error", error=str(exc.orig))
        return _response(
            409,
            {
                "code": "integrity_error",
                "message": "Thao tác vi phạm ràng buộc dữ liệu",
                **({"details": str(exc.orig)} if debug else {}),
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.exception("db.error", error=str(exc))
        return _response(
            500,
            {
                "code": "database_error",
                "message": "Lỗi truy cập cơ sở dữ liệu",
                **({"details": str(exc)} if debug else {}),
            },
        )


def register_error_handlers(app: FastAPI, *, debug: bool = False) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("app.error", code=exc.error_code, message=exc.message)
        return _response(exc.status_code, exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _response(
            422,
            {
                "code": "validation_error",
                "message": "Dữ liệu đầu vào không hợp lệ",
                "details": [
                    {
                        "field": ".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]),
                        "message": err["msg"],
                        "type": err["type"],
                    }
                    for err in exc.errors()
                ],
            },
        )

    @app.exception_handler(NotImplementedError)
    async def _not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
        # Khung do `make module` sinh ra dùng `raise NotImplementedError(...)`.
        return _response(
            501,
            {
                "code": "not_implemented",
                "message": str(exc) or "Chức năng chưa được cài đặt",
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _response(
            exc.status_code,
            {"code": f"http_{exc.status_code}", "message": str(exc.detail)},
        )

    _register_circuit_handler(app, debug=debug)
    _register_duplicate_handler(app, debug=debug)
    _register_connection_handlers(app, debug=debug)
    _register_db_handlers(app, debug=debug)
    _register_mongo_handlers(app, debug=debug)
    _register_amqp_handlers(app, debug=debug)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("app.unhandled_error", error=str(exc))
        return _response(
            500,
            {
                "code": "internal_error",
                "message": "Internal server error",
                **({"details": f"{type(exc).__name__}: {exc}"} if debug else {}),
            },
        )
