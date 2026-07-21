from __future__ import annotations

import re
from datetime import date
from math import isfinite, sqrt
from statistics import mean, pstdev
from typing import Any

from market_lens.types import (
    FundHoldingsRoute,
    FundNavPoint,
    FundProductInfo,
    StockBar,
)
from market_lens.valuation.scoring_config import FundProductModelKey


def classify_fund_product(
    code: str,
    name: str | None,
    product: FundProductInfo | None,
    route: FundHoldingsRoute | None,
) -> dict[str, Any]:
    tracking = route.tracking if route else None
    resolved_name = (product.fund_name if product else None) or name or ""
    normalized_name = re.sub(r"\s+", "", resolved_name).upper()
    fund_type = product.fund_type if product else (tracking.fund_type if tracking else None)
    warnings: list[str] = []

    if (tracking and tracking.target_etf_code) or "ETF联接" in normalized_name:
        profile: FundProductModelKey = "etf_linked"
        reason = (
            "explicit_target_etf"
            if tracking and tracking.target_etf_code
            else "legal_name_feeder_fallback"
        )
        if not tracking or not tracking.target_etf_code:
            warnings.append("target_etf_relationship_unavailable")
    elif "ETF" in normalized_name and "联接" not in normalized_name:
        profile = "etf"
        reason = "legal_name_etf"
        if not tracking or not tracking.index_code:
            warnings.append("tracked_index_relationship_unavailable")
    elif tracking and tracking.index_code:
        profile = "index_fund"
        reason = "explicit_tracked_index"
    elif fund_type and "指数" in fund_type:
        profile = "index_fund"
        reason = "fund_type_index_fallback"
        warnings.append("tracked_index_relationship_unavailable")
    elif "指数" in normalized_name:
        profile = "index_fund"
        reason = "legal_name_index_fallback"
        warnings.append("tracked_index_relationship_unavailable")
    else:
        profile = "active_fund"
        reason = "no_index_tracking_relationship"

    return {
        "profile": profile,
        "reason": reason,
        "fund_code": code,
        "fund_type": fund_type,
        "tracked_index_code": tracking.index_code if tracking else None,
        "tracked_index_name": tracking.index_name if tracking else None,
        "target_etf_code": tracking.target_etf_code if tracking else None,
        "target_etf_name": tracking.target_etf_name if tracking else None,
        "warnings": warnings,
    }


def calculate_tracking_metrics(
    nav_points: list[FundNavPoint],
    benchmark_bars: list[StockBar],
    *,
    profile: FundProductModelKey,
    benchmark: str | None,
    benchmark_source: str = "tracked_index_price_history",
) -> dict[str, Any]:
    if profile == "active_fund":
        return tracking_result("not_applicable", reason="active_fund")
    if not benchmark_bars:
        return tracking_result("unavailable", reason="tracked_index_history_unavailable")

    fund_daily_returns = fund_returns(nav_points)
    benchmark_daily_returns = benchmark_returns(benchmark_bars)
    common_dates = sorted(fund_daily_returns.keys() & benchmark_daily_returns.keys())
    if not common_dates:
        return tracking_result(
            "unavailable",
            reason="insufficient_overlapping_returns",
            source_as_of=common_dates[-1] if common_dates else None,
        )

    exposure, warnings = benchmark_exposure(
        profile,
        benchmark,
        benchmark_source=benchmark_source,
    )
    active_returns = [
        fund_daily_returns[day] - benchmark_daily_returns[day] * exposure
        for day in common_dates
    ]

    sample_size = len(active_returns)
    tracking_error = pstdev(active_returns) * sqrt(252) if sample_size >= 2 else None
    tracking_deviation = mean(active_returns) * 252 if active_returns else None
    return {
        "status": "available",
        "method": "overlapping_daily_returns_benchmark_proxy",
        "source": f"fund_nav_and_{benchmark_source}",
        "source_as_of": common_dates[-1].isoformat(),
        "sample_size": sample_size,
        "minimum_sample_size": 60,
        "full_sample_size": 252,
        "benchmark_exposure": exposure,
        "tracking_error_annualized": tracking_error,
        "tracking_deviation_annualized": tracking_deviation,
        "tracking_deviation_abs_annualized": (
            abs(tracking_deviation) if tracking_deviation is not None else None
        ),
        "warnings": warnings,
        "reason": None,
    }


def fund_returns(points: list[FundNavPoint]) -> dict[date, float]:
    returns: dict[date, float] = {}
    previous_value: float | None = None
    for point in sorted(points, key=lambda item: item.date):
        value = finite_positive(point.unit_nav)
        if point.daily_growth_pct is not None and isfinite(point.daily_growth_pct):
            returns[point.date] = float(point.daily_growth_pct) / 100.0
        elif value is not None and previous_value is not None:
            returns[point.date] = value / previous_value - 1
        if value is not None:
            previous_value = value
    return returns


def benchmark_returns(bars: list[StockBar]) -> dict[date, float]:
    returns: dict[date, float] = {}
    previous_value: float | None = None
    for bar in sorted(bars, key=lambda item: item.date):
        value = finite_positive(bar.close)
        if bar.change_pct is not None and isfinite(bar.change_pct):
            returns[bar.date] = float(bar.change_pct) / 100.0
        elif value is not None and previous_value is not None:
            returns[bar.date] = value / previous_value - 1
        if value is not None:
            previous_value = value
    return returns


def finite_positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return float(value)


def benchmark_exposure(
    profile: FundProductModelKey,
    benchmark: str | None,
    *,
    benchmark_source: str = "tracked_index_price_history",
) -> tuple[float, list[str]]:
    proxy_warning = {
        "target_etf_nav_history": "target_etf_nav_return_proxy",
        "sina_index_price_history": "tracked_index_price_return_proxy",
        "tracked_index_price_history": "tracked_index_price_return_proxy",
    }.get(benchmark_source, "benchmark_return_proxy")
    if profile == "etf":
        return 1.0, [proxy_warning]
    match = re.search(r"(?:\*|×|X)\s*(\d+(?:\.\d+)?)\s*%", (benchmark or "").upper())
    if not match:
        return 1.0, [
            proxy_warning,
            "benchmark_exposure_unavailable",
        ]
    exposure = max(0.0, min(float(match.group(1)) / 100.0, 1.0))
    warnings = [proxy_warning]
    if exposure < 1.0:
        warnings.append("cash_benchmark_component_assumed_zero_return")
    return exposure, warnings


def tracking_result(
    status: str,
    *,
    reason: str,
    source_as_of: date | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "method": None,
        "source": "eastmoney_fund_nav_and_tracked_index_history",
        "source_as_of": source_as_of.isoformat() if source_as_of else None,
        "sample_size": 0,
        "minimum_sample_size": 60,
        "full_sample_size": 252,
        "benchmark_exposure": None,
        "tracking_error_annualized": None,
        "tracking_deviation_annualized": None,
        "tracking_deviation_abs_annualized": None,
        "warnings": [],
        "reason": reason,
    }
