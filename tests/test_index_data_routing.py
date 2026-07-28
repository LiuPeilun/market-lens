from datetime import date, timedelta

import pytest

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.data.eastmoney import EastmoneyError
from market_lens.types import (
    CsiIndexConstituentWeight,
    CsiIndexValuationPoint,
    FundHoldingsRoute,
    FundNavPoint,
    FundProductInfo,
    FundTrackingInfo,
)
from market_lens.valuation.index_data import (
    analyze_csi_index_valuation,
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


def valuation_history(
    count: int,
    *,
    end: date = date(2026, 7, 24),
) -> list[CsiIndexValuationPoint]:
    start = end - timedelta(days=count - 1)
    return [
        valuation_point(start + timedelta(days=index))
        for index in range(count)
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


def test_official_index_valuation_scores_only_long_pe_history() -> None:
    route = build_csi_fund_index_data_route(
        tracking_info(),
        valuation_history(600),
        constituent_weights(),
        analysis_end=date(2026, 7, 24),
    )

    result = analyze_csi_index_valuation(
        route,
        fund_name="沪深300ETF",
        analysis_end=date(2026, 7, 24),
    )

    assert result["method"] == "official_index_fundamental_percentile"
    assert result["profile"] == "csi_index_fundamental"
    assert result["score"] == 100.0
    assert result["factor_coverage"] == 0.6
    assert result["confidence"] <= 0.6
    assert result["missing_factors"] == [
        "index_pb_percentile",
        "index_dividend_yield_percentile",
    ]
    assert result["factors"][0]["sample_size"] == 600  # type: ignore[index]


def test_dividend_low_vol_index_has_lower_coverage_and_confidence_cap() -> None:
    route = build_csi_fund_index_data_route(
        tracking_info(),
        valuation_history(600),
        constituent_weights(),
        analysis_end=date(2026, 7, 24),
    )

    result = analyze_csi_index_valuation(
        route,
        fund_name="中证红利低波ETF",
        analysis_end=date(2026, 7, 24),
    )

    assert result["profile"] == "csi_dividend_low_volatility_index"
    assert result["factor_coverage"] == 0.35
    assert result["confidence"] <= 0.45


def test_official_index_valuation_requires_two_year_history() -> None:
    route = build_csi_fund_index_data_route(
        tracking_info(),
        valuation_history(503),
        constituent_weights(),
        analysis_end=date(2026, 7, 24),
    )

    result = analyze_csi_index_valuation(
        route,
        fund_name="沪深300ETF",
        analysis_end=date(2026, 7, 24),
    )

    assert result["score"] is None
    assert result["confidence"] == 0.0
    assert result["status"] == "official_index_history_insufficient"


def test_market_agent_prefers_official_index_fundamentals() -> None:
    tracking = tracking_info()
    holdings_route = FundHoldingsRoute(
        holdings=[],
        source="csindex_official",
        scope="tracked_index_top10",
        as_of=date(2026, 6, 30),
        coverage=0.4,
        tracking=tracking,
    )

    class OfficialClient:
        def get_fund_product_info(self, code: str) -> FundProductInfo:
            return FundProductInfo(
                fund_code=code,
                fund_name="沪深300ETF",
                fund_type="指数型-股票",
                establishment_date=date(2012, 5, 4),
                scale_report_date=date(2026, 6, 30),
                period_end_net_assets_cny=100_000_000_000.0,
                management_fee_pct=0.15,
                custody_fee_pct=0.05,
                sales_service_fee_pct=None,
                benchmark="沪深300指数",
                raw={},
            )

        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [
                FundNavPoint(
                    date=start,
                    unit_nav=4.0,
                    cumulative_nav=4.0,
                    daily_growth_pct=None,
                    subscribe_status=None,
                    redeem_status=None,
                ),
                FundNavPoint(
                    date=end,
                    unit_nav=4.2,
                    cumulative_nav=4.2,
                    daily_growth_pct=5.0,
                    subscribe_status=None,
                    redeem_status=None,
                ),
            ]

        def get_fund_name(self, code: str) -> str:
            return "沪深300ETF"

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            return holdings_route

        def get_csi_index_valuation_history(
            self,
            index_code: str,
        ) -> list[CsiIndexValuationPoint]:
            return valuation_history(600)

        def get_csi_index_full_weights(
            self,
            index_code: str,
        ) -> list[CsiIndexConstituentWeight]:
            return constituent_weights()

        def search_assets(
            self,
            keyword: str,
            *,
            limit: int,
            include_indexes: bool,
        ) -> list[object]:
            return []

    result = MarketAnalysisAgent(OfficialClient()).analyze(  # type: ignore[arg-type]
        "fund",
        "510300",
        date(2026, 1, 2),
        date(2026, 7, 24),
    )

    assert result["valuation"]["method"] == "official_index_fundamental_percentile"
    assert result["valuation"]["score"] == 100.0
    assert result["valuation"]["product_data"]["profile"] == "etf"
    assert result["index_data_route"]["scoring_eligible"] is True
    assert result["assessment"]["dimensions"]["valuation"]["model"] == (
        "csi_index_fundamental_v1"
    )
    assert result["assessment"]["dimensions"]["valuation"]["confidence"] <= 0.6
    assert not any(
        note.startswith("Holding-level valuation uses")
        for note in result["notes"]
    )
    assert any(
        "used only for the separate underlying-quality dimension" in note
        for note in result["notes"]
    )
