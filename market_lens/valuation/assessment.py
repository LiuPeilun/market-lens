from __future__ import annotations

from datetime import UTC, date, datetime
from math import isfinite
from statistics import mean, pstdev
from typing import Any, cast

from market_lens.types import StockFinancialIndicator, StockProfile, StockValuationPoint
from market_lens.valuation.confidence import (
    calculate_confidence,
    conservative_overall_confidence,
)
from market_lens.valuation.framework import LEVEL_LABELS_ZH, valuation_level
from market_lens.valuation.scoring import FactorObservation, score_dimension
from market_lens.valuation.scoring_config import (
    FUND_PRODUCT_MODEL_CONFIGS,
    FUND_UNDERLYING_QUALITY_FACTOR,
    FUND_VALUATION_FACTOR_DIRECTIONS,
    LEGACY_FUND_FACTOR_WEIGHTS,
    MODEL_VERSION,
    SCHEMA_VERSION,
    STOCK_MODEL_CONFIGS,
    FactorDefinition,
    FundProductModelKey,
    StockModelConfig,
    StockModelKey,
)


def build_stock_assessment(
    valuations: list[StockValuationPoint],
    *,
    analysis_as_of: date | None,
    retrieved_at: datetime,
    factor_data: dict[str, Any],
    industry_valuation: dict[str, Any],
    financials: list[StockFinancialIndicator],
    stock_profile: StockProfile | None,
) -> dict[str, Any]:
    latest = valuations[-1] if valuations else None
    routing = route_stock_model(factor_data, stock_profile)
    model = STOCK_MODEL_CONFIGS[routing["profile"]]

    valuation_observations = build_stock_valuation_observations(
        model,
        valuations,
        industry_valuation,
    )
    valuation_scored = score_dimension(
        model.valuation_factors,
        valuation_observations,
        minimum_effective_weight=model.minimum_valuation_weight,
    )
    valuation_confidence = calculate_confidence(
        {
            "source_success": 1.0 if latest else 0.0,
            "freshness": freshness_component(
                analysis_as_of,
                latest.date if latest else None,
                full_freshness_days=10,
                decay_days=30,
            ),
            "factor_coverage": valuation_scored["weight_coverage"],
            "data_coverage": valuation_scored["data_coverage"],
            "sample_adequacy": valuation_scored["sample_adequacy"],
            "method_quality": 1.0,
        }
    )

    usable_financials = financial_rows_as_of(financials, analysis_as_of)
    quality_observations = build_quality_observations(
        model,
        usable_financials,
        factor_data,
    )
    quality_scored = score_dimension(
        model.quality_factors,
        quality_observations,
        minimum_effective_weight=model.minimum_quality_weight,
    )
    financial_diagnostic = factor_data.get("diagnostic") or {}
    financial_status = str(financial_diagnostic.get("status") or "unavailable")
    financial_source_as_of = (
        usable_financials[-1].date if usable_financials else None
    )
    quality_confidence = calculate_confidence(
        {
            "source_success": source_status_component(financial_status),
            "freshness": freshness_component(
                analysis_as_of,
                financial_source_as_of,
                full_freshness_days=365,
                decay_days=185,
            ),
            "factor_coverage": quality_scored["weight_coverage"],
            "data_coverage": quality_scored["data_coverage"],
            "sample_adequacy": quality_scored["sample_adequacy"],
            "method_quality": 0.8,
        }
    )
    valuation_dimension = build_dimension(
        valuation_scored,
        valuation_confidence["score"],
        category="valuation",
        model=f"{model.key}_valuation_v1",
        extra_warnings=list(model.warnings),
    )
    quality_dimension = build_dimension(
        quality_scored,
        quality_confidence["score"],
        category="quality",
        model=f"{model.key}_quality_v1",
    )
    dimensions = {
        "valuation": valuation_dimension,
        "quality": quality_dimension,
        "product": None,
    }
    sources = [
        {
            "key": "stock_valuation_history",
            "source": "eastmoney_stock_valuation_history",
            "status": "available" if latest else "unavailable",
            "source_as_of": latest.date.isoformat() if latest else None,
            "retrieved_at": utc_isoformat(retrieved_at),
        },
        {
            "key": "financial_factors",
            **financial_diagnostic,
        },
        {
            "key": "industry_valuation",
            "source": industry_valuation.get("source"),
            "status": industry_valuation.get("status"),
            "source_as_of": industry_valuation.get("as_of"),
            "retrieved_at": utc_isoformat(retrieved_at),
            "reason": industry_valuation.get("reason"),
        },
    ]
    warnings = collect_warnings(valuation_scored, sources)
    warnings.extend(quality_scored.get("warnings") or [])
    warnings.extend(routing.get("warnings") or [])
    warnings.extend(model.warnings)
    confidence_detail = combine_dimension_confidence(
        valuation_confidence,
        quality_confidence,
    )
    return build_assessment(
        profile=model.key,
        analysis_as_of=analysis_as_of,
        dimensions=dimensions,
        confidence_detail=confidence_detail,
        sources=sources,
        warnings=list(dict.fromkeys(warnings)),
        source_as_of=latest.date if latest else None,
        retrieved_at=retrieved_at,
        routing=routing,
    )


def build_stock_valuation_observations(
    model: StockModelConfig,
    valuations: list[StockValuationPoint],
    industry_valuation: dict[str, Any],
) -> dict[str, FactorObservation]:
    latest = valuations[-1] if valuations else None
    observations: dict[str, FactorObservation] = {}
    for definition in model.valuation_factors:
        if definition.key.startswith("industry_"):
            metric_key = "pe_ttm" if definition.key == "industry_pe_ttm" else "pb"
            metric = (industry_valuation.get("metrics") or {}).get(metric_key) or {}
            summary_status = str(industry_valuation.get("status") or "unavailable")
            status = source_factor_status(summary_status, metric.get("percentile"))
            sample_size = int(metric.get("valid_sample_size") or 0)
            total_size = int(industry_valuation.get("sample_size") or 0)
            observations[definition.key] = FactorObservation(
                value=to_finite_float(metric.get("percentile")),
                source="eastmoney_industry_valuation_snapshot",
                source_as_of=parse_iso_date(industry_valuation.get("as_of")),
                status=status,
                sample_size=sample_size,
                coverage=sample_size / total_size if total_size else 0.0,
                warnings=tuple(
                    str(value)
                    for value in (metric.get("reason"), industry_valuation.get("reason"))
                    if value
                ),
            )
            continue

        current = getattr(latest, definition.key, None) if latest else None
        history = tuple(getattr(item, definition.key, None) for item in valuations)
        observations[definition.key] = FactorObservation(
            value=to_finite_float(current),
            history=history,
            source="eastmoney_stock_valuation_history",
            source_as_of=latest.date if latest else None,
            status="available" if latest else "missing",
            coverage=1.0 if latest else 0.0,
        )
    return observations


def build_quality_observations(
    model: StockModelConfig,
    financials: list[StockFinancialIndicator],
    factor_data: dict[str, Any],
) -> dict[str, FactorObservation]:
    latest = financials[-1] if financials else None
    diagnostic_status = str(
        (factor_data.get("diagnostic") or {}).get("status") or "unavailable"
    )
    observations: dict[str, FactorObservation] = {}
    for definition in model.quality_factors:
        if definition.key == "roe_stability":
            values = finite_attribute_values(financials, "roe_weighted")
            value = roe_stability(values)
            sample_size = len(values)
        elif definition.key == "new_business_value_growth_pct":
            base_values = finite_attribute_values(financials, "new_business_value_cny")
            growth_values = growth_percentages(base_values)
            value = growth_values[-1] if growth_values else None
            sample_size = len(base_values)
        else:
            values = finite_attribute_values(financials, definition.key)
            value = to_finite_float(getattr(latest, definition.key, None) if latest else None)
            sample_size = len(values)

        observations[definition.key] = FactorObservation(
            value=value,
            source="eastmoney_f10_key_financial_indicators",
            source_as_of=latest.date if latest else None,
            status=quality_factor_status(diagnostic_status, value),
            sample_size=sample_size,
            coverage=1.0 if value is not None else 0.0,
        )
    return observations


def route_stock_model(
    factor_data: dict[str, Any],
    stock_profile: StockProfile | None,
) -> dict[str, Any]:
    financial_scope = str(factor_data.get("model_scope") or "unknown")
    financial_profile = {
        "general_non_financial": "generic_non_financial",
        "bank": "bank",
        "insurance": "insurance",
        "securities": "securities",
    }.get(financial_scope)
    industry_values = [
        stock_profile.em_industry if stock_profile else None,
        stock_profile.csrc_industry if stock_profile else None,
    ]
    industry_text = " ".join(value for value in industry_values if value)
    fallback_profile = industry_profile(industry_text)
    warnings: list[str] = []

    if financial_profile in STOCK_MODEL_CONFIGS:
        selected = financial_profile
        reason = "financial_org_type"
        if fallback_profile != "generic_non_financial" and fallback_profile != selected:
            warnings.append(
                f"financial_industry_classification_conflict:{selected}!={fallback_profile}"
            )
    elif fallback_profile != "generic_non_financial":
        selected = fallback_profile
        reason = "industry_classification_fallback"
        warnings.append("financial_org_type_unavailable")
    else:
        selected = "generic_non_financial"
        reason = "generic_fallback"
        if financial_scope == "unknown":
            warnings.append("financial_org_type_unavailable")

    return {
        "profile": selected,
        "reason": reason,
        "financial_scope": financial_scope,
        "em_industry": stock_profile.em_industry if stock_profile else None,
        "csrc_industry": stock_profile.csrc_industry if stock_profile else None,
        "warnings": warnings,
    }


def industry_profile(industry: str) -> StockModelKey:
    normalized = industry.strip()
    if "银行" in normalized:
        return "bank"
    if "保险" in normalized:
        return "insurance"
    if "证券" in normalized or "券商" in normalized:
        return "securities"
    return "generic_non_financial"


def financial_rows_as_of(
    financials: list[StockFinancialIndicator],
    analysis_as_of: date | None,
) -> list[StockFinancialIndicator]:
    return [
        item
        for item in financials
        if analysis_as_of is None
        or (
            item.date <= analysis_as_of
            and (item.notice_date is None or item.notice_date <= analysis_as_of)
        )
    ]


def finite_attribute_values(
    rows: list[StockFinancialIndicator],
    attribute: str,
) -> list[float]:
    return [
        float(value)
        for row in rows
        if (value := getattr(row, attribute, None)) is not None and isfinite(value)
    ]


def growth_percentages(values: list[float]) -> list[float]:
    return [
        (current / previous - 1) * 100
        for previous, current in zip(values, values[1:], strict=False)
        if previous > 0 and current >= 0
    ]


def roe_stability(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    denominator = max(abs(mean(values)), 5.0)
    return max(0.0, min(1.0, 1 - pstdev(values) / denominator))


def quality_factor_status(status: str, value: float | None) -> str:
    if status == "error":
        return "error"
    if status == "stale":
        return "stale"
    if status == "invalid":
        return "invalid"
    if value is None:
        return "missing"
    return "available"


def source_factor_status(status: str, value: Any) -> str:
    if status == "error":
        return "error"
    if status in {"invalid", "stale"}:
        return status
    if value is None and status in {"unavailable", "empty"}:
        return "missing"
    return "available"


def source_status_component(status: str) -> float:
    return {
        "available": 1.0,
        "partial": 0.75,
        "stale": 0.25,
        "invalid": 0.0,
        "error": 0.0,
        "unavailable": 0.0,
    }.get(status, 0.0)


def to_finite_float(value: Any) -> float | None:
    if not isinstance(value, int | float) or not isfinite(value):
        return None
    return float(value)


def build_fund_assessment(
    result: dict[str, Any],
    *,
    retrieved_at: datetime,
) -> dict[str, Any]:
    valuation = result.get("valuation") or {}
    analysis_as_of = parse_iso_date(result.get("as_of"))
    source_as_of = fund_source_as_of(valuation)
    factors = standardize_legacy_factors(valuation, source_as_of)
    score = valuation.get("score")
    level = valuation_level(score)
    confidence = float(valuation.get("confidence") or 0.0)
    valuation_dimension = {
        "model": "legacy_fund_valuation_v1",
        "score": score,
        "level": level,
        "level_zh": LEVEL_LABELS_ZH[level],
        "confidence": confidence,
        "factors": factors,
        "weight_coverage": float(valuation.get("factor_coverage") or 0.0),
        "data_coverage": float(
            valuation.get("holding_factor_coverage")
            or valuation.get("factor_coverage")
            or 0.0
        ),
        "warnings": [],
    }
    product_data = valuation.get("product_data") or {}
    product_diagnostic = product_data.get("diagnostic") or {}
    holdings_route = valuation.get("holdings_route") or result.get("holdings_route") or {}
    method_quality = 0.6 if valuation.get("status") == "proxy_valuation" else 1.0
    route_quality = fund_route_quality(holdings_route, valuation)
    valuation_confidence = calculate_confidence(
        {
            "legacy_dimension_confidence": confidence,
            "factor_coverage": valuation_dimension["weight_coverage"],
            "route_quality": route_quality,
            "method_quality": method_quality,
        },
        caps=[("index_price_proxy", 0.6)]
        if valuation.get("status") == "proxy_valuation"
        else None,
    )
    valuation_dimension["legacy_confidence"] = confidence
    valuation_dimension["confidence"] = min(confidence, valuation_confidence["score"])

    product_profile = fund_product_profile(product_data, valuation)
    product_model = FUND_PRODUCT_MODEL_CONFIGS[product_profile]
    product_scored = score_dimension(
        product_model.product_factors,
        build_fund_product_observations(product_model.product_factors, product_data),
        minimum_effective_weight=product_model.minimum_product_weight,
    )
    product_confidence = calculate_confidence(
        {
            "source_success": source_status_component(
                str(product_diagnostic.get("status") or "unavailable")
            ),
            "freshness": freshness_component(
                analysis_as_of,
                parse_iso_date(product_diagnostic.get("source_as_of")),
                full_freshness_days=190,
                decay_days=175,
            ),
            "factor_coverage": product_scored["weight_coverage"],
            "data_coverage": product_scored["data_coverage"],
            "sample_adequacy": product_scored["sample_adequacy"],
            "method_quality": fund_product_method_quality(product_data),
        },
        caps=fund_product_confidence_caps(product_data),
    )
    product_dimension = build_dimension(
        product_scored,
        product_confidence["score"],
        category="product",
        model=f"{product_profile}_product_v1",
        extra_warnings=list((product_data.get("routing") or {}).get("warnings") or []),
    )

    quality_scored = score_dimension(
        (FUND_UNDERLYING_QUALITY_FACTOR,),
        build_fund_quality_observations(valuation, holdings_route),
        minimum_effective_weight=1.0,
    )
    quality_confidence = calculate_confidence(
        {
            "source_success": 1.0 if quality_scored["weight_coverage"] else 0.0,
            "freshness": freshness_component(
                analysis_as_of,
                parse_iso_date(holdings_route.get("as_of")),
                full_freshness_days=120,
                decay_days=245,
            ),
            "factor_coverage": quality_scored["weight_coverage"],
            "data_coverage": quality_scored["data_coverage"],
            "sample_adequacy": quality_scored["sample_adequacy"],
            "route_quality": fund_route_quality(holdings_route, valuation),
            "method_quality": fund_quality_method_quality(holdings_route),
        },
        caps=fund_quality_confidence_caps(holdings_route),
    )
    quality_dimension = build_dimension(
        quality_scored,
        quality_confidence["score"],
        category="quality",
        model="fund_underlying_holdings_quality_v1",
    )
    dimensions = {
        "valuation": valuation_dimension,
        "quality": quality_dimension,
        "product": product_dimension,
    }
    sources = [
        {
            "key": "fund_valuation_route",
            "source": holdings_route.get("source") or result.get("data_source"),
            "status": "available" if score is not None else "partial",
            "source_as_of": source_as_of.isoformat() if source_as_of else None,
            "retrieved_at": utc_isoformat(retrieved_at),
            "scope": holdings_route.get("scope"),
            "coverage": holdings_route.get("coverage"),
            "reasons": holdings_route.get("fallback_reasons") or [],
        },
        {"key": "fund_product", **product_diagnostic},
        {
            "key": "fund_tracking",
            **(product_data.get("tracking") or {}),
            "retrieved_at": utc_isoformat(retrieved_at),
        },
    ]
    warnings = collect_warnings({"warnings": []}, sources)
    warnings.extend(quality_scored.get("warnings") or [])
    warnings.extend(product_scored.get("warnings") or [])
    warnings.extend((product_data.get("routing") or {}).get("warnings") or [])
    confidence_detail = combine_named_confidence(
        {
            "valuation": valuation_confidence,
            "quality": quality_confidence,
            "product": product_confidence,
        }
    )
    return build_assessment(
        profile=product_profile,
        analysis_as_of=analysis_as_of,
        dimensions=dimensions,
        confidence_detail=confidence_detail,
        sources=sources,
        warnings=list(dict.fromkeys(warnings)),
        source_as_of=source_as_of,
        retrieved_at=retrieved_at,
        routing=product_data.get("routing") or None,
    )


def fund_product_profile(
    product_data: dict[str, Any],
    valuation: dict[str, Any],
) -> FundProductModelKey:
    profile = str(product_data.get("profile") or "")
    if profile in FUND_PRODUCT_MODEL_CONFIGS:
        return cast(FundProductModelKey, profile)
    if valuation.get("profile") in {"index_etf", "index_fund"}:
        return "index_fund"
    return "active_fund"


def build_fund_product_observations(
    definitions: tuple[FactorDefinition, ...],
    product_data: dict[str, Any],
) -> dict[str, FactorObservation]:
    diagnostic = product_data.get("diagnostic") or {}
    diagnostic_status = str(diagnostic.get("status") or "unavailable")
    product_source_as_of = parse_iso_date(diagnostic.get("source_as_of"))
    tracking = product_data.get("tracking") or {}
    tracking_status = str(tracking.get("status") or "unavailable")
    tracking_source_as_of = parse_iso_date(tracking.get("source_as_of"))
    values = {
        "total_annual_fee_pct": (product_data.get("fees") or {}).get(
            "total_annual_fee_pct"
        ),
        "period_end_net_assets_cny": (product_data.get("scale") or {}).get(
            "period_end_net_assets_cny"
        ),
        "tracking_error_annualized": tracking.get("tracking_error_annualized"),
        "tracking_deviation_abs_annualized": tracking.get(
            "tracking_deviation_abs_annualized"
        ),
    }
    observations: dict[str, FactorObservation] = {}
    for definition in definitions:
        is_tracking = definition.key.startswith("tracking_")
        value = to_finite_float(values.get(definition.key))
        status = (
            factor_source_status(tracking_status, value)
            if is_tracking
            else factor_source_status(diagnostic_status, value)
        )
        observations[definition.key] = FactorObservation(
            value=value,
            source=(
                "eastmoney_fund_nav_and_tracked_index_history"
                if is_tracking
                else "eastmoney_fund_mobile_detail"
            ),
            source_as_of=tracking_source_as_of if is_tracking else product_source_as_of,
            status=status,
            sample_size=int(tracking.get("sample_size") or 0) if is_tracking else 1,
            coverage=1.0 if value is not None else 0.0,
            warnings=tuple(str(item) for item in tracking.get("warnings") or [])
            if is_tracking
            else (),
        )
    return observations


def build_fund_quality_observations(
    valuation: dict[str, Any],
    holdings_route: dict[str, Any],
) -> dict[str, FactorObservation]:
    metric = (
        ((valuation.get("portfolio") or {}).get("metrics") or {}).get(
            "underlying_quality_score"
        )
        or {}
    )
    value = to_finite_float(metric.get("value"))
    return {
        "underlying_quality_score": FactorObservation(
            value=value,
            source=str(holdings_route.get("source") or "unavailable"),
            source_as_of=parse_iso_date(holdings_route.get("as_of")),
            status="available" if value is not None else "missing",
            sample_size=int(metric.get("sample_size") or 0),
            coverage=float(metric.get("coverage") or 0.0),
        )
    }


def factor_source_status(status: str, value: float | None) -> str:
    if status in {"error", "stale", "invalid"}:
        return status
    if status == "not_applicable":
        return "not_applicable"
    return "available" if value is not None else "missing"


def fund_product_method_quality(product_data: dict[str, Any]) -> float:
    tracking = product_data.get("tracking") or {}
    if tracking.get("status") == "not_applicable":
        return 1.0
    if tracking.get("status") != "available":
        return 0.5
    return 0.8 if tracking.get("warnings") else 1.0


def fund_product_confidence_caps(
    product_data: dict[str, Any],
) -> list[tuple[str, float]]:
    warnings = set((product_data.get("tracking") or {}).get("warnings") or [])
    if "target_etf_nav_return_proxy" in warnings:
        return [("target_etf_nav_return_proxy", 0.75)]
    if "tracked_index_price_return_proxy" in warnings:
        return [("tracked_index_price_return_proxy", 0.8)]
    return []


def fund_quality_method_quality(route: dict[str, Any]) -> float:
    return {
        "tracked_index_top10": 0.9,
        "target_etf_top10": 0.7,
        "fund_direct_top10": 0.65,
    }.get(str(route.get("scope") or ""), 0.0)


def fund_quality_confidence_caps(
    route: dict[str, Any],
) -> list[tuple[str, float]]:
    scope = route.get("scope")
    if scope == "tracked_index_top10":
        return [("top_index_constituents_only", 0.8)]
    if scope == "target_etf_top10":
        return [("target_etf_top_holdings_only", 0.65)]
    if scope == "fund_direct_top10":
        return [("fund_top_holdings_only", 0.6)]
    return []


def build_dimension(
    scored: dict[str, Any],
    confidence: float,
    *,
    category: str,
    model: str,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    score = scored.get("score")
    if category in {"quality", "product"}:
        level, level_zh = quality_level(score)
    else:
        level = valuation_level(score)
        level_zh = LEVEL_LABELS_ZH[level]
    return {
        "model": model,
        "score": score,
        "level": level,
        "level_zh": level_zh,
        "confidence": confidence,
        "factors": scored.get("factors", []),
        "weight_coverage": scored.get("weight_coverage", 0.0),
        "data_coverage": scored.get("data_coverage", 0.0),
        "sample_adequacy": scored.get("sample_adequacy", 0.0),
        "warnings": list(
            dict.fromkeys([*(scored.get("warnings") or []), *(extra_warnings or [])])
        ),
    }


def quality_level(score: float | None) -> tuple[str, str]:
    if score is None:
        return "unknown", "未知"
    if score >= 80:
        return "very_high", "很高"
    if score >= 60:
        return "high", "较高"
    if score >= 40:
        return "moderate", "中等"
    if score >= 20:
        return "low", "较低"
    return "very_low", "很低"


def combine_dimension_confidence(
    valuation: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    return combine_named_confidence({"valuation": valuation, "quality": quality})


def combine_named_confidence(
    details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scores = [float(detail.get("score") or 0.0) for detail in details.values()]
    return {
        "score": round(min(scores), 4) if scores else 0.0,
        "components": {
            dimension: detail.get("score", 0.0)
            for dimension, detail in details.items()
        },
        "caps": [
            {"dimension": dimension, **cap}
            for dimension, detail in details.items()
            for cap in detail.get("caps", [])
        ],
        "reasons": [
            f"{dimension}:{reason}"
            for dimension, detail in details.items()
            for reason in detail.get("reasons", [])
        ],
        "dimensions": details,
    }


def build_assessment(
    *,
    profile: str,
    analysis_as_of: date | None,
    dimensions: dict[str, dict[str, Any] | None],
    confidence_detail: dict[str, Any],
    sources: list[dict[str, Any]],
    warnings: list[str],
    source_as_of: date | None,
    retrieved_at: datetime,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "profile": profile,
        "analysis_as_of": analysis_as_of.isoformat() if analysis_as_of else None,
        "dimensions": dimensions,
        "overall_confidence": conservative_overall_confidence(dimensions),
        "attractiveness": None,
        "confidence_detail": confidence_detail,
        "data_quality": {
            "sources": sources,
            "warnings": warnings,
            "source_as_of": source_as_of.isoformat() if source_as_of else None,
            "retrieved_at": utc_isoformat(retrieved_at),
        },
    }
    if routing is not None:
        result["routing"] = routing
    return result


def standardize_legacy_factors(
    valuation: dict[str, Any],
    source_as_of: date | None,
) -> list[dict[str, Any]]:
    available = valuation.get("factors") or []
    available_weight = sum(float(item.get("weight") or 0.0) for item in available)
    factors = [
        {
            "key": item.get("key"),
            "name": item.get("name"),
            "category": "valuation",
            "value": item.get("value"),
            "unit": legacy_factor_unit(str(item.get("key") or "")),
            "source_as_of": source_as_of.isoformat() if source_as_of else None,
            "score": item.get("score"),
            "direction": FUND_VALUATION_FACTOR_DIRECTIONS.get(
                str(item.get("key") or ""), "higher_value_higher_score"
            ),
            "normalization": "legacy_valuation_rule",
            "weight": float(item.get("weight") or 0.0),
            "effective_weight": (
                float(item.get("weight") or 0.0) / available_weight
                if available_weight
                else 0.0
            ),
            "sample_size": legacy_factor_sample_size(valuation),
            "coverage": legacy_factor_coverage(item),
            "source": legacy_factor_source(valuation),
            "status": "available",
            "eligible": item.get("score") is not None,
            "warnings": ["legacy_compatibility_factor"],
        }
        for item in available
    ]
    existing_keys = {item.get("key") for item in factors}
    for key in valuation.get("missing_factors") or []:
        if key in existing_keys:
            continue
        factors.append(
            {
                "key": key,
                "name": key,
                "category": "valuation",
                "value": None,
                "unit": legacy_factor_unit(str(key)),
                "source_as_of": source_as_of.isoformat() if source_as_of else None,
                "score": None,
                "direction": FUND_VALUATION_FACTOR_DIRECTIONS.get(
                    str(key), "higher_value_higher_score"
                ),
                "normalization": "legacy_valuation_rule",
                "weight": legacy_factor_weight(valuation, str(key)),
                "effective_weight": 0.0,
                "sample_size": legacy_factor_sample_size(valuation),
                "coverage": 0.0,
                "source": legacy_factor_source(valuation),
                "status": "missing",
                "eligible": False,
                "warnings": ["factor_missing"],
            }
        )
    return factors


def freshness_component(
    analysis_as_of: date | None,
    source_as_of: date | None,
    full_freshness_days: int,
    decay_days: int = 30,
) -> float:
    if analysis_as_of is None or source_as_of is None:
        return 0.0
    age = (analysis_as_of - source_as_of).days
    if age < 0:
        return 0.0
    if age <= full_freshness_days:
        return 1.0
    return max(0.0, 1 - (age - full_freshness_days) / decay_days)


def fund_source_as_of(valuation: dict[str, Any]) -> date | None:
    index = valuation.get("index") or {}
    holdings = valuation.get("holdings") or {}
    return parse_iso_date(index.get("as_of") or holdings.get("report_date"))


def fund_route_quality(route: dict[str, Any], valuation: dict[str, Any]) -> float:
    if valuation.get("status") == "proxy_valuation":
        return 0.6
    scope = route.get("scope")
    if scope in {"tracked_index_top10", "target_etf_top10", "fund_direct_top10"}:
        return max(0.1, min(float(route.get("coverage") or 0.0), 1.0))
    return 0.0


def legacy_factor_source(valuation: dict[str, Any]) -> str:
    return (
        "tracked_index_price_history"
        if valuation.get("status") == "proxy_valuation"
        else "fund_disclosed_holdings"
    )


def legacy_factor_sample_size(valuation: dict[str, Any]) -> int:
    index = valuation.get("index") or {}
    holdings = valuation.get("holdings") or {}
    return int(index.get("sample_size") or holdings.get("analyzed_count") or 0)


def legacy_factor_unit(key: str) -> str:
    if key == "dividend_yield":
        return "ratio"
    if "percentile" in key:
        return "percentile"
    return "number"


def legacy_factor_weight(valuation: dict[str, Any], key: str) -> float:
    profile = str(valuation.get("profile") or "generic_fund")
    weights = LEGACY_FUND_FACTOR_WEIGHTS.get(profile)
    if weights is None and profile == "index_fund":
        weights = LEGACY_FUND_FACTOR_WEIGHTS["generic_fund"]
    return float((weights or {}).get(key, 0.0))


def legacy_factor_coverage(item: dict[str, Any]) -> float:
    value = item.get("coverage")
    return float(value) if isinstance(value, int | float) else 1.0


def collect_warnings(
    scored: dict[str, Any],
    sources: list[dict[str, Any]],
) -> list[str]:
    warnings = list(scored.get("warnings") or [])
    for source in sources:
        status = source.get("status")
        if status not in {None, "available", "not_applicable"}:
            warnings.append(f"source_{source.get('key')}:{status}")
        warnings.extend(source.get("degradation_reasons") or [])
        reason = source.get("reason")
        if reason:
            warnings.append(str(reason))
        warnings.extend(source.get("reasons") or [])
    return list(dict.fromkeys(str(item) for item in warnings if item))


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def utc_isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
