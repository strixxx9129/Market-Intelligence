"""One error format for every failure:

    {"error": {"code": "not_found", "message": "...", "details": ..., "request_id": "..."}}

Clients (the website, external developers) can rely on `code` (stable, machine-readable) and
quote `request_id` in bug reports, so we can find the matching log line. Unexpected exceptions
become a generic 500: tracebacks never leak to clients, they go to the logs.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from marketlens.core.errors import ServiceError
from marketlens.core.logging import get_logger

logger = get_logger(__name__)

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(StarletteHTTPException):
    """Raise from services/routers: ApiError(404, "asset not found", code="asset_not_found")."""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code or _CODES.get(status_code, "error")
        self.details = details


def error_body(
    request: Request, status: int, message: str, code: str, details: Any = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or _CODES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
        return JSONResponse(
            error_body(request, exc.status_code, message, code, getattr(exc, "details", None)),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            error_body(request, exc.status_code, exc.message, exc.code, exc.details),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(
            error_body(request, 422, "request validation failed", "validation_error", details),
            status_code=422,
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        response = JSONResponse(
            error_body(request, 429, f"rate limit exceeded: {exc.detail}", "rate_limited"),
            status_code=429,
        )
        limiter = request.app.state.limiter
        return limiter._inject_headers(response, request.state.view_rate_limit)  # type: ignore[no-any-return]

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            error_body(request, 500, "internal server error", "internal_error"), status_code=500
        )
