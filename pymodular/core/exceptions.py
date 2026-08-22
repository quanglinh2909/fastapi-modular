"""Cây exception của tầng ứng dụng.

Service/repository chỉ ném các lỗi này; việc dịch sang HTTP status hay gRPC
status code do tầng ngoài (error_handlers / interceptor) đảm nhiệm. Nhờ vậy
domain không phụ thuộc vào giao thức.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Lỗi nghiệp vụ gốc."""

    status_code: int = 500
    error_code: str = "internal_error"
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        error_code: str | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.message
        self.error_code = error_code or self.error_code
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.error_code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class BadRequestError(AppError):
    status_code = 400
    error_code = "bad_request"
    message = "Bad request"


class UnauthorizedError(AppError):
    status_code = 401
    error_code = "unauthorized"
    message = "Not authenticated"


class ForbiddenError(AppError):
    status_code = 403
    error_code = "forbidden"
    message = "Permission denied"


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"
    message = "Resource conflict"


class ValidationError(AppError):
    status_code = 422
    error_code = "validation_error"
    message = "Validation failed"


class TooManyRequestsError(AppError):
    status_code = 429
    error_code = "too_many_requests"
    message = "Too many requests"


class NotImplementedYetError(AppError):
    """Hàm mới được sinh khung, chưa viết thân.

    Trả 501 chứ không phải 500: 501 nói "chức năng này chưa tồn tại", còn 500
    nói "có bug". Nhờ vậy khung do `pym module` sinh ra không bị nhầm là hỏng.
    """

    status_code = 501
    error_code = "not_implemented"
    message = "Chức năng chưa được cài đặt"


class ServiceUnavailableError(AppError):
    status_code = 503
    error_code = "service_unavailable"
    message = "Service temporarily unavailable"


class ComponentNotEnabledError(AppError):
    """Code cố dùng một hạ tầng đang bị tắt trong config."""

    status_code = 503
    error_code = "component_not_enabled"
    message = "Required infrastructure component is not enabled"
