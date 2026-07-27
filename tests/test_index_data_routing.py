from datetime import date

import pytest

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.data.eastmoney import EastmoneyError
from market_lens.types import (
    CsiIndexConstituentWeight,
    CsiIndexValuationPoint,
    FundHoldingsRoute,
    FundTrackingInfo,
)
from market_lens.valuation.index_data import (
    build_csi_fund_index_data_route,
    index_names_match,
    serialize_fund_index_data_route,
)


def tracking_info() -> FundTrackingInfo:
    return FundTrackingInfo(
        fund_code="510300",
        fund_name="沪深300ETF",
        fund_type="指数型-股票",
        index_code="000300",
        index_name="沪深300指数",
        target_etf_code=None,
        target_etf_name=None,
    )


def valuation_point(
    point_date: date,
    *,
    index_name: str = "沪深300",
    source: str = "csindex_official_valuation",
) -> CsiIndexValuationPoint:
    return CsiIndexValuationPoint(
        date=point_date,
        index_code="000300",
        index_name=index_name,
        pe_ttm=14.39,
        pe_static_total_capital=14.72,
        pe_static_calculation_capital=14.70,
        pb=None,
        dividend_yield_total_capital_pct=2.67,
        dividend_yield_calculation_capital_pct=2.68,
        source=source,
        raw={},
    )


def constituent_weights(
    report_date: date = date(2026, 6, 30),
) -> list[CsiIndexConstituentWeight]:
    return [
        CsiIndexConstituentWeight(
            rank=1,
            report_date=report_date,
            index_code="000300",
            index_name="沪深300",
            security_code="600519",
            security_name="贵州茅台",
            exchange="Shanghai",
            weight_pct=55.0,
        ),
        CsiIndexConstituentWeight(
            rank=2,
            report_date=report_date,
            index_code="000300",
            index_name="沪深300",
            security_code="300750",
            security_name="宁德时代",
            exchange="Shenzhen",
            weight_pct=45.0,
        ),
    ]


def test_csi_fund_index_route_requires_and_serializes_both_official_datasets() -> None:
    route = build_csi_fund_index_data_route(
        tracking_info(),
        [
            valuation_point(date(2026, 7, 23)),
            valuation_point(date(2026, 7, 24)),
            valuation_point(date(2026, 7, 28)),
        ],
        constituent_weights(),
        analysis_end=date(2026, 7, 24),
    )

    assert route.scope == "tracked_index_fundamentals_and_full_weights"
    assert route.valuation_as_of == date(2026, 7, 24)
    assert route.weights_as_of == date(2026, 6, 30)
    assert len(route.valuation_points) == 2
    assert route.coverage == 1.0
    assert route.scoring_eligible is False

    serialized = serialize_fund_index_data_route(route)
    assert serialized["status"] == "available"
    assert serialized["valuation_sample_size"] == 2
    assert serialized["constituent_count"] == 2
    assert serialized["latest_pe_ttm"] == 14.39
    assert serialized["latest_dividend_yield_pct"] == 2.67
    assert serialized["scoring_eligible"] is False


def test_csi_fund_index_route_rejects_tracking_name_mismatch() -> None:
    tracking = tracking_info()
    mismatched = FundTrackingInfo(
        fund_code=tracking.fund_code,
        fund_name=tracking.fund_name,
        fund_type=tracking.fund_type,
        index_code=tracking.index_code,
        index_name="中证500指数",
        target_etf_code=tracking.target_etf_code,
        target_etf_name=tracking.target_etf_name,
    )

    with pytest.raises(ValueError, match="official_index_valuation_name_mismatch"):
        build_csi_fund_index_data_route(
            mismatched,
            [valuation_point(date(2026, 7, 24))],
            constituent_weights(),
            analysis_end=date(2026, 7, 24),
        )


def test_csi_fund_index_route_rejects_future_complete_weights() -> None:
    with pytest.raises(ValueError, match="official_index_weights_after_analysis_date"):
        build_csi_fund_index_data_route(
            tracking_info(),
            [valuation_point(date(2026, 5, 30))],
            constituent_weights(report_date=date(2026, 6, 30)),
            analysis_end=date(2026, 5, 30),
        )


def test_index_name_matching_allows_only_normalized_index_suffix_difference() -> None:
    assert index_names_match("沪深 300 指数", "沪深300")
    assert index_names_match("CSI 300 Index", "CSI300")
    assert index_names_match("中证电网设备主题指数", "电网设备主题")
    assert not index_names_match("沪深300指数", "中证500")


def test_market_agent_index_route_fails_closed_before_requesting_weights() -> None:
    class FailingClient:
        def get_csi_index_valuation_history(self, index_code: str) -> object:
            raise EastmoneyError(f"no official route for {index_code}")

        def get_csi_index_full_weights(self, index_code: str) -> object:
            raise AssertionError("weights must not be requested after valuation route failure")

    holdings_route = FundHoldingsRoute(
        holdings=[],
        source="unavailable",
        scope="unresolved_index_fund",
        as_of=None,
        coverage=0.0,
        tracking=tracking_info(),
    )
    agent = MarketAnalysisAgent(FailingClient())  # type: ignore[arg-type]

    route = agent._load_fund_index_data_route(
        holdings_route,
        analysis_end=date(2026, 7, 24),
    )

    assert route.scope == "unavailable"
    assert route.scoring_eligible is False
    assert route.fallback_reasons == (
        "official_index_valuation_unavailable: no official route for 000300",
    )
