from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from market_lens.types import FundNavPoint, StockBar, StockValuationPoint
from market_lens.valuation.analyzer import analyze_fund, analyze_stock
from market_lens.valuation.assessment import build_fund_assessment
from market_lens.valuation.confidence import (
    calculate_confidence,
    conservative_overall_confidence,
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
    direction: str = "lower_is_better",
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
            direction="higher_is_better",
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

    assert MODEL_VERSION == "valuation-v2.0.0-infrastructure"
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
    assert assessment["dimensions"]["valuation"]["sample_adequacy"] == 0.21
    assert assessment["dimensions"]["valuation"]["confidence"] < 1.0
    assert assessment["dimensions"]["quality"] is None
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
    assert assessment["dimensions"]["quality"] is None
    assert assessment["dimensions"]["product"] is None
    assert assessment["overall_confidence"] == 0.0
    assert sum(factor["weight"] for factor in valuation["factors"]) == 1.0
    assert all(factor["status"] == "missing" for factor in valuation["factors"])


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
