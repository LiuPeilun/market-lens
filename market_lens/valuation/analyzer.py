from __future__ import annotations

from dataclasses import asdict
from typing import Any

from market_lens.types import FundNavPoint, StockBar, StockValuationPoint
from market_lens.valuation.metrics import (
    annualized_return,
    format_pct,
    max_drawdown,
    percentile_rank,
    simple_return,
    valuation_label,
)


def analyze_stock(
    symbol: str,
    bars: list[StockBar],
    valuations: list[StockValuationPoint],
    name: str | None = None,
) -> dict[str, Any]:
    latest_bar = bars[-1] if bars else None
    latest_valuation = valuations[-1] if valuations else None

    pe_percentile = percentile_rank(
        [item.pe_ttm for item in valuations],
        latest_valuation.pe_ttm if latest_valuation else None,
    )
    pb_percentile = percentile_rank(
        [item.pb for item in valuations],
        latest_valuation.pb if latest_valuation else None,
    )
    close_values = [item.close for item in bars]

    total_return = None
    annualized = None
    if bars:
        total_return = simple_return(bars[0].close, bars[-1].close)
        annualized = annualized_return(bars[0].close, bars[-1].close, bars[0].date, bars[-1].date)

    return {
        "asset_type": "stock",
        "code": symbol,
        "name": name or latest_valuation.name if latest_valuation else name,
        "as_of": latest_bar.date.isoformat() if latest_bar else None,
        "latest_price": latest_bar.close if latest_bar else None,
        "valuation": {
            "as_of": latest_valuation.date.isoformat() if latest_valuation else None,
            "pe_ttm": latest_valuation.pe_ttm if latest_valuation else None,
            "pb": latest_valuation.pb if latest_valuation else None,
            "pe_ttm_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
            "pe_ttm_label": valuation_label(pe_percentile),
            "pb_label": valuation_label(pb_percentile),
        },
        "performance": {
            "sample_size": len(bars),
            "total_return": total_return,
            "annualized_return": annualized,
            "max_drawdown": max_drawdown(close_values),
            "total_return_text": format_pct(total_return),
            "annualized_return_text": format_pct(annualized),
            "max_drawdown_text": format_pct(max_drawdown(close_values)),
        },
        "notes": [
            "Stock valuation history may not cover a full 10 years for every symbol.",
            "This is a research summary, not investment advice.",
        ],
        "latest_raw": asdict(latest_valuation) if latest_valuation else None,
    }


def analyze_fund(
    code: str,
    nav_points: list[FundNavPoint],
    name: str | None = None,
) -> dict[str, Any]:
    latest = nav_points[-1] if nav_points else None
    nav_values = [item.unit_nav for item in nav_points]
    total_return = None
    annualized = None
    if nav_points:
        total_return = simple_return(nav_points[0].unit_nav, nav_points[-1].unit_nav)
        annualized = annualized_return(
            nav_points[0].unit_nav,
            nav_points[-1].unit_nav,
            nav_points[0].date,
            nav_points[-1].date,
        )

    return {
        "asset_type": "fund",
        "code": code,
        "name": name,
        "as_of": latest.date.isoformat() if latest else None,
        "latest_unit_nav": latest.unit_nav if latest else None,
        "latest_cumulative_nav": latest.cumulative_nav if latest else None,
        "performance": {
            "sample_size": len(nav_points),
            "total_return": total_return,
            "annualized_return": annualized,
            "max_drawdown": max_drawdown(nav_values),
            "total_return_text": format_pct(total_return),
            "annualized_return_text": format_pct(annualized),
            "max_drawdown_text": format_pct(max_drawdown(nav_values)),
        },
        "valuation": {
            "method": "nav_performance_only",
            "status": "holding_weighted_stock_valuation_pending",
        },
        "notes": [
            "Fund NAV performance is not the same as holding-level valuation.",
            "The next useful module is fund holdings plus weighted PE/PB estimation.",
            "This is a research summary, not investment advice.",
        ],
    }
