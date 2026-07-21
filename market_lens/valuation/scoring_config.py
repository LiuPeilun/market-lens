from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SCHEMA_VERSION = "2"
MODEL_VERSION = "valuation-v2.2.0-fund-product-models"

DimensionCategory = Literal["valuation", "quality", "product"]
FactorDirection = Literal["higher_value_higher_score", "lower_value_higher_score"]
StockModelKey = Literal["generic_non_financial", "bank", "insurance", "securities"]
FundProductModelKey = Literal["etf", "etf_linked", "index_fund", "active_fund"]
NormalizationMethod = Literal[
    "historical_percentile",
    "pre_normalized_percentile",
    "linear_anchor",
    "log_linear_anchor",
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
        if self.normalization in {"linear_anchor", "log_linear_anchor"}:
            if self.anchor_min is None or self.anchor_max is None:
                raise ValueError("linear anchor factors require anchor_min and anchor_max")
            if self.anchor_max <= self.anchor_min:
                raise ValueError("anchor_max must be greater than anchor_min")


@dataclass(frozen=True)
class StockModelConfig:
    key: StockModelKey
    name: str
    valuation_factors: tuple[FactorDefinition, ...]
    quality_factors: tuple[FactorDefinition, ...]
    minimum_valuation_weight: float = 0.5
    minimum_quality_weight: float = 0.6
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FundProductModelConfig:
    key: FundProductModelKey
    name: str
    product_factors: tuple[FactorDefinition, ...]
    minimum_product_weight: float = 0.5


STOCK_VALUATION_FACTORS = (
    FactorDefinition(
        key="pe_ttm",
        name="PE TTM",
        category="valuation",
        unit="multiple",
        weight=0.40,
        direction="higher_value_higher_score",
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
        direction="higher_value_higher_score",
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
        direction="higher_value_higher_score",
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
        direction="higher_value_higher_score",
        normalization="historical_percentile",
        minimum_sample_size=252,
        full_sample_size=1200,
        positive_only=True,
    ),
)


def historical_valuation_factor(
    key: str,
    name: str,
    weight: float,
    *,
    core: bool = False,
) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        name=name,
        category="valuation",
        unit="multiple",
        weight=weight,
        direction="higher_value_higher_score",
        normalization="historical_percentile",
        minimum_sample_size=252,
        full_sample_size=1200,
        positive_only=True,
        core=core,
    )


def industry_valuation_factor(key: str, name: str, weight: float) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        name=name,
        category="valuation",
        unit="percentile",
        weight=weight,
        direction="higher_value_higher_score",
        normalization="pre_normalized_percentile",
        minimum_sample_size=10,
        full_sample_size=30,
    )


def quality_anchor_factor(
    key: str,
    name: str,
    unit: str,
    weight: float,
    anchor_min: float,
    anchor_max: float,
    *,
    lower_is_better: bool = False,
    core: bool = False,
) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        name=name,
        category="quality",
        unit=unit,
        weight=weight,
        direction=(
            "lower_value_higher_score" if lower_is_better else "higher_value_higher_score"
        ),
        normalization="linear_anchor",
        minimum_sample_size=3,
        full_sample_size=5,
        core=core,
        anchor_min=anchor_min,
        anchor_max=anchor_max,
    )


def stability_factor(weight: float) -> FactorDefinition:
    return FactorDefinition(
        key="roe_stability",
        name="ROE stability",
        category="quality",
        unit="score_ratio",
        weight=weight,
        direction="higher_value_higher_score",
        normalization="pre_normalized_percentile",
        minimum_sample_size=3,
        full_sample_size=5,
    )


GENERIC_NON_FINANCIAL_MODEL = StockModelConfig(
    key="generic_non_financial",
    name="通用非金融",
    valuation_factors=(
        historical_valuation_factor("pe_ttm", "PE TTM historical percentile", 0.30, core=True),
        historical_valuation_factor("pb", "PB historical percentile", 0.25, core=True),
        historical_valuation_factor("ps_ttm", "PS TTM historical percentile", 0.10),
        historical_valuation_factor("pcf_ocf_ttm", "PCF historical percentile", 0.10),
        industry_valuation_factor("industry_pe_ttm", "Industry PE percentile", 0.15),
        industry_valuation_factor("industry_pb", "Industry PB percentile", 0.10),
    ),
    quality_factors=(
        quality_anchor_factor("roe_weighted", "Weighted ROE", "percent", 0.25, 5, 25, core=True),
        quality_anchor_factor("roic_pct", "ROIC", "percent", 0.25, 5, 25, core=True),
        quality_anchor_factor(
            "parent_netprofit_growth_pct", "Net profit growth", "percent", 0.15, -20, 30
        ),
        quality_anchor_factor(
            "revenue_growth_pct", "Revenue growth", "percent", 0.15, -10, 25
        ),
        stability_factor(0.20),
    ),
)

BANK_MODEL = StockModelConfig(
    key="bank",
    name="银行",
    valuation_factors=(
        historical_valuation_factor("pb", "PB historical percentile", 0.40, core=True),
        historical_valuation_factor("pe_ttm", "PE TTM historical percentile", 0.20),
        industry_valuation_factor("industry_pb", "Industry PB percentile", 0.25),
        industry_valuation_factor("industry_pe_ttm", "Industry PE percentile", 0.15),
    ),
    quality_factors=(
        quality_anchor_factor("roe_weighted", "Weighted ROE", "percent", 0.25, 5, 18, core=True),
        quality_anchor_factor(
            "net_interest_margin_pct", "Net interest margin", "percent", 0.20, 1, 3, core=True
        ),
        quality_anchor_factor(
            "non_performing_loan_ratio_pct",
            "Non-performing loan ratio",
            "percent",
            0.20,
            0.5,
            2.5,
            lower_is_better=True,
            core=True,
        ),
        quality_anchor_factor(
            "provision_coverage_ratio_pct", "Provision coverage", "percent", 0.15, 100, 400
        ),
        quality_anchor_factor(
            "capital_adequacy_ratio_pct", "Capital adequacy", "percent", 0.10, 10.5, 20
        ),
        stability_factor(0.10),
    ),
)

INSURANCE_MODEL = StockModelConfig(
    key="insurance",
    name="保险",
    valuation_factors=(
        historical_valuation_factor("pb", "PB historical percentile", 0.65, core=True),
        historical_valuation_factor("pe_ttm", "PE TTM historical percentile", 0.35),
    ),
    quality_factors=(
        quality_anchor_factor("roe_weighted", "Weighted ROE", "percent", 0.25, 5, 20, core=True),
        quality_anchor_factor(
            "solvency_adequacy_ratio_pct",
            "Solvency adequacy",
            "percent",
            0.25,
            100,
            250,
            core=True,
        ),
        quality_anchor_factor(
            "new_business_value_growth_pct", "New business value growth", "percent", 0.15, -20, 30
        ),
        quality_anchor_factor(
            "new_business_value_margin_pct", "New business value margin", "percent", 0.15, 10, 50
        ),
        quality_anchor_factor(
            "parent_netprofit_growth_pct", "Net profit growth", "percent", 0.10, -20, 30
        ),
        stability_factor(0.10),
    ),
    warnings=("insurance_pev_unavailable",),
)

SECURITIES_MODEL = StockModelConfig(
    key="securities",
    name="证券",
    valuation_factors=(
        historical_valuation_factor("pb", "PB historical percentile", 0.45, core=True),
        historical_valuation_factor("pe_ttm", "PE TTM historical percentile", 0.25),
        industry_valuation_factor("industry_pb", "Industry PB percentile", 0.20),
        industry_valuation_factor("industry_pe_ttm", "Industry PE percentile", 0.10),
    ),
    quality_factors=(
        quality_anchor_factor("roe_weighted", "Weighted ROE", "percent", 0.20, 3, 15, core=True),
        quality_anchor_factor(
            "risk_coverage_ratio_pct", "Risk coverage", "percent", 0.20, 100, 300, core=True
        ),
        quality_anchor_factor(
            "liquidity_coverage_ratio_pct",
            "Liquidity coverage",
            "percent",
            0.15,
            100,
            250,
            core=True,
        ),
        quality_anchor_factor(
            "net_stable_funding_ratio_pct", "Net stable funding", "percent", 0.15, 100, 180
        ),
        quality_anchor_factor(
            "net_capital_to_net_assets_pct",
            "Net capital to net assets",
            "percent",
            0.15,
            20,
            80,
        ),
        quality_anchor_factor(
            "parent_netprofit_growth_pct", "Net profit growth", "percent", 0.05, -30, 40
        ),
        stability_factor(0.10),
    ),
)

STOCK_MODEL_CONFIGS: dict[StockModelKey, StockModelConfig] = {
    model.key: model
    for model in (
        GENERIC_NON_FINANCIAL_MODEL,
        BANK_MODEL,
        INSURANCE_MODEL,
        SECURITIES_MODEL,
    )
}


def product_factor(
    key: str,
    name: str,
    unit: str,
    weight: float,
    anchor_min: float,
    anchor_max: float,
    *,
    lower_is_better: bool = False,
    minimum_sample_size: int = 1,
    full_sample_size: int | None = None,
    core: bool = False,
    logarithmic: bool = False,
) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        name=name,
        category="product",
        unit=unit,
        weight=weight,
        direction=(
            "lower_value_higher_score" if lower_is_better else "higher_value_higher_score"
        ),
        normalization="log_linear_anchor" if logarithmic else "linear_anchor",
        minimum_sample_size=minimum_sample_size,
        full_sample_size=full_sample_size,
        core=core,
        anchor_min=anchor_min,
        anchor_max=anchor_max,
    )


def index_product_factors(
    fee_max: float,
) -> tuple[FactorDefinition, ...]:
    return (
        product_factor(
            "total_annual_fee_pct",
            "Annual operating fee",
            "percent",
            0.30,
            0.1,
            fee_max,
            lower_is_better=True,
            core=True,
        ),
        product_factor(
            "period_end_net_assets_cny",
            "Period-end net assets",
            "cny",
            0.20,
            100_000_000,
            10_000_000_000,
            logarithmic=True,
        ),
        product_factor(
            "tracking_error_annualized",
            "Annualized tracking error",
            "ratio",
            0.30,
            0.002,
            0.04,
            lower_is_better=True,
            minimum_sample_size=60,
            full_sample_size=252,
            core=True,
        ),
        product_factor(
            "tracking_deviation_abs_annualized",
            "Absolute annualized tracking deviation",
            "ratio",
            0.20,
            0.0,
            0.04,
            lower_is_better=True,
            minimum_sample_size=60,
            full_sample_size=252,
        ),
    )


ETF_PRODUCT_MODEL = FundProductModelConfig(
    key="etf",
    name="ETF",
    product_factors=index_product_factors(1.0),
)

ETF_LINKED_PRODUCT_MODEL = FundProductModelConfig(
    key="etf_linked",
    name="ETF feeder fund",
    product_factors=index_product_factors(1.5),
)

INDEX_FUND_PRODUCT_MODEL = FundProductModelConfig(
    key="index_fund",
    name="Index fund",
    product_factors=index_product_factors(1.5),
)

ACTIVE_FUND_PRODUCT_MODEL = FundProductModelConfig(
    key="active_fund",
    name="Active fund",
    product_factors=(
        product_factor(
            "total_annual_fee_pct",
            "Annual operating fee",
            "percent",
            0.60,
            0.5,
            2.5,
            lower_is_better=True,
            core=True,
        ),
        product_factor(
            "period_end_net_assets_cny",
            "Period-end net assets",
            "cny",
            0.40,
            100_000_000,
            10_000_000_000,
            logarithmic=True,
        ),
    ),
    minimum_product_weight=0.6,
)

FUND_PRODUCT_MODEL_CONFIGS: dict[FundProductModelKey, FundProductModelConfig] = {
    model.key: model
    for model in (
        ETF_PRODUCT_MODEL,
        ETF_LINKED_PRODUCT_MODEL,
        INDEX_FUND_PRODUCT_MODEL,
        ACTIVE_FUND_PRODUCT_MODEL,
    )
}

FUND_UNDERLYING_QUALITY_FACTOR = FactorDefinition(
    key="underlying_quality_score",
    name="Holdings-weighted fundamental quality",
    category="quality",
    unit="score",
    weight=1.0,
    direction="higher_value_higher_score",
    normalization="linear_anchor",
    minimum_sample_size=1,
    full_sample_size=10,
    core=True,
    anchor_min=0.0,
    anchor_max=100.0,
)

FUND_VALUATION_FACTOR_DIRECTIONS: dict[str, FactorDirection] = {
    "pe_historical_percentile": "higher_value_higher_score",
    "pb_historical_percentile": "higher_value_higher_score",
    "peer_pe_percentile": "higher_value_higher_score",
    "dividend_yield": "lower_value_higher_score",
    "index_price_percentile": "higher_value_higher_score",
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
