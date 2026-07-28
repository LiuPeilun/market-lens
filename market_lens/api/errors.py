from __future__ import annotations

from typing import Any

from market_lens.errors import (
    ErrorCategory,
    InternalServiceError,
    MarketLensError,
)
from market_lens.tools.executor import ToolInvocationError

HTTP_STATUS_BY_CATEGORY = {
    ErrorCategory.INVALID_REQUEST: 400,
    ErrorCategory.UPSTREAM_UNAVAILABLE: 503,
    ErrorCategory.DATA_UNAVAILABLE: 422,
    ErrorCategory.INTERNAL_ERROR: 500,
    ErrorCategory.PERSISTENCE_ERROR: 502,
}

TOOL_ERROR_CATEGORIES = {
    "invalid_input": ErrorCategory.INVALID_REQUEST,
    "tool_timeout": ErrorCategory.UPSTREAM_UNAVAILABLE,
    "invalid_output": ErrorCategory.INTERNAL_ERROR,
    "tool_execution_failed": ErrorCategory.INTERNAL_ERROR,
}


def error_from_tool_invocation(exc: ToolInvocationError) -> MarketLensError:
    result = exc.result
    category = parse_error_category(result.error_category)
    if category is None:
        category = TOOL_ERROR_CATEGORIES.get(
            result.error_code or "",
            ErrorCategory.INTERNAL_ERROR,
        )
    message = result.message or _default_message(category)
    if category is ErrorCategory.INTERNAL_ERROR:
        message = _default_message(category)
    return MarketLensError(
        code=result.error_code or "tool_execution_failed",
        category=category,
        message=message,
        retryable=result.retryable or category is ErrorCategory.UPSTREAM_UNAVAILABLE,
    )


def parse_error_category(value: str | None) -> ErrorCategory | None:
    if value is None:
        return None
    try:
        return ErrorCategory(value)
    except ValueError:
        return None


def http_status_for_error(exc: MarketLensError) -> int:
    return HTTP_STATUS_BY_CATEGORY[exc.category]


def error_response_content(exc: MarketLensError) -> dict[str, Any]:
    return {
        "detail": exc.message,
        "code": exc.code,
        "category": exc.category.value,
        "retryable": exc.retryable,
    }


def internal_error() -> InternalServiceError:
    return InternalServiceError()


def _default_message(category: ErrorCategory) -> str:
    return {
        ErrorCategory.INVALID_REQUEST: "The analysis request is invalid",
        ErrorCategory.UPSTREAM_UNAVAILABLE: "Market data service is temporarily unavailable",
        ErrorCategory.DATA_UNAVAILABLE: "Verified market data is unavailable for this request",
        ErrorCategory.INTERNAL_ERROR: "Market analysis failed unexpectedly",
        ErrorCategory.PERSISTENCE_ERROR: "The analysis result could not be saved",
    }[category]
