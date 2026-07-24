from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from market_lens.data.eastmoney import parse_stock_financial_indicator
from market_lens.types import (
    FundHolding,
    FundHoldingsRoute,
    FundNavPoint,
    FundProductInfo,
    FundTrackingInfo,
    StockBar,
    StockIndustryValuationSnapshot,
    StockProfile,
    StockValuationPoint,
)
from market_lens.valuation.analyzer import analyze_fund, analyze_stock
from market_lens.valuation.assessment import build_fund_assessment
from market_lens.valuation.confidence import (
    calculate_confidence,
    conservative_overall_confidence,
)
from market_lens.valuation.fund_product import (
    calculate_tracking_metrics,
    classify_fund_product,
)
from market_lens.valuation.scoring import (
    FactorObservation,
    evaluate_factor,
    score_dimension,
)
from market_lens.valuation.scoring_config import (
    MODEL_VERSION,
    SCHEMA_VERSION,
    FactorDefinition,
)


def factor_definition(
    key: str = "pe_ttm",
    *,
    weight: float = 1.0,
    minimum_sample_size: int = 3,
    positive_only: bool = True,
    core: bool = False,
    direction: str = "higher_value_higher_score",
) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        name=key,
        category="valuation",
        unit="multiple",
        weight=weight,
        direction=direction,  # type: ignore[arg-type]
        normalization="historical_percentile",
        minimum_sample_size=minimum_sample_size,
        positive_only=positive_only,
        core=core,
    )


def observation(
    value: float | None,
    *,
    history: tuple[float | None, ...] = (1.0, 2.0, 3.0),
    status: str = "available",
    coverage: float = 1.0,
) -> FactorObservation:
    return FactorObservation(
        value=value,
        history=history,
        source="fixture",
        source_as_of=date(2026, 7, 20),
        status=status,  # type: ignore[arg-type]
        coverage=coverage,
    )


def test_factor_definition_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="weight"):
        factor_definition(weight=0)
    with pytest.raises(ValueError, match="anchor"):
        FactorDefinition(
            key="yield",
            name="yield",
            category="valuation",
            unit="ratio",
            weight=1.0,
            direction="higher_value_higher_score",
            normalization="linear_anchor",
        )


def test_historical_percentile_factor_scores_boundaries() -> None:
    definition = factor_definition()

    low = evaluate_factor(definition, observation(1.0))
    high = evaluate_factor(definition, observation(3.0))

    assert low.score == pytest.approx(33.333333)
    assert high.score == 100.0
    assert low.eligible is True


def test_non_positive_valuation_is_available_but_ineligible() -> None:
    result = evaluate_factor(factor_definition(), observation(-1.0, history=(-1.0, 2.0, 3.0)))

    assert result.status == "available"
    assert result.eligible is False
    assert result.score is None
    assert "non_positive_value" in result.warnings


@pytest.mark.parametrize("status", ["missing", "stale", "error", "invalid"])
def test_unavailable_factor_statuses_never_score(status: str) -> None:
    result = evaluate_factor(factor_definition(), observation(2.0, status=status))

    assert result.status == status
    assert result.eligible is False
    assert result.score is None


def test_insufficient_sample_keeps_available_status() -> None:
    result = evaluate_factor(
        factor_definition(minimum_sample_size=4),
        observation(2.0, history=(1.0, 2.0, 3.0)),
    )

    assert result.status == "available"
    assert result.eligible is False
    assert "insufficient_sample:3<4" in result.warnings


def test_dimension_reweights_available_factors() -> None:
    definitions = (
        factor_definition("pe", weight=0.6),
        factor_definition("pb", weight=0.4),
    )
    result = score_dimension(
        definitions,
        {
            "pe": observation(2.0),
            "pb": observation(None, status="missing", coverage=0.0),
        },
        minimum_effective_weight=0.5,
    )

    assert result["score"] == pytest.approx(66.67)
    assert result["weight_coverage"] == 0.6
    assert result["factors"][0]["effective_weight"] == 1.0
    assert result["factors"][1]["effective_weight"] == 0.0


def test_core_factor_gate_and_minimum_weight_both_apply() -> None:
    definitions = (
        factor_definition("core", weight=0.4, core=True),
        factor_definition("other", weight=0.6),
    )
    missing_core = score_dimension(
        definitions,
        {
            "core": observation(None, status="missing", coverage=0.0),
            "other": observation(2.0),
        },
    )
    low_weight = score_dimension(
        definitions,
        {
            "core": observation(2.0),
            "other": observation(None, status="missing", coverage=0.0),
        },
        minimum_effective_weight=0.5,
    )

    assert missing_core["weight_coverage"] == 0.6
    assert missing_core["score"] is None
    assert "core_factors_unavailable:core" in missing_core["warnings"]
    assert low_weight["weight_coverage"] == 0.4
    assert low_weight["score"] is None


def test_not_applicable_factor_does_not_reduce_weight_coverage() -> None:
    definitions = (
        factor_definition("applicable", weight=0.5),
        factor_definition("not_applicable", weight=0.5),
    )
    result = score_dimension(
        definitions,
        {
            "applicable": observation(2.0),
            "not_applicable": observation(None, status="not_applicable", coverage=0.0),
        },
    )

    assert result["weight_coverage"] == 1.0
    assert result["score"] == pytest.approx(66.67)


def test_scoring_is_reproducible_for_same_version_and_input() -> None:
    definitions = (factor_definition(),)
    observations = {"pe_ttm": observation(2.0)}

    first = score_dimension(definitions, observations)
    second = score_dimension(definitions, observations)

    assert MODEL_VERSION == "valuation-v2.2.0-fund-product-models"
    assert first == second


def test_confidence_uses_geometric_components_caps_and_conservative_overall() -> None:
    detail = calculate_confidence(
        {"source": 1.0, "coverage": 0.25},
        caps=[("proxy", 0.4)],
    )
    overall = conservative_overall_confidence(
        {
            "valuation": {"confidence": 0.8},
            "quality": {"confidence": 0.35},
            "product": None,
        }
    )

    assert detail["score"] == 0.4
    assert detail["caps"] == [{"reason": "proxy", "limit": 0.4}]
    assert overall == 0.35


def test_stock_analysis_exposes_v2_assessment_and_legacy_fields() -> None:
    start = date(2025, 1, 1)
    valuations = [make_valuation(start + timedelta(days=index), index + 1) for index in range(252)]
    bars = [make_bar(item.date, item.close or 0.0) for item in valuations]

    result = analyze_stock(
        "600000",
        bars,
        valuations,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assessment = result["assessment"]
    assert assessment["schema_version"] == SCHEMA_VERSION
    assert assessment["model_version"] == MODEL_VERSION
    assert assessment["dimensions"]["valuation"]["score"] == 100.0
    assert assessment["dimensions"]["valuation"]["sample_adequacy"] == 0.1575
    assert assessment["dimensions"]["valuation"]["confidence"] < 1.0
    quality = assessment["dimensions"]["quality"]
    assert quality is not None
    assert quality["score"] is None
    assert quality["confidence"] == 0.0
    assert assessment["dimensions"]["product"] is None
    assert assessment["attractiveness"] is None
    assert result["valuation"]["score"] == 100.0
    assert all("raw" not in factor for factor in assessment["dimensions"]["valuation"]["factors"])


def test_fund_analysis_exposes_pending_assessment_with_model_weights() -> None:
    nav_points = [
        FundNavPoint(
            date=date(2026, 6, 1) + timedelta(days=index),
            unit_nav=1 + index / 100,
            cumulative_nav=None,
            daily_growth_pct=None,
            subscribe_status=None,
            redeem_status=None,
        )
        for index in range(30)
    ]

    result = analyze_fund(
        "000001",
        nav_points,
        name="Generic Fund",
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assessment = result["assessment"]
    valuation = assessment["dimensions"]["valuation"]
    assert valuation["score"] is None
    assert valuation["confidence"] == 0.0
    assert assessment["dimensions"]["quality"]["score"] is None
    assert assessment["dimensions"]["product"]["score"] is None
    assert assessment["overall_confidence"] == 0.0
    assert sum(factor["weight"] for factor in valuation["factors"]) == 1.0
    assert all(factor["status"] == "missing" for factor in valuation["factors"])


def test_fund_analysis_uses_dividend_reinvested_performance() -> None:
    nav_points = [
        FundNavPoint(date(2020, 1, 1), 1.0, 1.0, None, None, None),
        FundNavPoint(date(2021, 1, 1), 2.0, 2.0, None, None, None),
        FundNavPoint(date(2022, 1, 1), 1.5, 2.0, None, None, None),
        FundNavPoint(date(2023, 1, 1), 1.5, 2.5, None, None, None),
    ]

    result = analyze_fund(
        "000001",
        nav_points,
        name="Distribution Fund",
        retrieved_at=datetime(2023, 1, 1, tzinfo=UTC),
    )

    assert result["performance"]["basis"] == "dividend_reinvested_nav"
    assert result["performance"]["total_return"] == pytest.approx(5 / 3)
    assert result["performance"]["max_drawdown"] == 0.0


def test_index_proxy_assessment_standardizes_factors_and_applies_cap() -> None:
    result = {
        "asset_type": "fund",
        "as_of": "2026-07-20",
        "data_source": "exchange_price_history",
        "valuation": {
            "profile": "index_etf",
            "status": "proxy_valuation",
            "score": 50.0,
            "confidence": 0.55,
            "factor_coverage": 1.0,
            "factors": [
                {
                    "key": "index_price_percentile",
                    "name": "Index price percentile",
                    "weight": 1.0,
                    "value": 4000.0,
                    "score": 50.0,
                }
            ],
            "missing_factors": [],
            "index": {"as_of": "2026-07-20", "sample_size": 1200},
            "holdings_route": {"source": "unavailable", "scope": "unavailable"},
            "product_data": {"diagnostic": {"status": "available"}},
        },
    }

    assessment = build_fund_assessment(
        result,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    dimension = assessment["dimensions"]["valuation"]

    assert dimension["score"] == 50.0
    assert dimension["confidence"] <= 0.55
    assert dimension["factors"][0]["normalization"] == "legacy_valuation_rule"
    assert dimension["factors"][0]["source"] == "tracked_index_price_history"
    assert assessment["confidence_detail"]["score"] <= 0.6
    assert assessment["attractiveness"] is None


@pytest.mark.parametrize(
    ("name", "index_code", "target_etf_code", "expected_profile"),
    [
        ("沪深300ETF", "000300", None, "etf"),
        ("沪深300ETF联接A", "000300", "510300", "etf_linked"),
        ("沪深300指数增强", "000300", None, "index_fund"),
        ("主动成长混合", None, None, "active_fund"),
    ],
)
def test_fund_product_routing_distinguishes_product_types(
    name: str,
    index_code: str | None,
    target_etf_code: str | None,
    expected_profile: str,
) -> None:
    tracking = FundTrackingInfo(
        fund_code="000001",
        fund_name=name,
        fund_type="指数型-股票" if index_code else "混合型-灵活",
        index_code=index_code,
        index_name="沪深300指数" if index_code else None,
        target_etf_code=target_etf_code,
        target_etf_name="沪深300ETF" if target_etf_code else None,
    )
    route = FundHoldingsRoute(
        holdings=[],
        source="unavailable",
        scope="unavailable",
        as_of=None,
        coverage=0.0,
        tracking=tracking,
    )

    routing = classify_fund_product("000001", name, None, route)

    assert routing["profile"] == expected_profile


def test_tracking_metrics_align_returns_and_apply_benchmark_exposure() -> None:
    nav_points, benchmark_bars = tracking_fixture(size=253, exposure=0.95)

    metrics = calculate_tracking_metrics(
        nav_points,
        benchmark_bars,
        profile="etf_linked",
        benchmark="沪深300指数增长率*95%+银行活期存款税后利率*5%",
    )

    assert metrics["status"] == "available"
    assert metrics["sample_size"] == 252
    assert metrics["benchmark_exposure"] == 0.95
    assert metrics["tracking_error_annualized"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["tracking_deviation_annualized"] == pytest.approx(0.0, abs=1e-12)
    assert "cash_benchmark_component_assumed_zero_return" in metrics["warnings"]


def test_tracking_metrics_use_official_daily_growth_not_cumulative_nav_ratio() -> None:
    days = [date(2026, 1, day) for day in range(1, 4)]
    nav_points = [
        FundNavPoint(days[0], 1.0, 3.0, None, None, None),
        FundNavPoint(days[1], 1.1, 3.02, 10.0, None, None),
        FundNavPoint(days[2], 1.21, 3.04, 10.0, None, None),
    ]
    benchmark_bars = [
        make_bar(days[0], 100.0),
        make_bar(days[1], 110.0),
        make_bar(days[2], 121.0),
    ]

    metrics = calculate_tracking_metrics(
        nav_points,
        benchmark_bars,
        profile="etf",
        benchmark="Test Index",
    )

    assert metrics["sample_size"] == 2
    assert metrics["tracking_error_annualized"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["tracking_deviation_annualized"] == pytest.approx(0.0, abs=1e-12)


def test_etf_assessment_scores_product_and_underlying_quality_separately() -> None:
    nav_points, benchmark_bars = tracking_fixture(size=253, exposure=1.0)
    report_date = nav_points[-1].date - timedelta(days=30)
    holding = FundHolding(
        rank=1,
        code="600000",
        name="Holding",
        weight_pct=10.0,
        shares_10k=None,
        market_value_10k=None,
        report_date=report_date,
    )
    tracking = FundTrackingInfo(
        fund_code="510300",
        fund_name="沪深300ETF",
        fund_type="指数型-股票",
        index_code="000300",
        index_name="沪深300指数",
        target_etf_code=None,
        target_etf_name=None,
    )
    route = FundHoldingsRoute(
        holdings=[holding],
        source="csindex_official",
        scope="tracked_index_top10",
        as_of=report_date,
        coverage=0.1,
        tracking=tracking,
    )
    product = FundProductInfo(
        fund_code="510300",
        fund_name="沪深300ETF",
        fund_type="指数型-股票",
        establishment_date=date(2012, 5, 4),
        scale_report_date=report_date,
        period_end_net_assets_cny=10_000_000_000,
        management_fee_pct=0.15,
        custody_fee_pct=0.05,
        sales_service_fee_pct=None,
        benchmark="沪深300指数",
        raw={},
    )
    result = analyze_fund(
        "510300",
        nav_points,
        name="沪深300ETF",
        holdings=[holding],
        holding_analyses={
            "600000": {
                "assessment": {"dimensions": {"quality": {"score": 80.0}}}
            }
        },
        product_info=product,
        holdings_route=route,
        benchmark_bars=benchmark_bars,
        data_source="fund_nav_history",
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assessment = result["assessment"]
    assert assessment["profile"] == "etf"
    assert assessment["dimensions"]["product"]["score"] is not None
    assert assessment["dimensions"]["product"]["confidence"] <= 0.8
    assert assessment["dimensions"]["quality"]["score"] == 80.0
    assert assessment["dimensions"]["valuation"]["score"] is None
    assert assessment["dimensions"]["product"]["model"] == "etf_product_v1"
    assert assessment["attractiveness"] is None


def test_active_fund_product_scores_without_tracking_factors() -> None:
    nav_points, _ = tracking_fixture(size=30, exposure=1.0)
    report_date = nav_points[-1].date
    product = FundProductInfo(
        fund_code="000001",
        fund_name="主动成长混合",
        fund_type="混合型-灵活",
        establishment_date=date(2001, 1, 1),
        scale_report_date=report_date,
        period_end_net_assets_cny=3_000_000_000,
        management_fee_pct=1.2,
        custody_fee_pct=0.2,
        sales_service_fee_pct=None,
        benchmark="--",
        raw={},
    )

    result = analyze_fund(
        "000001",
        nav_points,
        name=product.fund_name,
        product_info=product,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assessment = result["assessment"]
    product_dimension = assessment["dimensions"]["product"]
    assert assessment["profile"] == "active_fund"
    assert product_dimension["score"] is not None
    assert {factor["key"] for factor in product_dimension["factors"]} == {
        "total_annual_fee_pct",
        "period_end_net_assets_cny",
    }


def test_index_product_does_not_score_with_insufficient_tracking_sample() -> None:
    nav_points, benchmark_bars = tracking_fixture(size=60, exposure=1.0)
    report_date = nav_points[-1].date
    tracking = FundTrackingInfo(
        fund_code="510300",
        fund_name="沪深300ETF",
        fund_type="指数型-股票",
        index_code="000300",
        index_name="沪深300指数",
        target_etf_code=None,
        target_etf_name=None,
    )
    route = FundHoldingsRoute(
        holdings=[],
        source="csindex_official",
        scope="tracked_index_top10",
        as_of=report_date,
        coverage=0.0,
        tracking=tracking,
    )
    product = FundProductInfo(
        fund_code="510300",
        fund_name="沪深300ETF",
        fund_type="指数型-股票",
        establishment_date=date(2012, 5, 4),
        scale_report_date=report_date,
        period_end_net_assets_cny=10_000_000_000,
        management_fee_pct=0.15,
        custody_fee_pct=0.05,
        sales_service_fee_pct=None,
        benchmark="沪深300指数",
        raw={},
    )

    result = analyze_fund(
        "510300",
        nav_points,
        name=product.fund_name,
        product_info=product,
        holdings_route=route,
        benchmark_bars=benchmark_bars,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    product_dimension = result["assessment"]["dimensions"]["product"]
    factors = {factor["key"]: factor for factor in product_dimension["factors"]}
    assert product_dimension["score"] is None
    assert factors["tracking_error_annualized"]["sample_size"] == 59
    assert factors["tracking_error_annualized"]["eligible"] is False


@pytest.mark.parametrize(
    ("org_type", "expected_profile"),
    [
        ("通用", "generic_non_financial"),
        ("银行", "bank"),
        ("保险", "insurance"),
        ("证券", "securities"),
    ],
)
def test_stock_industry_models_route_and_score_quality(
    org_type: str,
    expected_profile: str,
) -> None:
    valuations = model_valuation_history(300)
    bars = [make_bar(item.date, item.close or 0.0) for item in valuations]
    result = analyze_stock(
        "600000",
        bars,
        valuations,
        profile=StockProfile(
            code="600000",
            name="Test",
            em_industry="制造业",
            csrc_industry="制造业",
            security_type="A share",
            raw={},
        ),
        financials=model_financial_history(org_type),
        industry_valuation=model_industry_snapshot(valuations[-1].date),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assessment = result["assessment"]
    quality = assessment["dimensions"]["quality"]
    valuation = assessment["dimensions"]["valuation"]
    assert assessment["profile"] == expected_profile
    assert assessment["routing"]["reason"] == "financial_org_type"
    assert quality["score"] is not None
    assert quality["confidence"] > 0
    assert quality["model"] == f"{expected_profile}_quality_v1"
    assert valuation["model"] == f"{expected_profile}_valuation_v1"
    assert assessment["dimensions"]["product"] is None
    assert assessment["attractiveness"] is None


def test_stock_model_routing_uses_industry_only_as_fallback() -> None:
    valuations = model_valuation_history(300)
    result = analyze_stock(
        "600000",
        [make_bar(item.date, item.close or 0.0) for item in valuations],
        valuations,
        profile=StockProfile(
            code="600000",
            name="Test Bank",
            em_industry="银行",
            csrc_industry="货币金融服务",
            security_type="A share",
            raw={},
        ),
        financials=[],
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assessment = result["assessment"]
    assert assessment["profile"] == "bank"
    assert assessment["routing"]["reason"] == "industry_classification_fallback"
    assert "financial_org_type_unavailable" in assessment["routing"]["warnings"]
    assert assessment["dimensions"]["quality"]["score"] is None


def test_financial_org_type_wins_over_conflicting_industry_classification() -> None:
    valuations = model_valuation_history(300)
    result = analyze_stock(
        "600000",
        [make_bar(item.date, item.close or 0.0) for item in valuations],
        valuations,
        profile=StockProfile(
            code="600000",
            name="Conflict",
            em_industry="保险",
            csrc_industry="保险业",
            security_type="A share",
            raw={},
        ),
        financials=model_financial_history("银行"),
        industry_valuation=model_industry_snapshot(valuations[-1].date),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    routing = result["assessment"]["routing"]
    assert routing["profile"] == "bank"
    assert routing["reason"] == "financial_org_type"
    assert "financial_industry_classification_conflict:bank!=insurance" in routing["warnings"]


def test_insurance_model_does_not_use_industry_percentiles_without_pev() -> None:
    valuations = model_valuation_history(300)
    result = analyze_stock(
        "601318",
        [make_bar(item.date, item.close or 0.0) for item in valuations],
        valuations,
        financials=model_financial_history("保险"),
        industry_valuation=model_industry_snapshot(valuations[-1].date),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    valuation = result["assessment"]["dimensions"]["valuation"]
    assert [factor["key"] for factor in valuation["factors"]] == ["pb", "pe_ttm"]
    assert "insurance_pev_unavailable" in valuation["warnings"]


def test_industry_factors_require_ten_valid_samples() -> None:
    valuations = model_valuation_history(300)
    result = analyze_stock(
        "600000",
        [make_bar(item.date, item.close or 0.0) for item in valuations],
        valuations,
        financials=model_financial_history("通用"),
        industry_valuation=model_industry_snapshot(valuations[-1].date, size=9),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    factors = {
        factor["key"]: factor
        for factor in result["assessment"]["dimensions"]["valuation"]["factors"]
    }
    for key in ("industry_pe_ttm", "industry_pb"):
        assert factors[key]["status"] == "available"
        assert factors[key]["eligible"] is False
        assert factors[key]["sample_size"] == 9
        assert factors[key]["minimum_sample_size"] == 10
        assert "insufficient_industry_sample" in factors[key]["warnings"]


def test_quality_changes_do_not_rewrite_valuation_dimension() -> None:
    valuations = model_valuation_history(300)
    bars = [make_bar(item.date, item.close or 0.0) for item in valuations]
    high_quality = model_financial_history("通用")
    low_quality = [
        replace(
            row,
            roe_weighted=5.0,
            roic_pct=5.0,
            parent_netprofit_growth_pct=-20.0,
            revenue_growth_pct=-10.0,
        )
        for row in high_quality
    ]
    common = {
        "industry_valuation": model_industry_snapshot(valuations[-1].date),
        "retrieved_at": datetime(2026, 7, 21, tzinfo=UTC),
    }

    high_result = analyze_stock(
        "600000", bars, valuations, financials=high_quality, **common
    )
    low_result = analyze_stock(
        "600000", bars, valuations, financials=low_quality, **common
    )

    high_assessment = high_result["assessment"]
    low_assessment = low_result["assessment"]
    assert high_assessment["dimensions"]["valuation"] == low_assessment["dimensions"]["valuation"]
    high_quality_score = high_assessment["dimensions"]["quality"]["score"]
    low_quality_score = low_assessment["dimensions"]["quality"]["score"]
    assert high_quality_score > low_quality_score
    assert high_result["valuation"]["score"] == low_result["valuation"]["score"]


def model_valuation_history(size: int) -> list[StockValuationPoint]:
    start = date(2025, 1, 1)
    return [make_valuation(start + timedelta(days=index), index + 1) for index in range(size)]


def tracking_fixture(
    *,
    size: int,
    exposure: float,
) -> tuple[list[FundNavPoint], list[StockBar]]:
    start = date(2025, 10, 1)
    benchmark_level = 1000.0
    fund_level = 1.0
    nav_points: list[FundNavPoint] = []
    benchmark_bars: list[StockBar] = []
    for index in range(size):
        if index:
            benchmark_level *= 1.001
            fund_level *= 1 + 0.001 * exposure
        day = start + timedelta(days=index)
        nav_points.append(
            FundNavPoint(
                date=day,
                unit_nav=fund_level,
                cumulative_nav=fund_level,
                daily_growth_pct=None,
                subscribe_status=None,
                redeem_status=None,
            )
        )
        benchmark_bars.append(make_bar(day, benchmark_level))
    return nav_points, benchmark_bars


def model_financial_history(org_type: str) -> list:
    rows = []
    for index, year in enumerate(range(2020, 2025), start=1):
        row = {
            "REPORT_DATE": f"{year}-12-31",
            "NOTICE_DATE": f"{year + 1}-03-31",
            "REPORT_TYPE": "年报",
            "ORG_TYPE": org_type,
            "ROEJQ": 8 + index,
            "ROEKCJQ": 7.5 + index,
            "PARENTNETPROFITTZ": -5 + index * 4,
            "TOTALOPERATEREVETZ": -2 + index * 3,
        }
        if org_type == "通用":
            row.update({"ROIC": 9 + index})
        elif org_type == "银行":
            row.update(
                {
                    "NET_INTEREST_MARGIN": 1.5 + index / 10,
                    "NET_INTEREST_SPREAD": 1.4 + index / 10,
                    "NONPERLOAN": 1.4 - index / 20,
                    "BLDKBBL": 220 + index * 20,
                    "NEWCAPITALADER": 13 + index / 2,
                    "FIRST_ADEQUACY_RATIO": 11 + index / 2,
                    "HXYJBCZL": 9 + index / 2,
                }
            )
        elif org_type == "保险":
            row.update(
                {
                    "SOLVENCY_AR": 160 + index * 5,
                    "NBV_LIFE": 10_000_000_000 * (1 + index / 10),
                    "NBV_RATE": 18 + index * 2,
                }
            )
        elif org_type == "证券":
            row.update(
                {
                    "RISK_COVERAGE": 150 + index * 10,
                    "LIQUIDITY_COVERAGE_RATIO": 120 + index * 5,
                    "NET_FUNDING_RATIO": 110 + index * 4,
                    "JZBJZC": 45 + index * 2,
                }
            )
        rows.append(parse_stock_financial_indicator(row))
    return rows


def model_industry_snapshot(day: date, *, size: int = 12) -> StockIndustryValuationSnapshot:
    rows = tuple(
        StockValuationPoint(
            date=day,
            code="600000" if index == 1 else f"600{index:03d}",
            name=f"Stock {index}",
            close=10.0,
            market_cap=None,
            pe_ttm=float(index + 5),
            pe_static=None,
            pb=float(index) / 2,
            ps_ttm=None,
            pcf_ocf_ttm=None,
            peg=None,
            raw={},
            board_code="016000",
            board_name="Fixture",
            original_board_code="1000",
        )
        for index in range(1, size + 1)
    )
    return StockIndustryValuationSnapshot(
        date=day,
        board_code="016000",
        board_name="Fixture",
        original_board_code="1000",
        rows=rows,
    )


def make_valuation(day: date, value: int) -> StockValuationPoint:
    number = float(value)
    return StockValuationPoint(
        date=day,
        code="600000",
        name="Test",
        close=number,
        market_cap=None,
        pe_ttm=number,
        pe_static=None,
        pb=number,
        ps_ttm=number,
        pcf_ocf_ttm=number,
        peg=None,
        raw={"must_not_leak": True},
    )


def make_bar(day: date, value: float) -> StockBar:
    return StockBar(
        date=day,
        open=value,
        close=value,
        high=value,
        low=value,
        volume=1.0,
        amount=1.0,
        amplitude_pct=None,
        change_pct=None,
        change_amount=None,
        turnover_pct=None,
    )
