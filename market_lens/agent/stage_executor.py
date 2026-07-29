from __future__ import annotations

import concurrent.futures
from collections.abc import Callable, Mapping
from contextvars import copy_context
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

from market_lens.valuation.fallback_matrix import (
    FALLBACK_MATRICES,
    FallbackMatrixKey,
)

ResultT = TypeVar("ResultT")
StageKey = tuple[FallbackMatrixKey, str]


@dataclass(frozen=True)
class StageBudget:
    matrix_key: FallbackMatrixKey
    step_key: str
    timeout_seconds: float
    deadline: float

    @property
    def reason_code(self) -> str:
        return f"{self.step_key}_timeout"


class StageTimeoutError(TimeoutError):
    def __init__(self, budget: StageBudget) -> None:
        super().__init__(f"Stage '{budget.step_key}' exceeded its time budget")
        self.budget = budget
        self.reason_code = budget.reason_code


class StageExecutor:
    """Runs deterministic analysis stages within declared hard deadlines."""

    def __init__(
        self,
        budget_overrides: Mapping[StageKey, float] | None = None,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._budget_overrides = dict(budget_overrides or {})
        self._clock = clock
        if any(value <= 0 for value in self._budget_overrides.values()):
            raise ValueError("Stage timeout overrides must be greater than zero")

    def start_budget(
        self,
        matrix_key: FallbackMatrixKey,
        step_key: str,
    ) -> StageBudget:
        timeout_seconds = self.timeout_seconds(matrix_key, step_key)
        return StageBudget(
            matrix_key=matrix_key,
            step_key=step_key,
            timeout_seconds=timeout_seconds,
            deadline=self._clock() + timeout_seconds,
        )

    def timeout_seconds(
        self,
        matrix_key: FallbackMatrixKey,
        step_key: str,
    ) -> float:
        override = self._budget_overrides.get((matrix_key, step_key))
        if override is not None:
            return override
        return float(
            FALLBACK_MATRICES[matrix_key].step(step_key).timeout_budget_seconds
        )

    def run(
        self,
        matrix_key: FallbackMatrixKey,
        step_key: str,
        operation: Callable[[], ResultT],
        *,
        parent: StageBudget | None = None,
    ) -> ResultT:
        budget = self.start_budget(matrix_key, step_key)
        timeout_seconds = budget.timeout_seconds
        timeout_budget = budget
        if parent is not None:
            parent_remaining = self.remaining_seconds(parent)
            if parent_remaining <= 0:
                raise StageTimeoutError(parent)
            if parent_remaining < timeout_seconds:
                timeout_seconds = parent_remaining
                timeout_budget = parent
        return self._run_with_timeout(operation, timeout_seconds, timeout_budget)

    def run_remaining(
        self,
        budget: StageBudget,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        remaining = self.remaining_seconds(budget)
        if remaining <= 0:
            raise StageTimeoutError(budget)
        return self._run_with_timeout(operation, remaining, budget)

    def remaining_seconds(self, budget: StageBudget) -> float:
        return max(0.0, budget.deadline - self._clock())

    @staticmethod
    def _run_with_timeout(
        operation: Callable[[], ResultT],
        timeout_seconds: float,
        timeout_budget: StageBudget,
    ) -> ResultT:
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"market-lens-{timeout_budget.step_key}",
        )
        context = copy_context()
        future = pool.submit(context.run, operation)
        timed_out = False
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            if future.done():
                raise
            timed_out = True
            future.cancel()
            raise StageTimeoutError(timeout_budget) from exc
        finally:
            pool.shutdown(wait=not timed_out, cancel_futures=True)
