from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from market_lens.backtesting.collector import (
    StockBacktestCollector,
    financials_available_as_of,
    generate_rebalance_dates,
)
from market_lens.backtesting.models import BacktestDataError, snapshot_from_analysis
from market_lens.backtesting.universe import (
    IndustryPeriod,
    MembershipPeriod,
    StockUniverseEntry,
    StockUniverseManifest,
)
from market_lens.data.eastmoney import parse_stock_financial_indicator
from market_lens.types import StockBar, StockIndustryValuationSnapshot, StockValuationPoint


class FakeCollectorClient:
    def __init__(self) -> None:
        self.industry_calls = 0

    def get_index_history(self, quote_id, start, end):
        del quote_id, start, end
        return [make_bar(date(2026, 1, 1), 100.0), make_bar(date(2026, 1, 30), 110.0)]

    def get_stock_history(self, code, start, end):
        del code, start, end
        return [
            make_bar(date(2026, 1, 29), 100.0),
            make_bar(date(2026, 1, 30), 101.0),
            make_bar(date(2026, 2, 2), 102.0),
        ]

    def get_tencent_stock_history(self, code, start, end):
        return self.get_stock_history(code, start, end)

    def get_stock_valuation(self, code):
        return [
            make_valuation(code, date(2026, 1, 29), 10.0),
            make_valuation(code, date(2026, 1, 30), 11.0),
        ]

    def get_stock_financial_indicators(self, code):
        del code
        return [
            parse_stock_financial_indicator(
                {
                    "REPORT_DATE": "2025-12-31",
                    "NOTICE_DATE": "2026-01-20",
                    "ORG_TYPE": "通用",
                    "ROEJQ": 12.0,
                    "ROIC": 10.0,
                }
            )
        ]

    def get_stock_industry_valuation_snapshot(
        self, board_code, trade_date, board_name=None
    ):
        self.industry_calls += 1
        row = make_valuation("600519", trade_date, 11.0)
        return StockIndustryValuationSnapshot(
            date=trade_date,
            board_code=board_code,
            board_name=board_name,
            original_board_code="OLD",
            rows=(row,),
        )


class FakeBenchmarkFallbackClient(FakeCollectorClient):
    def get_index_history(self, quote_id, start, end):
        del quote_id, start, end
        raise ValueError("primary unavailable")

    def get_sina_index_history(self, index_code, quote_id, start, end):
        del index_code, quote_id, start, end
        return [make_bar(date(2026, 1, 1), 100.0), make_bar(date(2026, 1, 30), 110.0)]


class FakeStockFallbackClient(FakeCollectorClient):
    def get_tencent_stock_history(self, code, start, end):
        del code, start, end
        raise ValueError("primary unavailable")

    def get_stock_history(self, code, start, end):
        del code, start, end
        return [
            make_bar(date(2026, 1, 29), 100.0),
            make_bar(date(2026, 1, 30), 101.0),
            make_bar(date(2026, 2, 2), 102.0),
        ]


def test_collector_builds_verified_month_end_snapshot() -> None:
    client = FakeCollectorClient()
    collector = StockBacktestCollector(client)  # type: ignore[arg-type]

    dataset = collector.collect(
        verified_manifest(),
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        horizons=(1,),
    )

    assert len(dataset["analyses"]) == 1
    analysis = dataset["analyses"][0]
    assert analysis["as_of"] == "2026-01-30"
    assert analysis["backtest_provenance"]["point_in_time_verified"] is True
    assert analysis["backtest_provenance"]["financial_rule"].startswith(
        "notice_date_required"
    )
    assert snapshot_from_analysis(analysis).provenance == "stock-point-in-time-v2"
    assert dataset["prices"]["stock:600519"][-1]["date"] == "2026-02-02"
    assert dataset["collection"]["diagnostics"][0]["financial_rows"] == 1
    assert client.industry_calls == 1


def test_collector_falls_back_to_sina_benchmark_history() -> None:
    dataset = StockBacktestCollector(FakeBenchmarkFallbackClient()).collect(  # type: ignore[arg-type]
        verified_manifest(),
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        horizons=(1,),
    )

    assert dataset["collection"]["benchmark_source"] == "sina_index_history"


def test_collector_falls_back_to_eastmoney_stock_history() -> None:
    dataset = StockBacktestCollector(FakeStockFallbackClient()).collect(  # type: ignore[arg-type]
        verified_manifest(),
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        horizons=(1,),
    )

    diagnostic = dataset["collection"]["diagnostics"][0]
    assert diagnostic["price_source"] == "eastmoney_stock_history"
    assert len(diagnostic["price_sha256"]) == 64
    assert diagnostic["price_request_ranges"] == [
        {"start": "2025-12-18", "end": "2026-03-03"}
    ]


def test_collector_manifest_rejects_survivorship_biased_universe() -> None:
    manifest = replace(verified_manifest(), includes_delisted=False)

    with pytest.raises(BacktestDataError, match="must include delisted"):
        manifest.validate_for_collection([date(2026, 1, 31)])


def test_financial_filter_requires_known_notice_date() -> None:
    available = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31",
            "NOTICE_DATE": "2026-01-20",
            "ORG_TYPE": "通用",
        }
    )
    future = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31",
            "NOTICE_DATE": "2026-02-20",
            "ORG_TYPE": "通用",
        }
    )
    unknown = parse_stock_financial_indicator(
        {"REPORT_DATE": "2025-09-30", "ORG_TYPE": "通用"}
    )

    result = financials_available_as_of(
        [available, future, unknown], date(2026, 1, 31)
    )

    assert result == [available]


def test_generate_rebalance_dates_uses_completed_months_and_quarters() -> None:
    assert generate_rebalance_dates(
        date(2026, 1, 15), date(2026, 4, 15), "monthly"
    ) == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert generate_rebalance_dates(
        date(2026, 1, 1), date(2026, 7, 1), "quarterly"
    ) == [date(2026, 3, 31), date(2026, 6, 30)]


def verified_manifest() -> StockUniverseManifest:
    return StockUniverseManifest(
        schema_version="stock-universe-1",
        name="Verified fixture universe",
        source="fixture://historical-membership",
        point_in_time_verified=True,
        includes_delisted=True,
        historical_industry_verified=True,
        entries=(
            StockUniverseEntry(
                code="600519",
                name="贵州茅台",
                memberships=(MembershipPeriod(date(2020, 1, 1), None),),
                industries=(
                    IndustryPeriod(
                        start=date(2020, 1, 1),
                        end=None,
                        em_industry="酿酒行业",
                        csrc_industry="酒、饮料和精制茶制造业",
                        source="fixture://historical-industry",
                    ),
                ),
            ),
        ),
    )


def make_bar(day: date, close: float) -> StockBar:
    return StockBar(
        date=day,
        open=close,
        close=close,
        high=close,
        low=close,
        volume=100.0,
        amount=1000.0,
        amplitude_pct=0.0,
        change_pct=0.0,
        change_amount=0.0,
        turnover_pct=1.0,
    )


def make_valuation(code: str, day: date, value: float) -> StockValuationPoint:
    return StockValuationPoint(
        date=day,
        code=code,
        name="贵州茅台",
        close=value,
        market_cap=None,
        pe_ttm=value,
        pe_static=None,
        pb=value / 2,
        ps_ttm=value / 3,
        pcf_ocf_ttm=value / 4,
        peg=None,
        raw={},
        board_code="BK0477",
        board_name="酿酒行业",
        original_board_code="OLD",
    )
