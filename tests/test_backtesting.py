from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from market_lens.backtesting.engine import calculate_forward_outcome, run_backtest
from market_lens.backtesting.io import load_backtest_dataset
from market_lens.backtesting.models import (
    AssessmentSnapshot,
    BacktestConfiguration,
    BacktestDataError,
    PricePoint,
    snapshot_from_analysis,
)


def test_snapshot_rejects_future_factor_source_date() -> None:
    analysis = analysis_payload("600519", date(2026, 1, 10), 30.0, 70.0)
    analysis["assessment"]["dimensions"]["valuation"]["factors"] = [
        {"key": "pe_ttm", "source_as_of": "2026-01-11"}
    ]

    with pytest.raises(BacktestDataError, match="future source data detected"):
        snapshot_from_analysis(analysis)


def test_forward_outcome_enters_after_analysis_date() -> None:
    prices = tuple(
        PricePoint(date(2026, 1, day), close)
        for day, close in ((1, 100.0), (2, 110.0), (3, 99.0), (4, 121.0))
    )

    outcome = calculate_forward_outcome(prices, date(2026, 1, 1), 2)

    assert outcome is not None
    assert outcome.entry_date == date(2026, 1, 2)
    assert outcome.exit_date == date(2026, 1, 4)
    assert outcome.forward_return == pytest.approx(0.1)
    assert outcome.max_drawdown == pytest.approx(-0.1)


def test_run_backtest_builds_ranked_segments_and_holdout_candidate() -> None:
    snapshots, prices, benchmark = synthetic_dataset()
    config = BacktestConfiguration(
        horizons=(2,),
        bucket_count=2,
        minimum_cross_section=4,
        validation_fraction=0.25,
        selection_horizon=2,
        regime_lookback_sessions=2,
        bull_threshold=0.01,
        bear_threshold=-0.01,
    )

    report = run_backtest(
        snapshots,
        prices,
        benchmark_prices=benchmark,
        configuration=config,
        generated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    valuation = report["dimensions"]["valuation"]["2"]
    assert valuation["sample_count"] == 32
    assert valuation["date_count"] == 4
    assert valuation["top_minus_bottom_return"] > 0
    assert valuation["mean_information_coefficient"] > 0
    assert valuation["segments"]["market_regime"]["bull"]["count"] == 32
    assert report["attractiveness_candidate"]["selected"] is not None
    selected = report["attractiveness_candidate"]["selected_validation"]["2"]
    assert selected["date_count"] == 1
    assert selected["top_minus_bottom_return"] > 0
    assert report["release_decision"]["production_release"] is False
    assert report["release_decision"]["attractiveness"]["status"] == "insufficient_evidence"
    assert report["leakage_checks"]["same_day_entry_used"] is False


def test_backtest_rejects_mixed_model_versions() -> None:
    snapshots, prices, _benchmark = synthetic_dataset()
    snapshots[0] = AssessmentSnapshot(
        **{**snapshots[0].__dict__, "model_version": "different-model"}
    )

    with pytest.raises(BacktestDataError, match="cannot mix model versions"):
        run_backtest(
            snapshots,
            prices,
            configuration=BacktestConfiguration(
                horizons=(2,),
                bucket_count=2,
                minimum_cross_section=4,
                selection_horizon=2,
            ),
        )


def test_dataset_fingerprint_is_stable_for_input_order() -> None:
    snapshots, prices, benchmark = synthetic_dataset()
    config = BacktestConfiguration(
        horizons=(2,),
        bucket_count=2,
        minimum_cross_section=4,
        selection_horizon=2,
    )

    first = run_backtest(snapshots, prices, benchmark_prices=benchmark, configuration=config)
    second = run_backtest(
        reversed(snapshots),
        dict(reversed(list(prices.items()))),
        benchmark_prices=reversed(benchmark),
        configuration=config,
    )

    assert first["dataset_fingerprint"] == second["dataset_fingerprint"]


def test_load_backtest_dataset_parses_v2_analyses_and_prices() -> None:
    payload = {
        "analyses": [analysis_payload("600519", date(2026, 1, 10), 30.0, 70.0)],
        "prices": {
            "stock:600519": [
                {"date": "2026-01-10", "close": 100.0},
                {"date": "2026-01-11", "close": 101.0},
            ]
        },
        "benchmark_prices": [{"date": "2026-01-10", "close": 100.0}],
    }

    class MemoryPath:
        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            return json.dumps(payload, ensure_ascii=False)

    snapshots, prices, benchmark = load_backtest_dataset(MemoryPath())  # type: ignore[arg-type]

    assert snapshots[0].key == "stock:600519"
    assert prices["stock:600519"][1].close == 101.0
    assert benchmark[0].date == date(2026, 1, 10)


def synthetic_dataset() -> tuple[
    list[AssessmentSnapshot], dict[str, list[PricePoint]], list[PricePoint]
]:
    start = date(2026, 1, 1)
    price_dates = [start + timedelta(days=offset) for offset in range(35)]
    snapshot_dates = [start + timedelta(days=offset) for offset in (5, 10, 15, 20)]
    snapshots: list[AssessmentSnapshot] = []
    prices: dict[str, list[PricePoint]] = {}
    for index in range(8):
        code = f"{index:06d}"
        desirability = 90.0 - index * 10
        slope = 2.0 - index * 0.2
        key = f"stock:{code}"
        prices[key] = [
            PricePoint(item, 100.0 + offset * slope)
            for offset, item in enumerate(price_dates)
        ]
        for as_of in snapshot_dates:
            snapshots.append(
                AssessmentSnapshot(
                    asset_type="stock",
                    code=code,
                    name=f"Stock {code}",
                    profile="generic_non_financial",
                    industry="测试行业A" if index < 4 else "测试行业B",
                    as_of=as_of,
                    model_version="valuation-v2.2.0-fund-product-models",
                    valuation_score=100 - desirability,
                    valuation_confidence=0.8,
                    quality_score=desirability,
                    quality_confidence=0.7,
                    product_score=None,
                    product_confidence=0.0,
                    overall_confidence=0.7,
                    point_in_time_verified=True,
                    provenance="synthetic_fixture",
                    source_dates=(as_of,),
                )
            )
    benchmark = [
        PricePoint(item, 100.0 + offset) for offset, item in enumerate(price_dates)
    ]
    return snapshots, prices, benchmark


def analysis_payload(
    code: str,
    as_of: date,
    valuation_score: float,
    quality_score: float,
) -> dict:
    dimension = {
        "model": "test",
        "score": valuation_score,
        "level": "normal",
        "level_zh": "正常估值",
        "confidence": 0.7,
        "factors": [{"key": "pe_ttm", "source_as_of": as_of.isoformat()}],
        "weight_coverage": 1.0,
        "data_coverage": 1.0,
        "sample_adequacy": 1.0,
        "warnings": [],
    }
    return {
        "asset_type": "stock",
        "code": code,
        "name": "测试股票",
        "as_of": as_of.isoformat(),
        "backtest_provenance": {
            "point_in_time_verified": True,
            "method": "synthetic_fixture",
        },
        "valuation": {"industry": {"em_industry": "测试行业"}},
        "assessment": {
            "schema_version": "2",
            "model_version": "valuation-v2.2.0-fund-product-models",
            "profile": "generic_non_financial",
            "analysis_as_of": as_of.isoformat(),
            "dimensions": {
                "valuation": dimension,
                "quality": {
                    **dimension,
                    "score": quality_score,
                    "level": "high",
                    "level_zh": "较高",
                },
                "product": None,
            },
            "overall_confidence": 0.7,
            "attractiveness": None,
            "confidence_detail": {},
            "data_quality": {
                "sources": [],
                "warnings": [],
                "source_as_of": as_of.isoformat(),
                "retrieved_at": "2026-07-21T00:00:00Z",
            },
        },
    }
