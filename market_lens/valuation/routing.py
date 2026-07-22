from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from market_lens.types import AssetType, ReitProfile, StockProfile

RoutedAssetType = Literal["stock", "fund", "reit"]
MainModelKey = Literal[
    "generic_non_financial",
    "bank",
    "insurance",
    "securities",
    "real_estate_developer",
    "cyclical_steel",
    "cyclical_nonferrous",
    "cyclical_nonferrous_financial",
    "cyclical_coking_coal",
    "cyclical_coal_financial",
    "regulated_utility_basic",
    "technology_rd",
    "medical_device",
    "mature_pharma",
    "reit_basic",
    "etf",
    "etf_linked",
    "index_fund",
    "active_fund",
    "fund_unclassified",
]
StyleOverlayKey = Literal["dividend_quality_overlay", "growth_quality_overlay"]
CommodityExposureKey = Literal[
    "copper",
    "aluminum",
    "gold",
    "coking_coal",
    "thermal_coal",
]
CommodityMappingMethod = Literal[
    "structured_business_segment",
    "manual_audited_mapping",
]
FundProductProfile = Literal["etf", "etf_linked", "index_fund", "active_fund"]

FINANCIAL_SCOPE_MODELS: dict[str, MainModelKey] = {
    "general_non_financial": "generic_non_financial",
    "bank": "bank",
    "insurance": "insurance",
    "securities": "securities",
}
FINANCIAL_SPECIALIST_MODELS = frozenset({"bank", "insurance", "securities"})
FUND_PRODUCT_MODELS = frozenset({"etf", "etf_linked", "index_fund", "active_fund"})
NONFERROUS_COMMODITY_EXPOSURES = frozenset({"copper", "aluminum", "gold"})
COMMODITY_EXPOSURES = NONFERROUS_COMMODITY_EXPOSURES | {
    "coking_coal",
    "thermal_coal",
}
COMMODITY_MAPPING_METHODS = frozenset(
    {"structured_business_segment", "manual_audited_mapping"}
)


@dataclass(frozen=True)
class CommodityEquityMapping:
    exposure: CommodityExposureKey
    method: CommodityMappingMethod
    source: str
    as_of: date

    def __post_init__(self) -> None:
        if self.exposure not in COMMODITY_EXPOSURES:
            raise ValueError(f"unsupported commodity exposure: {self.exposure}")
        if self.method not in COMMODITY_MAPPING_METHODS:
            raise ValueError(f"unsupported commodity mapping method: {self.method}")
        if not self.source.strip():
            raise ValueError("commodity mapping source must not be empty")


@dataclass(frozen=True)
class StyleRoutingEvidence:
    source: str
    as_of: date
    ruleset_version: str
    dividend_quality_eligible: bool = False
    growth_quality_eligible: bool = False


@dataclass(frozen=True)
class DeterministicAssetRoute:
    asset_type: RoutedAssetType
    main_model: MainModelKey
    style_overlays: tuple[StyleOverlayKey, ...]
    reason: str
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    scoring_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class IndustryClassification:
    model: MainModelKey | None
    source: str | None
    value: str | None
    warnings: tuple[str, ...] = ()


def route_asset_model(
    *,
    declared_asset_type: AssetType,
    stock_profile: StockProfile | None = None,
    financial_scope: str | None = None,
    fund_product_profile: FundProductProfile | None = None,
    reit_profile: ReitProfile | None = None,
    commodity_mapping: CommodityEquityMapping | None = None,
    style_evidence: StyleRoutingEvidence | None = None,
) -> DeterministicAssetRoute:
    if declared_asset_type not in {"stock", "fund"}:
        raise ValueError("declared_asset_type must be 'stock' or 'fund'")

    if reit_profile is not None:
        validate_reit_routing_profile(reit_profile)
        warnings = []
        if declared_asset_type != "fund":
            warnings.append(
                f"declared_asset_type_conflict:{declared_asset_type}!=reit"
            )
        if commodity_mapping is not None:
            warnings.append("commodity_mapping_not_applicable:reit")
        warnings.extend(suppressed_non_stock_style_warnings(style_evidence, "reit"))
        return DeterministicAssetRoute(
            asset_type="reit",
            main_model="reit_basic",
            style_overlays=(),
            reason="eastmoney_exact_reit_type",
            evidence=(
                f"fund_code:{reit_profile.fund_code}",
                f"fund_type:{reit_profile.fund_type}",
                f"profile_source:{reit_profile.source}",
            ),
            warnings=tuple(warnings),
        )

    if declared_asset_type == "fund":
        warnings = suppressed_non_stock_style_warnings(style_evidence, "fund")
        if commodity_mapping is not None:
            warnings.append("commodity_mapping_not_applicable:fund")
        if fund_product_profile in FUND_PRODUCT_MODELS:
            main_model: MainModelKey = fund_product_profile
            reason = "existing_fund_product_classification"
            evidence = (f"fund_product_profile:{fund_product_profile}",)
        else:
            main_model = "fund_unclassified"
            reason = "fund_product_classification_unavailable"
            evidence = ()
            warnings.append("fund_product_profile_unavailable")
        return DeterministicAssetRoute(
            asset_type="fund",
            main_model=main_model,
            style_overlays=(),
            reason=reason,
            evidence=evidence,
            warnings=tuple(warnings),
        )

    return route_stock_asset(
        stock_profile=stock_profile,
        financial_scope=financial_scope,
        commodity_mapping=commodity_mapping,
        style_evidence=style_evidence,
    )


def route_stock_asset(
    *,
    stock_profile: StockProfile | None,
    financial_scope: str | None,
    commodity_mapping: CommodityEquityMapping | None,
    style_evidence: StyleRoutingEvidence | None,
) -> DeterministicAssetRoute:
    normalized_scope = str(financial_scope or "unknown").strip()
    financial_model = FINANCIAL_SCOPE_MODELS.get(normalized_scope)
    classification = classify_stock_industry(stock_profile)
    warnings = list(classification.warnings)
    evidence: list[str] = []

    if financial_model in FINANCIAL_SPECIALIST_MODELS:
        main_model = financial_model
        reason = "financial_org_type"
        evidence.append(f"financial_scope:{normalized_scope}")
        if classification.model not in {None, main_model}:
            warnings.append(
                "financial_industry_classification_conflict:"
                f"{main_model}!={classification.model}"
            )
        if commodity_mapping is not None:
            warnings.append(
                f"commodity_mapping_ignored_for_financial_specialist:"
                f"{commodity_mapping.exposure}"
            )
    elif classification.model is not None:
        main_model, commodity_reason, commodity_warnings = apply_commodity_route(
            classification.model,
            commodity_mapping,
        )
        reason = commodity_reason or "industry_classification"
        warnings.extend(commodity_warnings)
        if classification.source and classification.value:
            evidence.append(
                f"{classification.source}:{normalize_industry_text(classification.value)}"
            )
        if commodity_mapping is not None and commodity_reason is not None:
            evidence.extend(serialize_commodity_mapping_evidence(commodity_mapping))
        if normalized_scope == "unknown":
            warnings.append("financial_org_type_unavailable")
        elif normalized_scope not in FINANCIAL_SCOPE_MODELS:
            warnings.append(f"unsupported_financial_scope:{normalized_scope}")
    else:
        main_model = "generic_non_financial"
        reason = "generic_fallback"
        if commodity_mapping is not None:
            warnings.append(
                "commodity_mapping_ignored_without_supported_industry:"
                f"{commodity_mapping.exposure}"
            )
        if normalized_scope == "unknown":
            warnings.append("financial_org_type_unavailable")
        elif normalized_scope not in FINANCIAL_SCOPE_MODELS:
            warnings.append(f"unsupported_financial_scope:{normalized_scope}")

    overlays, style_warnings, style_evidence_items = resolve_style_overlays(
        style_evidence
    )
    warnings.extend(style_warnings)
    evidence.extend(style_evidence_items)
    return DeterministicAssetRoute(
        asset_type="stock",
        main_model=main_model,
        style_overlays=overlays,
        reason=reason,
        evidence=tuple(evidence),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def validate_reit_routing_profile(profile: ReitProfile) -> None:
    if profile.fund_type.strip().casefold() != "reits":
        raise ValueError("reit_profile must have exact normalized FTYPE=Reits")
    if not re.fullmatch(r"\d{6}", profile.fund_code):
        raise ValueError("reit_profile must include a six-digit fund code")
    if profile.fund_code.startswith("180"):
        expected_quote_id = f"0.{profile.fund_code}"
        expected_exchange = "SZSE"
    elif profile.fund_code.startswith("508"):
        expected_quote_id = f"1.{profile.fund_code}"
        expected_exchange = "SSE"
    else:
        raise ValueError("reit_profile has an unsupported verified exchange code route")
    if (
        profile.quote_id != expected_quote_id
        or profile.exchange != expected_exchange
    ):
        raise ValueError("reit_profile exchange quote route does not match its fund code")
    if profile.source != "eastmoney_fund_mobile":
        raise ValueError("reit_profile must come from eastmoney_fund_mobile")


def classify_stock_industry(
    profile: StockProfile | None,
) -> IndustryClassification:
    if profile is None:
        return IndustryClassification(None, None, None)

    candidates: list[tuple[str, str, MainModelKey]] = []
    warnings: list[str] = []
    for source, raw_value in (
        ("em_industry", profile.em_industry),
        ("csrc_industry", profile.csrc_industry),
    ):
        model, classification_warnings = classify_industry_text(raw_value)
        warnings.extend(f"{source}:{warning}" for warning in classification_warnings)
        if model is not None and raw_value:
            candidates.append((source, raw_value, model))

    models = {candidate[2] for candidate in candidates}
    if len(models) > 1:
        warnings.append(
            "industry_classification_conflict:"
            + "!=".join(sorted(models))
        )
        return IndustryClassification(None, None, None, tuple(warnings))
    if not candidates:
        return IndustryClassification(None, None, None, tuple(warnings))

    source, value, model = candidates[0]
    return IndustryClassification(model, source, value, tuple(warnings))


def classify_industry_text(
    value: str | None,
) -> tuple[MainModelKey | None, tuple[str, ...]]:
    normalized = normalize_industry_text(value)
    if not normalized:
        return None, ()
    if contains_any(normalized, ("物业管理", "物业服务", "房地产服务")):
        return None, ("real_estate_service_excluded",)
    if contains_any(normalized, ("创新药", "生物科技")):
        return None, ("innovative_pharma_model_unavailable",)

    rules: tuple[tuple[MainModelKey, tuple[str, ...]], ...] = (
        ("bank", ("银行",)),
        ("insurance", ("保险",)),
        ("securities", ("证券", "券商")),
        ("real_estate_developer", ("房地产开发", "房地产开发经营")),
        ("cyclical_steel", ("钢铁", "普钢", "特钢", "黑色金属冶炼和压延")),
        ("cyclical_nonferrous", ("有色金属", "工业金属", "贵金属", "小金属", "能源金属")),
        ("cyclical_coking_coal", ("煤炭", "焦煤", "煤炭开采和洗选")),
        (
            "regulated_utility_basic",
            (
                "公用事业",
                "电力行业",
                "电力热力生产和供应",
                "燃气生产和供应",
                "水务行业",
                "水的生产和供应",
            ),
        ),
        ("medical_device", ("医疗器械",)),
        ("mature_pharma", ("化学制药", "中药", "生物制品", "医药制造")),
        (
            "technology_rd",
            (
                "半导体",
                "电子元件",
                "消费电子",
                "光学光电子",
                "计算机设备",
                "软件开发",
                "软件和信息技术服务",
                "通信设备",
                "自动化设备",
            ),
        ),
    )
    matches = [model for model, tokens in rules if contains_any(normalized, tokens)]
    unique_matches = tuple(dict.fromkeys(matches))
    if len(unique_matches) > 1:
        return None, (
            "industry_text_matches_multiple_models:" + "!=".join(unique_matches),
        )
    return (unique_matches[0], ()) if unique_matches else (None, ())


def apply_commodity_route(
    industry_model: MainModelKey,
    mapping: CommodityEquityMapping | None,
) -> tuple[MainModelKey, str | None, list[str]]:
    if industry_model == "cyclical_nonferrous":
        if mapping is not None and mapping.exposure in NONFERROUS_COMMODITY_EXPOSURES:
            return industry_model, "industry_and_explicit_commodity_mapping", []
        warnings = ["nonferrous_commodity_mapping_unavailable_financial_fallback"]
        if mapping is not None:
            warnings.append(
                f"nonferrous_commodity_mapping_mismatch:{mapping.exposure}"
            )
        return "cyclical_nonferrous_financial", "industry_financial_fallback", warnings

    if industry_model == "cyclical_coking_coal":
        if mapping is not None and mapping.exposure == "coking_coal":
            return industry_model, "industry_and_explicit_commodity_mapping", []
        warnings = ["coking_coal_mapping_unavailable_financial_fallback"]
        if mapping is not None and mapping.exposure == "thermal_coal":
            warnings.append("thermal_coal_current_price_history_unavailable")
        elif mapping is not None:
            warnings.append(f"coal_commodity_mapping_mismatch:{mapping.exposure}")
        return "cyclical_coal_financial", "industry_financial_fallback", warnings

    if mapping is not None:
        return industry_model, None, [
            f"commodity_mapping_not_applicable:{mapping.exposure}"
        ]
    return industry_model, None, []


def serialize_commodity_mapping_evidence(
    mapping: CommodityEquityMapping,
) -> tuple[str, ...]:
    return (
        f"commodity_exposure:{mapping.exposure}",
        f"commodity_mapping_method:{mapping.method}",
        f"commodity_mapping_source:{mapping.source.strip()}",
        f"commodity_mapping_as_of:{mapping.as_of.isoformat()}",
    )


def resolve_style_overlays(
    evidence: StyleRoutingEvidence | None,
) -> tuple[tuple[StyleOverlayKey, ...], list[str], list[str]]:
    if evidence is None:
        return (), [], []
    if not evidence.source.strip() or not evidence.ruleset_version.strip():
        return (), ["style_evidence_provenance_incomplete"], []

    overlays: list[StyleOverlayKey] = []
    if evidence.dividend_quality_eligible:
        overlays.append("dividend_quality_overlay")
    if evidence.growth_quality_eligible:
        overlays.append("growth_quality_overlay")
    if not overlays:
        return (), [], []
    return (
        tuple(overlays),
        [],
        [
            f"style_ruleset:{evidence.ruleset_version.strip()}",
            f"style_source:{evidence.source.strip()}",
            f"style_as_of:{evidence.as_of.isoformat()}",
        ],
    )


def suppressed_non_stock_style_warnings(
    evidence: StyleRoutingEvidence | None,
    asset_type: str,
) -> list[str]:
    if evidence and (
        evidence.dividend_quality_eligible or evidence.growth_quality_eligible
    ):
        return [f"stock_style_overlays_not_applicable:{asset_type}"]
    return []


def normalize_industry_text(value: str | None) -> str:
    return re.sub(r"[\s（）()\-_/·]+", "", value or "").strip()


def contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token in value for token in tokens)
