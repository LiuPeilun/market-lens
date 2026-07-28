from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from market_lens.api.schemas import PersistenceStatus
from market_lens.storage.supabase import SupabaseError

logger = logging.getLogger(__name__)
T = TypeVar("T")


class PersistenceTracker:
    def __init__(self) -> None:
        self._attempted = 0
        self._succeeded = 0
        self._failed_operations: list[str] = []

    def attempt(self, operation: str, action: Callable[[], T]) -> T | None:
        self._attempted += 1
        try:
            result = action()
        except SupabaseError:
            logger.warning(
                "Non-blocking persistence failed operation=%s",
                operation,
                exc_info=True,
            )
            self._failed_operations.append(operation)
            return None
        self._succeeded += 1
        return result

    def report(self) -> PersistenceStatus:
        if not self._attempted:
            status = "not_attempted"
            error_code = None
        elif not self._failed_operations:
            status = "saved"
            error_code = None
        elif self._succeeded:
            status = "partial"
            error_code = "persistence_partial_failure"
        else:
            status = "failed"
            error_code = "persistence_failed"
        return PersistenceStatus(
            status=status,
            error_code=error_code,
            retryable=bool(self._failed_operations),
            failed_operations=list(self._failed_operations),
        )
