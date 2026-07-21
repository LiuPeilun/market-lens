from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from math import isfinite, log10
from typing import Any, Literal

from market_lens.valuation.metrics import percentile_rank
from market_lens.valuation.scoring_config import FactorDefinition

FactorStatus = Literal[
    "available",
    "missing",
    "stale",
    "error",
    "invalid",
    "not_applicable",
]


@dataclass(frozen=True)
class FactorObservation:
    value: float | None
    source: str
    source_as_of: date | None
    status: FactorStatus = "available"
    history: tuple[float | None, ...] = ()
    sample_size: int | None = None
    coverage: float = 1.0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorResult:
    key: str
    name: str
    category: str
    value: float | None
    unit: str
    source_as_of: str | None
    score: float | None
    direction: str
    normalization: str
    weight: float
    effective_weight: float
    sample_size: int
    minimum_sample_size: int
    full_sample_size: int
    coverage: float
    source: str
    status: FactorStatus
    eligible: bool
    core: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["warnings"] = list(self.warnings)
        return result


def evaluate_factor(
    definition: FactorDefinition,
    observation: FactorObservation,
) -> FactorResult:
    warnings = list(observation.warnings)
    sample_size = observation.sample_size
    if sample_size is None:
        sample_size = len(observation.history)

    value = observation.value
    eligible = observation.status == "available"
    score: float | None = None

    if eligible and (value is None or not isfinite(value)):
        eligible = False
        warnings.append("value_missing_or_non_finite")
    if eligible and definition.positive_only and value is not None and value <= 0:
        eligible = False
        warnings.append("non_positive_value")
    if (
        eligible
        and definition.normalization != "historical_percentile"
        and sample_size < definition.minimum_sample_size
    ):
        eligible = False
        warnings.append(
            f"insufficient_sample:{sample_size}<{definition.minimum_sample_size}"
        )

    normalized_value: float | None = None
    if eligible and definition.normalization == "historical_percentile":
        history = clean_history(observation.history, positive_only=definition.positive_only)
        sample_size = len(history)
        if sample_size < definition.minimum_sample_size:
            eligible = False
            warnings.append(
                f"insufficient_sample:{sample_size}<{definition.minimum_sample_size}"
            )
        else:
            normalized_value = percentile_rank(history, value)
    elif eligible and definition.normalization == "pre_normalized_percentile":
        if value is None or not 0 <= value <= 1:
            eligible = False
            warnings.append("percentile_out_of_range")
        else:
            normalized_value = value
    elif eligible and definition.normalization == "linear_anchor":
        assert definition.anchor_min is not None
        assert definition.anchor_max is not None
        assert value is not None
        anchor_range = definition.anchor_max - definition.anchor_min
        normalized_value = min(
            max((value - definition.anchor_min) / anchor_range, 0),
            1,
        )
    elif eligible and definition.normalization == "log_linear_anchor":
        assert definition.anchor_min is not None
        assert definition.anchor_max is not None
        assert value is not None
        if value <= 0 or definition.anchor_min <= 0:
            eligible = False
            warnings.append("log_anchor_requires_positive_value")
        else:
            anchor_range = log10(definition.anchor_max) - log10(definition.anchor_min)
            normalized_value = min(
                max((log10(value) - log10(definition.anchor_min)) / anchor_range, 0),
                1,
            )

    if eligible and normalized_value is not None:
        score = normalized_value * 100
        if definition.direction == "lower_value_higher_score":
            score = 100 - score

    return FactorResult(
        key=definition.key,
        name=definition.name,
        category=definition.category,
        value=value,
        unit=definition.unit,
        source_as_of=(
            observation.source_as_of.isoformat() if observation.source_as_of else None
        ),
        score=round(score, 6) if score is not None else None,
        direction=definition.direction,
        normalization=definition.normalization,
        weight=definition.weight,
        effective_weight=0.0,
        sample_size=sample_size,
        minimum_sample_size=definition.minimum_sample_size,
        full_sample_size=definition.full_sample_size or definition.minimum_sample_size,
        coverage=clamp(observation.coverage),
        source=observation.source,
        status=observation.status,
        eligible=eligible,
        core=definition.core,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def score_dimension(
    definitions: tuple[FactorDefinition, ...],
    observations: dict[str, FactorObservation],
    *,
    minimum_effective_weight: float = 0.5,
) -> dict[str, Any]:
    results = [
        evaluate_factor(
            definition,
            observations.get(
                definition.key,
                FactorObservation(
                    value=None,
                    source="unavailable",
                    source_as_of=None,
                    status="missing",
                    coverage=0.0,
                ),
            ),
        )
        for definition in definitions
    ]
    applicable_weight = sum(
        result.weight for result in results if result.status != "not_applicable"
    )
    eligible_weight = sum(result.weight for result in results if result.eligible)
    weight_coverage = eligible_weight / applicable_weight if applicable_weight else 0.0
    warnings: list[str] = []
    core_missing = [result.key for result in results if result.core and not result.eligible]
    if core_missing:
        warnings.append("core_factors_unavailable:" + ",".join(core_missing))
    if applicable_weight and weight_coverage < minimum_effective_weight:
        warnings.append(
            f"insufficient_effective_weight:{weight_coverage:.4f}<{minimum_effective_weight:.4f}"
        )

    normalized_results = [
        replace(
            result,
            effective_weight=(result.weight / eligible_weight if result.eligible else 0.0),
        )
        for result in results
    ]
    can_score = (
        bool(eligible_weight)
        and weight_coverage >= minimum_effective_weight
        and not core_missing
    )
    score = (
        sum(
            (result.score or 0.0) * result.effective_weight
            for result in normalized_results
            if result.eligible
        )
        if can_score
        else None
    )
    sample_adequacy = weighted_sample_adequacy(definitions, normalized_results)
    data_coverage = sum(
        result.weight * result.coverage for result in normalized_results if result.eligible
    )
    data_coverage = data_coverage / applicable_weight if applicable_weight else 0.0
    return {
        "score": round(score, 2) if score is not None else None,
        "factors": [result.to_dict() for result in normalized_results],
        "weight_coverage": round(weight_coverage, 4),
        "data_coverage": round(data_coverage, 4),
        "sample_adequacy": round(sample_adequacy, 4),
        "minimum_effective_weight": minimum_effective_weight,
        "warnings": warnings,
    }


def clean_history(
    values: tuple[float | None, ...],
    *,
    positive_only: bool,
) -> list[float]:
    return [
        float(value)
        for value in values
        if value is not None
        and isfinite(value)
        and (not positive_only or value > 0)
    ]


def weighted_sample_adequacy(
    definitions: tuple[FactorDefinition, ...],
    results: list[FactorResult],
) -> float:
    applicable = [result for result in results if result.status != "not_applicable"]
    total_weight = sum(result.weight for result in applicable)
    if not total_weight:
        return 0.0
    full_samples = {
        definition.key: definition.full_sample_size or definition.minimum_sample_size
        for definition in definitions
    }
    return sum(
        result.weight * min(result.sample_size / full_samples[result.key], 1.0)
        for result in applicable
    ) / total_weight


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
