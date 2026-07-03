from __future__ import annotations

from datetime import date

from market_lens.valuation.metrics import (
    annualized_return,
    max_drawdown,
    percentile_rank,
    valuation_label,
)


def test_percentile_rank() -> None:
    assert percentile_rank([1, 2, 3, 4], 3) == 0.75
    assert percentile_rank([], 3) is None
    assert percentile_rank([1, None, 3], None) is None


def test_valuation_label() -> None:
    assert valuation_label(0.1) == "low"
    assert valuation_label(0.5) == "neutral"
    assert valuation_label(0.9) == "very_expensive"
    assert valuation_label(None) == "unknown"


def test_max_drawdown() -> None:
    assert max_drawdown([100, 120, 90, 130]) == -0.25
    assert max_drawdown([]) is None


def test_annualized_return() -> None:
    result = annualized_return(100, 121, date(2020, 1, 1), date(2022, 1, 1))
    assert result is not None
    assert 0.09 < result < 0.11
