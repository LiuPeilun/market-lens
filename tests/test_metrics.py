from __future__ import annotations

from datetime import date

from market_lens.types import FundHolding, FundNavPoint, StockBar, StockValuationPoint
from market_lens.valuation.framework import (
    analyze_fund_valuation,
    analyze_index_price_proxy,
    analyze_stock_valuation,
    valuation_level,
)
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


def test_valuation_level_boundaries() -> None:
    assert valuation_level(95) == "extremely_overvalued"
    assert valuation_level(80) == "overvalued"
    assert valuation_level(65) == "slightly_overvalued"
    assert valuation_level(50) == "fair"
    assert valuation_level(35) == "slightly_undervalued"
    assert valuation_level(20) == "undervalued"
    assert valuation_level(5) == "extremely_undervalued"
    assert valuation_level(None) == "unknown"


def test_analyze_stock_valuation_framework() -> None:
    rows = [
        StockValuationPoint(
            date=date(2024, 1, index),
            code="600000",
            name="测试股票",
            close=10 + index,
            market_cap=None,
            pe_ttm=float(index),
            pe_static=None,
            pb=float(index),
            ps_ttm=float(index),
            pcf_ocf_ttm=float(index),
            peg=None,
            raw={},
        )
        for index in range(1, 11)
    ]

    result = analyze_stock_valuation(rows)

    assert result["score"] == 100
    assert result["level"] == "extremely_overvalued"
    assert result["level_zh"] == "极度高估"
    assert result["confidence"] > 0
    assert len(result["factors"]) == 4


def test_analyze_fund_valuation_framework_marks_pending_inputs() -> None:
    result = analyze_fund_valuation([], name="中证红利低波ETF联接")

    assert result["profile"] == "dividend_low_volatility_fund"
    assert result["score"] is None
    assert result["level"] == "unknown"
    assert "dividend_yield" in result["missing_factors"]


def test_analyze_fund_valuation_aggregates_holdings_and_confidence() -> None:
    nav_points = [
        FundNavPoint(
            date=date(2026, 4, day),
            unit_nav=1 + day / 100,
            cumulative_nav=None,
            daily_growth_pct=None,
            subscribe_status=None,
            redeem_status=None,
        )
        for day in range(1, 29)
    ]
    holdings = [
        FundHolding(
            rank=1,
            code="000651",
            name="格力电器",
            weight_pct=10.0,
            shares_10k=None,
            market_value_10k=None,
            report_date=date(2026, 3, 31),
        )
    ]
    analyses = {
        "000651": {
            "valuation": {
                "pe_ttm": 8.0,
                "pb": 2.0,
                "pe_ttm_percentile": 0.8,
                "pb_percentile": 0.6,
                "industry": {"em_industry": "家电"},
                "fundamentals": {
                    "roe_weighted": 20.0,
                    "parent_netprofit_growth_pct": 8.0,
                    "revenue_growth_pct": 5.0,
                },
                "peer_comparison": {
                    "valuation": {"percentiles": {"pe_ttm": 0.7}}
                },
                "dividend": {"dividend_yield": 0.04},
            }
        }
    }

    result = analyze_fund_valuation(
        nav_points,
        name="中证红利低波ETF",
        holdings=holdings,
        holding_analyses=analyses,
    )

    assert result["method"] == "holdings_weighted_multi_factor"
    assert result["status"] == "holdings_valuation"
    assert result["score"] == 54.83
    assert result["level"] == "fair"
    assert result["confidence"] == 0.1
    assert result["holdings"]["analyzed_holdings_weight"] == 0.1
    assert result["portfolio"]["metrics"]["roe_weighted"]["value"] == 20.0
    assert result["portfolio"]["industry_weights"][0]["industry"] == "家电"


def test_analyze_index_price_proxy() -> None:
    rows = [
        StockBar(
            date=date(2024, 1, index),
            open=float(index),
            close=float(index),
            high=float(index),
            low=float(index),
            volume=1000,
            amount=1000,
            amplitude_pct=None,
            change_pct=None,
            change_amount=None,
            turnover_pct=None,
        )
        for index in range(1, 11)
    ]

    result = analyze_index_price_proxy(rows, "H30269", "红利低波", "2.H30269")

    assert result["method"] == "index_price_percentile_proxy"
    assert result["profile"] == "index_etf"
    assert result["score"] == 100
    assert result["index"]["code"] == "H30269"
