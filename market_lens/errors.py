from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    DATA_UNAVAILABLE = "data_unavailable"
    INTERNAL_ERROR = "internal_error"
    PERSISTENCE_ERROR = "persistence_error"


class MarketLensError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        category: ErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.retryable = retryable


class InvalidRequestError(MarketLensError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            code=code,
            category=ErrorCategory.INVALID_REQUEST,
            message=message,
            retryable=False,
        )


class UpstreamUnavailableError(MarketLensError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            code=code,
            category=ErrorCategory.UPSTREAM_UNAVAILABLE,
            message=message,
            retryable=True,
        )


class DataUnavailableError(MarketLensError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            code=code,
            category=ErrorCategory.DATA_UNAVAILABLE,
            message=message,
            retryable=False,
        )


class InternalServiceError(MarketLensError):
    def __init__(
        self,
        code: str = "internal_error",
        message: str = "Market analysis failed unexpectedly",
    ) -> None:
        super().__init__(
            code=code,
            category=ErrorCategory.INTERNAL_ERROR,
            message=message,
            retryable=False,
        )


class PersistenceFailure(MarketLensError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            code=code,
            category=ErrorCategory.PERSISTENCE_ERROR,
            message=message,
            retryable=True,
        )
