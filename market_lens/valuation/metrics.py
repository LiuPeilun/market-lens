from __future__ import annotations

import math
from datetime import date

from market_lens.types import FundNavPoint


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


def fund_performance_index(points: list[FundNavPoint]) -> list[tuple[date, float]]:
    """Build a distribution-adjusted wealth index from fund NAV or adjusted prices."""
    result: list[tuple[date, float]] = []
    wealth = 1.0
    previous_unit_nav: float | None = None
    previous_cumulative_nav: float | None = None

    for point in sorted(points, key=lambda item: item.date):
        unit_nav = _finite_positive(point.unit_nav)
        if unit_nav is None:
            continue
        cumulative_nav = _finite_number(point.cumulative_nav)

        if previous_unit_nav is not None:
            period_return: float | None = None
            if previous_cumulative_nav is not None and cumulative_nav is not None:
                previous_distributions = previous_cumulative_nav - previous_unit_nav
                current_distributions = cumulative_nav - unit_nav
                distribution = current_distributions - previous_distributions
                if distribution < 1e-8:
                    distribution = 0.0
                gross_return = (unit_nav + distribution) / previous_unit_nav
                if math.isfinite(gross_return) and gross_return > 0:
                    period_return = gross_return - 1.0

            if (
                period_return is None
                and point.daily_growth_pct is not None
                and math.isfinite(point.daily_growth_pct)
            ):
                period_return = float(point.daily_growth_pct) / 100.0
            if period_return is None:
                period_return = unit_nav / previous_unit_nav - 1.0

            wealth *= 1.0 + period_return

        result.append((point.date, wealth))
        previous_unit_nav = unit_nav
        previous_cumulative_nav = cumulative_nav

    return result


def _finite_number(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _finite_positive(value: float | None) -> float | None:
    number = _finite_number(value)
    if number is None or number <= 0:
        return None
    return number


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
