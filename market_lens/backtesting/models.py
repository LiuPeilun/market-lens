from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any, Literal

DimensionName = Literal["valuation", "quality", "product", "attractiveness_candidate"]
MarketRegime = Literal["bull", "neutral", "bear", "unknown"]


class BacktestDataError(ValueError):
    """Raised when a backtest input violates point-in-time or value constraints."""


@dataclass(frozen=True)
class PricePoint:
    date: date
    close: float

    def __post_init__(self) -> None:
        if not isfinite(self.close) or self.close <= 0:
            raise BacktestDataError("price close must be finite and positive")


@dataclass(frozen=True)
class AssessmentSnapshot:
    asset_type: Literal["stock", "fund"]
    code: str
    name: str | None
    profile: str
    industry: str
    as_of: date
    model_version: str
    valuation_score: float | None
    valuation_confidence: float
    quality_score: float | None
    quality_confidence: float
    product_score: float | None
    product_confidence: float
    overall_confidence: float
    point_in_time_verified: bool
    provenance: str
    source_dates: tuple[date, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.asset_type}:{self.code}"

    def score(self, dimension: DimensionName) -> float | None:
        if dimension == "valuation":
            return self.valuation_score
        if dimension == "quality":
            return self.quality_score
        if dimension == "product":
            return self.product_score
        raise ValueError(f"Snapshot has no stored score for {dimension}")

    def confidence(self, dimension: DimensionName) -> float:
        if dimension == "valuation":
            return self.valuation_confidence
        if dimension == "quality":
            return self.quality_confidence
        if dimension == "product":
            return self.product_confidence
        raise ValueError(f"Snapshot has no stored confidence for {dimension}")

    def validate_point_in_time(self) -> None:
        if not self.point_in_time_verified or not self.provenance:
            raise BacktestDataError(
                f"point-in-time provenance is not verified for {self.key} at {self.as_of}"
            )
        future_dates = sorted(item for item in self.source_dates if item > self.as_of)
        if future_dates:
            joined = ",".join(item.isoformat() for item in future_dates)
            raise BacktestDataError(
                f"future source data detected for {self.key} at {self.as_of}: {joined}"
            )
        for name, value in (
            ("valuation", self.valuation_score),
            ("quality", self.quality_score),
            ("product", self.product_score),
        ):
            if value is not None and (not isfinite(value) or not 0 <= value <= 100):
                raise BacktestDataError(f"{name} score must be between 0 and 100")
        for name, value in (
            ("valuation", self.valuation_confidence),
            ("quality", self.quality_confidence),
            ("product", self.product_confidence),
            ("overall", self.overall_confidence),
        ):
            if not isfinite(value) or not 0 <= value <= 1:
                raise BacktestDataError(f"{name} confidence must be between 0 and 1")


@dataclass(frozen=True)
class ForwardOutcome:
    horizon_sessions: int
    entry_date: date
    exit_date: date
    forward_return: float
    max_drawdown: float


@dataclass(frozen=True)
class BacktestSample:
    snapshot: AssessmentSnapshot
    outcome: ForwardOutcome
    market_regime: MarketRegime


@dataclass(frozen=True)
class AttractivenessWeights:
    name: str
    stock_valuation: float
    stock_quality: float
    fund_valuation: float
    fund_quality: float
    fund_product: float

    def __post_init__(self) -> None:
        weights = (
            self.stock_valuation,
            self.stock_quality,
            self.fund_valuation,
            self.fund_quality,
            self.fund_product,
        )
        stock_total = self.stock_valuation + self.stock_quality
        fund_total = self.fund_valuation + self.fund_quality + self.fund_product
        if any(value < 0 for value in weights):
            raise ValueError("candidate weights cannot be negative")
        if abs(stock_total - 1.0) > 1e-9 or abs(fund_total - 1.0) > 1e-9:
            raise ValueError("candidate weights must sum to one for each asset type")


DEFAULT_ATTRACTIVENESS_CANDIDATES = (
    AttractivenessWeights("balanced", 0.60, 0.40, 0.50, 0.30, 0.20),
    AttractivenessWeights("valuation_tilt", 0.70, 0.30, 0.60, 0.25, 0.15),
    AttractivenessWeights("quality_tilt", 0.50, 0.50, 0.40, 0.35, 0.25),
)


@dataclass(frozen=True)
class BacktestConfiguration:
    horizons: tuple[int, ...] = (21, 63, 126, 252)
    bucket_count: int = 5
    minimum_cross_section: int = 10
    validation_fraction: float = 0.30
    selection_horizon: int = 63
    regime_lookback_sessions: int = 126
    bull_threshold: float = 0.10
    bear_threshold: float = -0.10
    candidates: tuple[AttractivenessWeights, ...] = DEFAULT_ATTRACTIVENESS_CANDIDATES

    def __post_init__(self) -> None:
        if not self.horizons or any(item < 1 for item in self.horizons):
            raise ValueError("backtest horizons must contain positive session counts")
        if self.bucket_count < 2:
            raise ValueError("bucket_count must be at least two")
        if self.minimum_cross_section < self.bucket_count:
            raise ValueError("minimum_cross_section cannot be below bucket_count")
        if not 0 < self.validation_fraction < 0.5:
            raise ValueError("validation_fraction must be between zero and 0.5")
        if self.selection_horizon not in self.horizons:
            raise ValueError("selection_horizon must be included in horizons")
        if self.bear_threshold >= self.bull_threshold:
            raise ValueError("bear threshold must be below bull threshold")


def snapshot_from_analysis(analysis: dict[str, Any]) -> AssessmentSnapshot:
    assessment = analysis.get("assessment")
    if not isinstance(assessment, dict):
        raise BacktestDataError("V2 assessment is required for backtesting")
    dimensions = assessment.get("dimensions")
    if not isinstance(dimensions, dict):
        raise BacktestDataError("assessment dimensions are required for backtesting")
    valuation = dimension_dict(dimensions.get("valuation"), "valuation")
    quality = dimension_dict(dimensions.get("quality"), "quality")
    product = optional_dimension_dict(dimensions.get("product"), "product")
    as_of = parse_required_date(assessment.get("analysis_as_of"), "analysis_as_of")
    top_level_as_of = parse_optional_date(analysis.get("as_of"))
    if top_level_as_of is not None and top_level_as_of != as_of:
        raise BacktestDataError("analysis.as_of and assessment.analysis_as_of must match")
    provenance = analysis.get("backtest_provenance")
    if not isinstance(provenance, dict) or provenance.get("point_in_time_verified") is not True:
        raise BacktestDataError(
            "backtest_provenance.point_in_time_verified=true is required"
        )

    industry_data = (analysis.get("valuation") or {}).get("industry") or {}
    industry = (
        industry_data.get("em_industry")
        or industry_data.get("csrc_industry")
        or assessment.get("profile")
        or "unknown"
    )
    snapshot = AssessmentSnapshot(
        asset_type=required_asset_type(analysis.get("asset_type")),
        code=str(analysis.get("code") or "").strip(),
        name=analysis.get("name"),
        profile=str(assessment.get("profile") or "unknown"),
        industry=str(industry),
        as_of=as_of,
        model_version=str(assessment.get("model_version") or "unknown"),
        valuation_score=optional_float(valuation.get("score")),
        valuation_confidence=confidence_value(valuation),
        quality_score=optional_float(quality.get("score")),
        quality_confidence=confidence_value(quality),
        product_score=optional_float(product.get("score")) if product else None,
        product_confidence=confidence_value(product) if product else 0.0,
        overall_confidence=bounded_float(
            assessment.get("overall_confidence"), "overall_confidence"
        ),
        point_in_time_verified=True,
        provenance=str(provenance.get("method") or "").strip(),
        source_dates=tuple(sorted(collect_source_dates(assessment))),
    )
    if not snapshot.code:
        raise BacktestDataError("analysis code is required")
    snapshot.validate_point_in_time()
    return snapshot


def collect_source_dates(value: Any) -> set[date]:
    dates: set[date] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "retrieved_at":
                continue
            if (
                key in {"source_as_of", "report_date", "available_from", "published_at"}
                or key.endswith("_report_date")
            ):
                parsed = parse_optional_date(child)
                if parsed is not None:
                    dates.add(parsed)
            dates.update(collect_source_dates(child))
    elif isinstance(value, list | tuple):
        for child in value:
            dates.update(collect_source_dates(child))
    return dates


def dimension_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BacktestDataError(f"assessment {name} dimension is required")
    return value


def optional_dimension_dict(value: Any, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return dimension_dict(value, name)


def required_asset_type(value: Any) -> Literal["stock", "fund"]:
    if value not in {"stock", "fund"}:
        raise BacktestDataError("asset_type must be stock or fund")
    return value


def confidence_value(dimension: dict[str, Any] | None) -> float:
    if dimension is None:
        return 0.0
    return bounded_float(dimension.get("confidence"), "dimension confidence")


def bounded_float(value: Any, name: str) -> float:
    parsed = optional_float(value)
    if parsed is None or not 0 <= parsed <= 1:
        raise BacktestDataError(f"{name} must be between 0 and 1")
    return parsed


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BacktestDataError(f"expected numeric value, got {value!r}")
    parsed = float(value)
    if not isfinite(parsed):
        raise BacktestDataError("numeric value must be finite")
    return parsed


def parse_required_date(value: Any, name: str) -> date:
    parsed = parse_optional_date(value)
    if parsed is None:
        raise BacktestDataError(f"{name} must be an ISO date")
    return parsed


def parse_optional_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise BacktestDataError(f"expected ISO date, got {value!r}")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise BacktestDataError(f"invalid ISO date: {value}") from exc
