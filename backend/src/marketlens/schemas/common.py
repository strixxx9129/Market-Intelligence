from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

DISCLAIMER = (
    "Educational project output, not financial advice. Forecasts are statistical estimates "
    "with low accuracy; do not use them to make investment decisions."
)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PageInfo(BaseModel):
    limit: int
    next_cursor: str | None = Field(
        None, description="Pass as `cursor` to get the next page; null on the last page."
    )


class Timestamped(BaseModel):
    generated_at: datetime


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}
