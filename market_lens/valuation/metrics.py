from __future__ import annotations

import math
from datetime import date


def clean_numbers(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None and math.isfinite(value)]


def percentile_rank(values: list[float | None], current: float | None) -> float | None:
    clean = clean_numbers(values)
    if current is None or not clean:
        return None
    lower_or_equal = sum(1 for value in clean if value <= current)
    return lower_or_equal / len(clean)


def simple_return(first: float | None, last: float | None) -> float | None:
    if first is None or last is None or first == 0:
        return None
    return last / first - 1


def annualized_return(
    first: float | None,
    last: float | None,
    start: date,
    end: date,
) -> float | None:
    total = simple_return(first, last)
    days = (end - start).days
    if total is None or days <= 0:
        return None
    years = days / 365.25
    return (1 + total) ** (1 / years) - 1


def max_drawdown(values: list[float | None]) -> float | None:
    clean = clean_numbers(values)
    if not clean:
        return None
    peak = clean[0]
    worst = 0.0
    for value in clean:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def valuation_label(percentile: float | None) -> str:
    if percentile is None:
        return "unknown"
    if percentile < 0.2:
        return "low"
    if percentile < 0.4:
        return "reasonable_low"
    if percentile < 0.7:
        return "neutral"
    if percentile < 0.85:
        return "expensive"
    return "very_expensive"


def format_pct(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value * 100:.2f}%"
