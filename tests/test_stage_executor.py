from __future__ import annotations

from threading import Event

import pytest

from market_lens.agent.stage_executor import StageExecutor, StageTimeoutError


def test_stage_executor_enforces_hard_timeout() -> None:
    started = Event()
    release = Event()
    executor = StageExecutor(
        {("stock", "stock_price_history"): 0.01},
    )

    def blocked_operation() -> str:
        started.set()
        release.wait(timeout=0.2)
        return "late result"

    try:
        with pytest.raises(StageTimeoutError) as caught:
            executor.run(
                "stock",
                "stock_price_history",
                blocked_operation,
            )
    finally:
        release.set()

    assert started.is_set()
    assert caught.value.reason_code == "stock_price_history_timeout"
    assert caught.value.budget.timeout_seconds == 0.01


def test_child_stage_uses_remaining_parent_budget() -> None:
    now = [0.0]
    release = Event()
    executor = StageExecutor(
        {
            ("fund", "fund_index_matrix"): 1.0,
            ("index", "sina_index_price_history"): 10.0,
        },
        clock=lambda: now[0],
    )
    parent = executor.start_budget("fund", "fund_index_matrix")
    now[0] = 0.99

    try:
        with pytest.raises(StageTimeoutError) as caught:
            executor.run(
                "index",
                "sina_index_price_history",
                lambda: release.wait(timeout=0.2),
                parent=parent,
            )
    finally:
        release.set()

    assert caught.value.reason_code == "fund_index_matrix_timeout"
    assert caught.value.budget.matrix_key == "fund"
    assert caught.value.budget.timeout_seconds == 1.0


def test_operation_timeout_error_is_not_reclassified_as_stage_deadline() -> None:
    executor = StageExecutor(
        {("stock", "stock_price_history"): 0.1},
    )

    def raise_upstream_timeout() -> None:
        raise TimeoutError("upstream client timeout")

    with pytest.raises(TimeoutError, match="upstream client timeout") as caught:
        executor.run(
            "stock",
            "stock_price_history",
            raise_upstream_timeout,
        )

    assert not isinstance(caught.value, StageTimeoutError)
