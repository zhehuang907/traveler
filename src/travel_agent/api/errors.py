"""统一错误模型与异常处理。

所有错误响应固定为信封结构：
    {"error": {"code": "...", "message": "...", "request_id": "...", "details": {...}}}
"""

import json
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from travel_agent.logging_conf import get_logger

__all__ = ["AppError", "ErrorCode", "register_exception_handlers"]


class ErrorCode(StrEnum):
    """业务错误码（字符串值对外稳定，禁止随意改名）。"""

    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """可预期的业务异常基类。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request: Request,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id: str | None = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": details,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """在应用上注册三类异常处理器。"""
    log = get_logger(component="exception_handler")

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log.info(
            "business_error",
            code=exc.code.value,
            path=request.url.path,
            status_code=exc.status_code,
        )
        return _error_response(exc.status_code, exc.code.value, exc.message, request, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = json.loads(json.dumps(exc.errors(), default=str))
        return _error_response(
            422,
            ErrorCode.VALIDATION_ERROR.value,
            "请求参数校验失败",
            request,
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_map = {
            401: ErrorCode.UNAUTHORIZED,
            404: ErrorCode.NOT_FOUND,
            429: ErrorCode.RATE_LIMITED,
            502: ErrorCode.UPSTREAM_ERROR,
            503: ErrorCode.SERVICE_UNAVAILABLE,
        }
        code = code_map.get(exc.status_code, ErrorCode.BAD_REQUEST)
        return _error_response(exc.status_code, code.value, str(exc.detail), request)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 不向客户端回显内部细节，避免泄漏实现与敏感信息
        log.exception("unhandled_exception", path=request.url.path, error_type=type(exc).__name__)
        return _error_response(
            500,
            ErrorCode.INTERNAL_ERROR.value,
            "服务内部错误，请稍后重试或联系管理员",
            request,
        )
