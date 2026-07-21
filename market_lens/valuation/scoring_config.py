from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SCHEMA_VERSION = "2"
MODEL_VERSION = "valuation-v2.0.0-infrastructure"

DimensionCategory = Literal["valuation", "quality", "product"]
FactorDirection = Literal["lower_is_better", "higher_is_better"]
NormalizationMethod = Literal[
    "historical_percentile",
    "pre_normalized_percentile",
    "linear_anchor",
]


@dataclass(frozen=True)
class FactorDefinition:
    key: str
    name: str
    category: DimensionCategory
    unit: str
    weight: float
    direction: FactorDirection
    normalization: NormalizationMethod
    minimum_sample_size: int = 1
    full_sample_size: int | None = None
    positive_only: bool = False
    core: bool = False
    anchor_min: float | None = None
    anchor_max: float | None = None

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("factor weight must be positive")
        if self.minimum_sample_size < 1:
            raise ValueError("minimum sample size must be at least 1")
        if self.full_sample_size is not None and self.full_sample_size < self.minimum_sample_size:
            raise ValueError("full sample size cannot be below minimum sample size")
        if self.normalization == "linear_anchor":
            if self.anchor_min is None or self.anchor_max is None:
                raise ValueError("linear anchor factors require anchor_min and anchor_max")
            if self.anchor_max <= self.anchor_min:
                raise ValueError("anchor_max must be greater than anchor_min")


STOCK_VALUATION_FACTORS = (
    FactorDefinition(
        key="pe_ttm",
        name="PE TTM",
        category="valuation",
        unit="multiple",
        weight=0.40,
        direction="lower_is_better",
        normalization="historical_percentile",
        minimum_sample_size=252,
        full_sample_size=1200,
        positive_only=True,
        core=True,
    ),
    FactorDefinition(
        key="pb",
        name="PB",
        category="valuation",
        unit="multiple",
        weight=0.30,
        direction="lower_is_better",
        normalization="historical_percentile",
        minimum_sample_size=252,
        full_sample_size=1200,
        positive_only=True,
        core=True,
    ),
    FactorDefinition(
        key="ps_ttm",
        name="PS TTM",
        category="valuation",
        unit="multiple",
        weight=0.15,
        direction="lower_is_better",
        normalization="historical_percentile",
        minimum_sample_size=252,
        full_sample_size=1200,
        positive_only=True,
    ),
    FactorDefinition(
        key="pcf_ocf_ttm",
        name="PCF OCF TTM",
        category="valuation",
        unit="multiple",
        weight=0.15,
        direction="lower_is_better",
        normalization="historical_percentile",
        minimum_sample_size=252,
        full_sample_size=1200,
        positive_only=True,
    ),
)

FUND_VALUATION_FACTOR_DIRECTIONS: dict[str, FactorDirection] = {
    "pe_historical_percentile": "lower_is_better",
    "pb_historical_percentile": "lower_is_better",
    "peer_pe_percentile": "lower_is_better",
    "dividend_yield": "higher_is_better",
    "index_price_percentile": "lower_is_better",
}

LEGACY_FUND_FACTOR_WEIGHTS: dict[str, dict[str, float]] = {
    "generic_fund": {
        "pe_historical_percentile": 0.35,
        "pb_historical_percentile": 0.30,
        "peer_pe_percentile": 0.20,
        "dividend_yield": 0.15,
    },
    "dividend_low_volatility_fund": {
        "dividend_yield": 0.40,
        "pb_historical_percentile": 0.25,
        "pe_historical_percentile": 0.20,
        "peer_pe_percentile": 0.15,
    },
    "index_etf": {"index_price_percentile": 1.0},
}
