from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from market_lens.types import StockValuationPoint
from market_lens.valuation.confidence import (
    calculate_confidence,
    conservative_overall_confidence,
)
from market_lens.valuation.framework import LEVEL_LABELS_ZH, valuation_level
from market_lens.valuation.scoring import FactorObservation, score_dimension
from market_lens.valuation.scoring_config import (
    FUND_VALUATION_FACTOR_DIRECTIONS,
    LEGACY_FUND_FACTOR_WEIGHTS,
    MODEL_VERSION,
    SCHEMA_VERSION,
    STOCK_VALUATION_FACTORS,
)


def build_stock_assessment(
    valuations: list[StockValuationPoint],
    *,
    analysis_as_of: date | None,
    retrieved_at: datetime,
    factor_data: dict[str, Any],
    industry_valuation: dict[str, Any],
) -> dict[str, Any]:
    latest = valuations[-1] if valuations else None
    observations = {
        definition.key: FactorObservation(
            value=getattr(latest, definition.key, None) if latest else None,
            history=tuple(getattr(item, definition.key, None) for item in valuations),
            source="eastmoney_stock_valuation_history",
            source_as_of=latest.date if latest else None,
            status="available" if latest else "missing",
            coverage=1.0 if latest else 0.0,
        )
        for definition in STOCK_VALUATION_FACTORS
    }
    scored = score_dimension(STOCK_VALUATION_FACTORS, observations)
    freshness = freshness_component(analysis_as_of, latest.date if latest else None, 10)
    confidence_detail = calculate_confidence(
        {
            "source_success": 1.0 if latest else 0.0,
            "freshness": freshness,
            "factor_coverage": scored["weight_coverage"],
            "data_coverage": scored["data_coverage"],
            "sample_adequacy": scored["sample_adequacy"],
            "method_quality": 1.0,
        }
    )
    dimension = build_dimension(scored, confidence_detail["score"])
    dimensions = {"valuation": dimension, "quality": None, "product": None}
    financial_diagnostic = factor_data.get("diagnostic") or {}
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
    warnings = collect_warnings(scored, sources)
    return build_assessment(
        profile="generic_stock",
        analysis_as_of=analysis_as_of,
        dimensions=dimensions,
        confidence_detail=confidence_detail,
        sources=sources,
        warnings=warnings,
        source_as_of=latest.date if latest else None,
        retrieved_at=retrieved_at,
    )


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
    dimension = {
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
    confidence_detail = calculate_confidence(
        {
            "legacy_dimension_confidence": confidence,
            "factor_coverage": dimension["weight_coverage"],
            "route_quality": route_quality,
            "method_quality": method_quality,
        },
        caps=[("index_price_proxy", 0.6)]
        if valuation.get("status") == "proxy_valuation"
        else None,
    )
    dimension["legacy_confidence"] = confidence
    dimension["confidence"] = min(confidence, confidence_detail["score"])
    dimensions = {"valuation": dimension, "quality": None, "product": None}
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
    ]
    warnings = collect_warnings({"warnings": []}, sources)
    return build_assessment(
        profile=str(valuation.get("profile") or "fund"),
        analysis_as_of=analysis_as_of,
        dimensions=dimensions,
        confidence_detail=confidence_detail,
        sources=sources,
        warnings=warnings,
        source_as_of=source_as_of,
        retrieved_at=retrieved_at,
    )


def build_dimension(scored: dict[str, Any], confidence: float) -> dict[str, Any]:
    level = valuation_level(scored.get("score"))
    return {
        "score": scored.get("score"),
        "level": level,
        "level_zh": LEVEL_LABELS_ZH[level],
        "confidence": confidence,
        "factors": scored.get("factors", []),
        "weight_coverage": scored.get("weight_coverage", 0.0),
        "data_coverage": scored.get("data_coverage", 0.0),
        "sample_adequacy": scored.get("sample_adequacy", 0.0),
        "warnings": scored.get("warnings", []),
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
) -> dict[str, Any]:
    return {
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
                str(item.get("key") or ""), "lower_is_better"
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
                    str(key), "lower_is_better"
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
) -> float:
    if analysis_as_of is None or source_as_of is None:
        return 0.0
    age = (analysis_as_of - source_as_of).days
    if age < 0:
        return 0.0
    if age <= full_freshness_days:
        return 1.0
    return max(0.0, 1 - (age - full_freshness_days) / 30)


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
        if status not in {None, "available"}:
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
