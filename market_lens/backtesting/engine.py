from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from math import floor, sqrt
from statistics import fmean, median
from typing import Any

from market_lens.backtesting.models import (
    AssessmentSnapshot,
    AttractivenessWeights,
    BacktestConfiguration,
    BacktestDataError,
    BacktestSample,
    ForwardOutcome,
    MarketRegime,
    PricePoint,
)

ScoreProvider = Callable[[AssessmentSnapshot], float | None]


def run_backtest(
    snapshots: Iterable[AssessmentSnapshot],
    prices: dict[str, Iterable[PricePoint]],
    *,
    benchmark_prices: Iterable[PricePoint] | None = None,
    configuration: BacktestConfiguration | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    config = configuration or BacktestConfiguration()
    ordered_snapshots = sorted(snapshots, key=lambda item: (item.as_of, item.key))
    validate_snapshots(ordered_snapshots)
    normalized_prices = {
        key: normalize_prices(points, key) for key, points in sorted(prices.items())
    }
    benchmark = normalize_prices(benchmark_prices or (), "benchmark", allow_empty=True)
    samples_by_horizon = build_samples(
        ordered_snapshots,
        normalized_prices,
        benchmark,
        config,
    )
    dates = sorted({item.as_of for item in ordered_snapshots})
    train_dates, validation_dates = chronological_date_split(
        dates, config.validation_fraction
    )

    dimensions: dict[str, Any] = {}
    for dimension in ("valuation", "quality", "product"):
        dimensions[dimension] = {
            str(horizon): evaluate_dimension(
                samples_by_horizon[horizon],
                score_provider=dimension_score_provider(dimension),
                dimension=dimension,
                config=config,
            )
            for horizon in config.horizons
        }

    candidate_results = evaluate_candidates(
        samples_by_horizon,
        train_dates,
        validation_dates,
        config,
    )
    selected_validation = candidate_results.get("selected_validation") or {}
    decision = attractiveness_review_decision(
        selected_validation.get(str(config.selection_horizon))
    )
    model_versions = sorted({item.model_version for item in ordered_snapshots})
    generated = generated_at or datetime.now(UTC)
    return {
        "schema_version": "backtest-1",
        "generated_at": generated.isoformat(),
        "dataset_fingerprint": dataset_fingerprint(
            ordered_snapshots, normalized_prices, benchmark, config
        ),
        "model_version": model_versions[0] if model_versions else None,
        "configuration": {
            "horizons_sessions": list(config.horizons),
            "bucket_count": config.bucket_count,
            "minimum_cross_section": config.minimum_cross_section,
            "entry_rule": "first_available_close_strictly_after_analysis_as_of",
            "exit_rule": "entry_session_plus_horizon_sessions",
            "validation_fraction": config.validation_fraction,
            "selection_horizon": config.selection_horizon,
            "regime_lookback_sessions": config.regime_lookback_sessions,
            "bull_threshold": config.bull_threshold,
            "bear_threshold": config.bear_threshold,
        },
        "dataset": {
            "snapshot_count": len(ordered_snapshots),
            "asset_count": len({item.key for item in ordered_snapshots}),
            "date_count": len(dates),
            "start": dates[0].isoformat() if dates else None,
            "end": dates[-1].isoformat() if dates else None,
            "train_dates": [item.isoformat() for item in sorted(train_dates)],
            "validation_dates": [item.isoformat() for item in sorted(validation_dates)],
            "sample_count_by_horizon": {
                str(key): len(value) for key, value in samples_by_horizon.items()
            },
        },
        "leakage_checks": {
            "status": "passed",
            "checked_snapshot_count": len(ordered_snapshots),
            "future_source_dates": 0,
            "same_day_entry_used": False,
        },
        "dimensions": dimensions,
        "attractiveness_candidate": candidate_results,
        "release_decision": {
            "attractiveness": decision,
            "production_release": False,
            "reason": "V2-7 produces research evidence only; publication requires V2-8.",
        },
        "limitations": [
            (
                "Returns exclude fees, taxes, slippage, suspension handling, "
                "and survivorship corrections."
            ),
            (
                "Fund calibration is invalid unless historical holdings and product snapshots "
                "are point-in-time."
            ),
            (
                "A passing candidate is eligible for review only and does not change "
                "production weights."
            ),
        ],
    }


def validate_snapshots(snapshots: list[AssessmentSnapshot]) -> None:
    seen: set[tuple[str, date]] = set()
    versions: set[str] = set()
    for snapshot in snapshots:
        snapshot.validate_point_in_time()
        identity = (snapshot.key, snapshot.as_of)
        if identity in seen:
            raise BacktestDataError(
                f"duplicate assessment snapshot for {snapshot.key} at {snapshot.as_of}"
            )
        seen.add(identity)
        versions.add(snapshot.model_version)
    if len(versions) > 1:
        raise BacktestDataError(
            "a backtest run cannot mix model versions: " + ",".join(sorted(versions))
        )


def normalize_prices(
    points: Iterable[PricePoint],
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[PricePoint, ...]:
    ordered = tuple(sorted(points, key=lambda item: item.date))
    if not ordered and not allow_empty:
        raise BacktestDataError(f"price series is empty for {label}")
    dates = [item.date for item in ordered]
    if len(dates) != len(set(dates)):
        raise BacktestDataError(f"duplicate price dates found for {label}")
    return ordered


def build_samples(
    snapshots: list[AssessmentSnapshot],
    prices: dict[str, tuple[PricePoint, ...]],
    benchmark: tuple[PricePoint, ...],
    config: BacktestConfiguration,
) -> dict[int, list[BacktestSample]]:
    result = {horizon: [] for horizon in config.horizons}
    for snapshot in snapshots:
        series = prices.get(snapshot.key)
        if not series:
            raise BacktestDataError(f"price series is missing for {snapshot.key}")
        regime = classify_market_regime(benchmark, snapshot.as_of, config)
        for horizon in config.horizons:
            outcome = calculate_forward_outcome(series, snapshot.as_of, horizon)
            if outcome is not None:
                result[horizon].append(BacktestSample(snapshot, outcome, regime))
    return result


def calculate_forward_outcome(
    prices: tuple[PricePoint, ...],
    as_of: date,
    horizon_sessions: int,
) -> ForwardOutcome | None:
    dates = [item.date for item in prices]
    entry_index = bisect_right(dates, as_of)
    exit_index = entry_index + horizon_sessions
    if entry_index >= len(prices) or exit_index >= len(prices):
        return None
    window = prices[entry_index : exit_index + 1]
    entry = window[0]
    exit_point = window[-1]
    return ForwardOutcome(
        horizon_sessions=horizon_sessions,
        entry_date=entry.date,
        exit_date=exit_point.date,
        forward_return=exit_point.close / entry.close - 1,
        max_drawdown=price_max_drawdown(window),
    )


def price_max_drawdown(prices: tuple[PricePoint, ...]) -> float:
    peak = prices[0].close
    worst = 0.0
    for point in prices:
        peak = max(peak, point.close)
        worst = min(worst, point.close / peak - 1)
    return worst


def classify_market_regime(
    benchmark: tuple[PricePoint, ...],
    as_of: date,
    config: BacktestConfiguration,
) -> MarketRegime:
    if not benchmark:
        return "unknown"
    dates = [item.date for item in benchmark]
    current_index = bisect_right(dates, as_of) - 1
    past_index = current_index - config.regime_lookback_sessions
    if current_index < 0 or past_index < 0:
        return "unknown"
    trailing_return = benchmark[current_index].close / benchmark[past_index].close - 1
    if trailing_return >= config.bull_threshold:
        return "bull"
    if trailing_return <= config.bear_threshold:
        return "bear"
    return "neutral"


def chronological_date_split(
    dates: list[date], validation_fraction: float
) -> tuple[set[date], set[date]]:
    if len(dates) < 2:
        return set(dates), set()
    validation_count = max(1, round(len(dates) * validation_fraction))
    validation_count = min(validation_count, len(dates) - 1)
    split_at = len(dates) - validation_count
    return set(dates[:split_at]), set(dates[split_at:])


def dimension_score_provider(dimension: str) -> ScoreProvider:
    def provider(snapshot: AssessmentSnapshot) -> float | None:
        score = snapshot.score(dimension)  # type: ignore[arg-type]
        if score is None:
            return None
        return 100 - score if dimension == "valuation" else score

    return provider


def evaluate_dimension(
    samples: list[BacktestSample],
    *,
    score_provider: ScoreProvider,
    dimension: str,
    config: BacktestConfiguration,
    allowed_dates: set[date] | None = None,
) -> dict[str, Any]:
    eligible = [
        item
        for item in samples
        if (allowed_dates is None or item.snapshot.as_of in allowed_dates)
        and score_provider(item.snapshot) is not None
    ]
    ranked = assign_cross_section_buckets(eligible, score_provider, config)
    bucket_rows: dict[int, list[BacktestSample]] = defaultdict(list)
    for item, bucket, _score in ranked:
        bucket_rows[bucket].append(item)
    bucket_metrics = {
        str(bucket): summarize_outcomes(bucket_rows.get(bucket, []))
        for bucket in range(1, config.bucket_count + 1)
    }
    low = bucket_metrics["1"]["mean_return"]
    high = bucket_metrics[str(config.bucket_count)]["mean_return"]
    spread = high - low if high is not None and low is not None else None
    bucket_means = [
        (bucket, metrics["mean_return"])
        for bucket, metrics in ((int(key), value) for key, value in bucket_metrics.items())
        if metrics["mean_return"] is not None
    ]
    by_date: dict[date, list[tuple[BacktestSample, float]]] = defaultdict(list)
    for item, _bucket, score in ranked:
        by_date[item.snapshot.as_of].append((item, score))
    information_coefficients = [
        coefficient
        for rows in by_date.values()
        if (coefficient := cross_section_ic(rows)) is not None
    ]
    return {
        "dimension": dimension,
        "score_direction": "higher_is_more_desirable",
        "sample_count": len(ranked),
        "date_count": len(by_date),
        "bucket_returns": bucket_metrics,
        "top_minus_bottom_return": rounded(spread),
        "bucket_monotonicity": rounded(
            spearman(
                [float(item[0]) for item in bucket_means],
                [float(item[1]) for item in bucket_means],
            )
            if len(bucket_means) >= 3
            else None
        ),
        "mean_information_coefficient": rounded(
            fmean(information_coefficients) if information_coefficients else None
        ),
        "positive_ic_ratio": rounded(
            sum(item > 0 for item in information_coefficients)
            / len(information_coefficients)
            if information_coefficients
            else None
        ),
        "top_bucket_turnover": rounded(
            calculate_top_bucket_turnover(ranked, config.bucket_count)
        ),
        "score_stability": rounded(calculate_score_stability(ranked)),
        "segments": build_segment_reports(ranked, config.bucket_count),
    }


def assign_cross_section_buckets(
    samples: list[BacktestSample],
    score_provider: ScoreProvider,
    config: BacktestConfiguration,
) -> list[tuple[BacktestSample, int, float]]:
    by_date: dict[date, list[tuple[BacktestSample, float]]] = defaultdict(list)
    for sample in samples:
        score = score_provider(sample.snapshot)
        if score is not None:
            by_date[sample.snapshot.as_of].append((sample, score))
    result: list[tuple[BacktestSample, int, float]] = []
    for rows in by_date.values():
        if len(rows) < config.minimum_cross_section:
            continue
        ranks = average_ranks([item[1] for item in rows])
        denominator = max(len(rows) - 1, 1)
        for (sample, score), rank in zip(rows, ranks, strict=True):
            percentile = rank / denominator
            bucket = min(floor(percentile * config.bucket_count) + 1, config.bucket_count)
            result.append((sample, bucket, score))
    return result


def summarize_outcomes(samples: list[BacktestSample]) -> dict[str, Any]:
    if not samples:
        return {
            "count": 0,
            "mean_return": None,
            "median_return": None,
            "win_rate": None,
            "mean_max_drawdown": None,
        }
    returns = [item.outcome.forward_return for item in samples]
    drawdowns = [item.outcome.max_drawdown for item in samples]
    return {
        "count": len(samples),
        "mean_return": rounded(fmean(returns)),
        "median_return": rounded(median(returns)),
        "win_rate": rounded(sum(item > 0 for item in returns) / len(returns)),
        "mean_max_drawdown": rounded(fmean(drawdowns)),
    }


def cross_section_ic(rows: list[tuple[BacktestSample, float]]) -> float | None:
    if len(rows) < 3:
        return None
    return spearman(
        [item[1] for item in rows],
        [item[0].outcome.forward_return for item in rows],
    )


def build_segment_reports(
    ranked: list[tuple[BacktestSample, int, float]],
    top_bucket: int,
) -> dict[str, dict[str, Any]]:
    segment_getters: dict[str, Callable[[BacktestSample], str]] = {
        "profile": lambda item: item.snapshot.profile,
        "industry": lambda item: item.snapshot.industry,
        "market_regime": lambda item: item.market_regime,
        "confidence": lambda item: confidence_band(item.snapshot.overall_confidence),
    }
    reports: dict[str, dict[str, Any]] = {}
    for segment_name, getter in segment_getters.items():
        groups: dict[str, list[tuple[BacktestSample, int]]] = defaultdict(list)
        for sample, bucket, _score in ranked:
            groups[getter(sample)].append((sample, bucket))
        reports[segment_name] = {
            key: summarize_segment(rows, top_bucket) for key, rows in sorted(groups.items())
        }
    return reports


def summarize_segment(
    rows: list[tuple[BacktestSample, int]], top_bucket: int
) -> dict[str, Any]:
    low = [item for item, bucket in rows if bucket == 1]
    high = [item for item, bucket in rows if bucket == top_bucket]
    low_mean = fmean(item.outcome.forward_return for item in low) if low else None
    high_mean = fmean(item.outcome.forward_return for item in high) if high else None
    return {
        "count": len(rows),
        "top_count": len(high),
        "bottom_count": len(low),
        "top_minus_bottom_return": rounded(
            high_mean - low_mean if high_mean is not None and low_mean is not None else None
        ),
    }


def calculate_top_bucket_turnover(
    ranked: list[tuple[BacktestSample, int, float]], top_bucket: int
) -> float | None:
    holdings: dict[date, set[str]] = defaultdict(set)
    for sample, bucket, _score in ranked:
        if bucket == top_bucket:
            holdings[sample.snapshot.as_of].add(sample.snapshot.key)
    values: list[float] = []
    previous: set[str] | None = None
    for current in (holdings[item] for item in sorted(holdings)):
        if previous and current:
            previous_weight = 1 / len(previous)
            current_weight = 1 / len(current)
            universe = previous | current
            values.append(
                0.5
                * sum(
                    abs(
                        (current_weight if key in current else 0.0)
                        - (previous_weight if key in previous else 0.0)
                    )
                    for key in universe
                )
            )
        previous = current
    return fmean(values) if values else None


def calculate_score_stability(
    ranked: list[tuple[BacktestSample, int, float]],
) -> float | None:
    by_date: dict[date, dict[str, float]] = defaultdict(dict)
    for sample, _bucket, score in ranked:
        by_date[sample.snapshot.as_of][sample.snapshot.key] = score
    values: list[float] = []
    previous: dict[str, float] | None = None
    for current in (by_date[item] for item in sorted(by_date)):
        if previous is not None:
            common = sorted(previous.keys() & current.keys())
            if len(common) >= 3:
                value = spearman(
                    [previous[key] for key in common],
                    [current[key] for key in common],
                )
                if value is not None:
                    values.append(value)
        previous = current
    return fmean(values) if values else None


def evaluate_candidates(
    samples_by_horizon: dict[int, list[BacktestSample]],
    train_dates: set[date],
    validation_dates: set[date],
    config: BacktestConfiguration,
) -> dict[str, Any]:
    selection_samples = samples_by_horizon[config.selection_horizon]
    candidates: dict[str, Any] = {}
    selected: AttractivenessWeights | None = None
    selected_key: tuple[float, float] | None = None
    for candidate in config.candidates:
        provider = attractiveness_provider(candidate)
        train_result = evaluate_dimension(
            selection_samples,
            score_provider=provider,
            dimension="attractiveness_candidate",
            config=config,
            allowed_dates=train_dates,
        )
        validation_result = evaluate_dimension(
            selection_samples,
            score_provider=provider,
            dimension="attractiveness_candidate",
            config=config,
            allowed_dates=validation_dates,
        )
        candidates[candidate.name] = {
            "weights": candidate_weights_dict(candidate),
            "training": train_result,
            "validation": validation_result,
        }
        key = (
            train_result.get("mean_information_coefficient") or float("-inf"),
            train_result.get("top_minus_bottom_return") or float("-inf"),
        )
        if selected_key is None or key > selected_key:
            selected = candidate
            selected_key = key

    if selected is None:
        return {
            "status": "unavailable",
            "reason": "no candidate configurations",
            "candidates": {},
            "selected": None,
            "selected_validation": {},
        }
    selected_provider = attractiveness_provider(selected)
    return {
        "status": "research_only",
        "selection_rule": (
            "highest training mean information coefficient, then training top-minus-bottom return"
        ),
        "selected": selected.name,
        "candidates": candidates,
        "selected_validation": {
            str(horizon): evaluate_dimension(
                samples,
                score_provider=selected_provider,
                dimension="attractiveness_candidate",
                config=config,
                allowed_dates=validation_dates,
            )
            for horizon, samples in samples_by_horizon.items()
        },
    }


def attractiveness_provider(candidate: AttractivenessWeights) -> ScoreProvider:
    def provider(snapshot: AssessmentSnapshot) -> float | None:
        if snapshot.valuation_score is None or snapshot.quality_score is None:
            return None
        cheapness = 100 - snapshot.valuation_score
        if snapshot.asset_type == "stock":
            raw_score = (
                cheapness * candidate.stock_valuation
                + snapshot.quality_score * candidate.stock_quality
            )
        else:
            if snapshot.product_score is None:
                return None
            raw_score = (
                cheapness * candidate.fund_valuation
                + snapshot.quality_score * candidate.fund_quality
                + snapshot.product_score * candidate.fund_product
            )
        return 50 + (raw_score - 50) * snapshot.overall_confidence

    return provider


def candidate_weights_dict(candidate: AttractivenessWeights) -> dict[str, Any]:
    return {
        "stock": {
            "cheapness": candidate.stock_valuation,
            "quality": candidate.stock_quality,
        },
        "fund": {
            "cheapness": candidate.fund_valuation,
            "quality": candidate.fund_quality,
            "product": candidate.fund_product,
        },
        "confidence_adjustment": "50 + (raw_score - 50) * overall_confidence",
    }


def attractiveness_review_decision(metrics: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[str] = []
    if not metrics:
        reasons.append("validation_metrics_unavailable")
    else:
        if int(metrics.get("sample_count") or 0) < 200:
            reasons.append("validation_sample_below_200")
        if int(metrics.get("date_count") or 0) < 12:
            reasons.append("validation_dates_below_12")
        if (metrics.get("mean_information_coefficient") or 0.0) <= 0.02:
            reasons.append("validation_mean_ic_not_above_0.02")
        if (metrics.get("top_minus_bottom_return") or 0.0) <= 0:
            reasons.append("validation_top_bottom_spread_not_positive")
        if (metrics.get("bucket_monotonicity") or 0.0) <= 0.5:
            reasons.append("validation_bucket_monotonicity_not_above_0.5")
    return {
        "status": "eligible_for_v2_8_review" if not reasons else "insufficient_evidence",
        "reasons": reasons,
        "automatic_publication": False,
    }


def confidence_band(value: float) -> str:
    if value >= 0.7:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average = (start + end - 1) / 2
        for original_index, _value in indexed[start:end]:
            ranks[original_index] = average
        start = end
    return ranks


def spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    return pearson(average_ranks(left), average_ranks(right))


def pearson(left: list[float], right: list[float]) -> float | None:
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_delta = [item - left_mean for item in left]
    right_delta = [item - right_mean for item in right]
    denominator = sqrt(
        sum(item * item for item in left_delta) * sum(item * item for item in right_delta)
    )
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(left_delta, right_delta, strict=True)) / denominator


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def dataset_fingerprint(
    snapshots: list[AssessmentSnapshot],
    prices: dict[str, tuple[PricePoint, ...]],
    benchmark: tuple[PricePoint, ...],
    config: BacktestConfiguration,
) -> str:
    payload = {
        "snapshots": [
            {
                "key": item.key,
                "as_of": item.as_of.isoformat(),
                "model_version": item.model_version,
                "valuation": item.valuation_score,
                "quality": item.quality_score,
                "product": item.product_score,
                "confidence": item.overall_confidence,
                "point_in_time_verified": item.point_in_time_verified,
                "provenance": item.provenance,
                "source_dates": [value.isoformat() for value in item.source_dates],
            }
            for item in snapshots
        ],
        "prices": {
            key: [(item.date.isoformat(), item.close) for item in values]
            for key, values in prices.items()
        },
        "benchmark": [(item.date.isoformat(), item.close) for item in benchmark],
        "configuration": {
            "horizons": config.horizons,
            "bucket_count": config.bucket_count,
            "minimum_cross_section": config.minimum_cross_section,
            "validation_fraction": config.validation_fraction,
            "selection_horizon": config.selection_horizon,
            "candidates": [candidate_weights_dict(item) for item in config.candidates],
        },
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
