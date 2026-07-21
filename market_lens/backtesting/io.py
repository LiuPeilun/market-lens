from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_lens.backtesting.models import (
    AssessmentSnapshot,
    BacktestDataError,
    PricePoint,
    parse_required_date,
    snapshot_from_analysis,
)


def load_backtest_dataset(
    path: Path,
) -> tuple[list[AssessmentSnapshot], dict[str, list[PricePoint]], list[PricePoint]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BacktestDataError(f"failed to read backtest dataset: {exc}") from exc
    if not isinstance(payload, dict):
        raise BacktestDataError("backtest dataset root must be an object")
    analyses = payload.get("analyses")
    raw_prices = payload.get("prices")
    if not isinstance(analyses, list):
        raise BacktestDataError("backtest dataset analyses must be an array")
    if not isinstance(raw_prices, dict):
        raise BacktestDataError("backtest dataset prices must be an object")
    snapshots = [snapshot_from_analysis(item) for item in analyses]
    prices = {
        str(key): parse_price_points(value, f"prices.{key}")
        for key, value in raw_prices.items()
    }
    benchmark = parse_price_points(
        payload.get("benchmark_prices") or [], "benchmark_prices"
    )
    return snapshots, prices, benchmark


def parse_price_points(value: Any, label: str) -> list[PricePoint]:
    if not isinstance(value, list):
        raise BacktestDataError(f"{label} must be an array")
    result: list[PricePoint] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BacktestDataError(f"{label}[{index}] must be an object")
        close = item.get("close")
        if isinstance(close, bool) or not isinstance(close, int | float):
            raise BacktestDataError(f"{label}[{index}].close must be numeric")
        result.append(
            PricePoint(
                date=parse_required_date(item.get("date"), f"{label}[{index}].date"),
                close=float(close),
            )
        )
    return result
