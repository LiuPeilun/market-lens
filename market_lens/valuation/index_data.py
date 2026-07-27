from __future__ import annotations

import re
import unicodedata
from datetime import date
from math import isfinite

from market_lens.types import (
    CsiIndexConstituentWeight,
    CsiIndexValuationPoint,
    FundIndexDataRoute,
    FundTrackingInfo,
)
from market_lens.valuation.confidence import calculate_confidence
from market_lens.valuation.framework import (
    LEVEL_LABELS_ZH,
    confidence_label,
    valuation_level,
)
from market_lens.valuation.metrics import percentile_rank

CSI_INDEX_HISTORY_MINIMUM = 504
CSI_INDEX_HISTORY_FULL_SAMPLE = 1260
CSI_INDEX_HISTORY_MAXIMUM = 2520

GENERIC_INDEX_FACTOR_WEIGHTS = {
    "index_pe_ttm_percentile": 0.60,
    "index_pb_percentile": 0.25,
    "index_dividend_yield_percentile": 0.15,
}

DIVIDEND_LOW_VOL_INDEX_FACTOR_WEIGHTS = {
    "index_pe_ttm_percentile": 0.35,
    "index_pb_percentile": 0.25,
    "index_dividend_yield_percentile": 0.40,
}


def build_csi_fund_index_data_route(
    tracking: FundTrackingInfo,
    valuation_points: list[CsiIndexValuationPoint],
    constituent_weights: list[CsiIndexConstituentWeight],
    *,
    analysis_end: date,
) -> FundIndexDataRoute:
    index_code = str(tracking.index_code or "").strip().upper()
    index_name = str(tracking.index_name or "").strip()
    if not index_code or not index_name:
        raise ValueError("tracked_index_identity_incomplete")

    eligible_points = [point for point in valuation_points if point.date <= analysis_end]
    if not eligible_points:
        raise ValueError("official_index_valuation_unavailable_as_of_analysis_date")
    if not constituent_weights:
        raise ValueError("official_index_complete_weights_empty")

    valuation_codes = {point.index_code for point in eligible_points}
    valuation_names = {point.index_name for point in eligible_points}
    valuation_sources = {point.source for point in eligible_points}
    if valuation_codes != {index_code}:
        raise ValueError("official_index_valuation_code_mismatch")
    if not all(index_names_match(index_name, name) for name in valuation_names):
        raise ValueError("official_index_valuation_name_mismatch")
    if not all(source.startswith("csindex_official") for source in valuation_sources):
        raise ValueError("official_index_valuation_source_mismatch")
    if not any(point.pe_ttm is not None for point in eligible_points):
        raise ValueError("official_index_rolling_pe_history_empty")

    weight_codes = {item.index_code for item in constituent_weights}
    weight_names = {item.index_name for item in constituent_weights}
    weight_sources = {item.source for item in constituent_weights}
    weight_dates = {item.report_date for item in constituent_weights}
    if weight_codes != {index_code}:
        raise ValueError("official_index_weights_code_mismatch")
    if not all(index_names_match(index_name, name) for name in weight_names):
        raise ValueError("official_index_weights_name_mismatch")
    if weight_sources != {"csindex_official_closeweight"}:
        raise ValueError("official_index_weights_source_mismatch")
    if len(weight_dates) != 1:
        raise ValueError("official_index_weights_date_mismatch")

    weights_as_of = next(iter(weight_dates))
    if weights_as_of > analysis_end:
        raise ValueError("official_index_weights_after_analysis_date")
    identities = {(item.exchange, item.security_code) for item in constituent_weights}
    if len(identities) != len(constituent_weights):
        raise ValueError("official_index_weights_duplicate_constituent")
    total_weight_pct = sum(item.weight_pct for item in constituent_weights)
    if not 98.0 <= total_weight_pct <= 102.0:
        raise ValueError("official_index_weights_coverage_invalid")

    eligible_points.sort(key=lambda point: point.date)
    return FundIndexDataRoute(
        valuation_points=eligible_points,
        constituent_weights=constituent_weights,
        source="csindex_official",
        scope="tracked_index_fundamentals_and_full_weights",
        tracking=tracking,
        valuation_as_of=eligible_points[-1].date,
        weights_as_of=weights_as_of,
        coverage=round(total_weight_pct / 100.0, 6),
    )


def unavailable_fund_index_data_route(
    tracking: FundTrackingInfo | None,
    *reasons: str,
) -> FundIndexDataRoute:
    return FundIndexDataRoute(
        valuation_points=[],
        constituent_weights=[],
        source="unavailable",
        scope="unavailable",
        tracking=tracking,
        valuation_as_of=None,
        weights_as_of=None,
        coverage=0.0,
        fallback_reasons=tuple(reason for reason in reasons if reason),
    )


def analyze_csi_index_valuation(
    route: FundIndexDataRoute,
    *,
    fund_name: str | None,
    analysis_end: date,
) -> dict[str, object]:
    if route.scope != "tracked_index_fundamentals_and_full_weights":
        raise ValueError("official_index_route_unavailable")
    if route.valuation_as_of is None or route.weights_as_of is None:
        raise ValueError("official_index_route_dates_incomplete")
    if route.valuation_as_of > analysis_end or route.weights_as_of > analysis_end:
        raise ValueError("official_index_route_after_analysis_date")

    profile, profile_name, weights = csi_index_factor_profile(fund_name)
    points = route.valuation_points[-CSI_INDEX_HISTORY_MAXIMUM:]
    latest = points[-1] if points else None
    pe_history = [
        point.pe_ttm
        for point in points
        if point.pe_ttm is not None and isfinite(point.pe_ttm) and point.pe_ttm > 0
    ]
    latest_pe = latest.pe_ttm if latest else None
    pe_percentile = (
        percentile_rank(pe_history, latest_pe)
        if len(pe_history) >= CSI_INDEX_HISTORY_MINIMUM
        and latest_pe is not None
        and isfinite(latest_pe)
        and latest_pe > 0
        else None
    )
    pe_score = pe_percentile * 100 if pe_percentile is not None else None
    pe_weight = weights["index_pe_ttm_percentile"]
    factor_coverage = pe_weight if pe_score is not None else 0.0
    level = valuation_level(pe_score)

    history_adequacy = min(len(pe_history) / CSI_INDEX_HISTORY_FULL_SAMPLE, 1.0)
    valuation_freshness = source_freshness(
        route.valuation_as_of,
        analysis_end,
        full_freshness_days=7,
        stale_after_days=90,
    )
    weights_freshness = source_freshness(
        route.weights_as_of,
        analysis_end,
        full_freshness_days=120,
        stale_after_days=365,
    )
    confidence_detail = calculate_confidence(
        {
            "factor_coverage": factor_coverage,
            "history_adequacy": history_adequacy,
            "method_quality": 0.85,
            "valuation_freshness": valuation_freshness,
            "weight_coverage": min(max(route.coverage, 0.0), 1.0),
            "weights_freshness": weights_freshness,
        },
        caps=[
            ("single_scored_index_factor", 0.60),
            (
                "dividend_history_required_for_low_volatility_profile",
                0.45,
            ),
        ]
        if profile == "csi_dividend_low_volatility_index"
        else [("single_scored_index_factor", 0.60)],
    )
    confidence = confidence_detail["score"] if pe_score is not None else 0.0
    factors = (
        [
            {
                "key": "index_pe_ttm_percentile",
                "name": "指数 PE TTM 历史分位",
                "weight": pe_weight,
                "value": latest_pe,
                "percentile": pe_percentile,
                "score": round(pe_score, 2),
                "level": level,
                "level_zh": LEVEL_LABELS_ZH[level],
                "sample_size": len(pe_history),
                "coverage": 1.0,
                "source": "csindex_official_pe_ttm_history",
                "normalization": "historical_percentile_trailing_10y",
                "unit": "multiple",
                "warnings": [],
            }
        ]
        if pe_score is not None
        else []
    )
    missing_factors = [
        "index_pb_percentile",
        "index_dividend_yield_percentile",
    ]
    tracking = route.tracking
    return {
        "method": "official_index_fundamental_percentile",
        "profile": profile,
        "profile_name": profile_name,
        "score": round(pe_score, 2) if pe_score is not None else None,
        "level": level,
        "level_zh": LEVEL_LABELS_ZH[level],
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "confidence_detail": confidence_detail,
        "factor_coverage": round(factor_coverage, 2),
        "factors": factors,
        "missing_factors": missing_factors,
        "required_future_data": [
            "指数 PB 长期历史",
            "指数股息率长期历史",
            "非中证指数供应商估值历史",
        ],
        "status": (
            "official_index_fundamental_valuation"
            if pe_score is not None
            else "official_index_history_insufficient"
        ),
        "index": {
            "code": tracking.index_code if tracking else None,
            "name": tracking.index_name if tracking else None,
            "source": "csindex_official",
            "as_of": route.valuation_as_of.isoformat(),
            "history_start": points[0].date.isoformat() if points else None,
            "sample_size": len(pe_history),
            "latest_pe_ttm": latest_pe,
            "latest_pe_static_total_capital": (
                latest.pe_static_total_capital if latest else None
            ),
            "latest_pb": latest.pb if latest else None,
            "latest_dividend_yield_pct": (
                latest.dividend_yield_total_capital_pct if latest else None
            ),
            "weights_as_of": route.weights_as_of.isoformat(),
            "constituent_count": len(route.constituent_weights),
            "weight_coverage": route.coverage,
        },
    }


def serialize_fund_index_data_route(route: FundIndexDataRoute) -> dict[str, object]:
    latest = route.valuation_points[-1] if route.valuation_points else None
    tracking = route.tracking
    return {
        "status": "available" if route.scope != "unavailable" else "unavailable",
        "source": route.source,
        "scope": route.scope,
        "scoring_eligible": route.scoring_eligible,
        "index_code": tracking.index_code if tracking else None,
        "index_name": tracking.index_name if tracking else None,
        "valuation_as_of": (
            route.valuation_as_of.isoformat() if route.valuation_as_of else None
        ),
        "valuation_sample_size": len(route.valuation_points),
        "latest_pe_ttm": latest.pe_ttm if latest else None,
        "latest_pe_static_total_capital": (
            latest.pe_static_total_capital if latest else None
        ),
        "latest_pb": latest.pb if latest else None,
        "latest_dividend_yield_pct": (
            latest.dividend_yield_total_capital_pct if latest else None
        ),
        "weights_as_of": route.weights_as_of.isoformat() if route.weights_as_of else None,
        "constituent_count": len(route.constituent_weights),
        "weight_coverage": route.coverage,
        "fallback_reasons": list(route.fallback_reasons),
    }


def index_names_match(expected: str, actual: str) -> bool:
    expected_key = normalize_index_name(expected)
    actual_key = normalize_index_name(actual)
    if not expected_key or not actual_key:
        return False
    if expected_key == actual_key:
        return True
    return expected_key == f"中证{actual_key}" or actual_key == f"中证{expected_key}"


def normalize_index_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"(?:指数|index)$", "", normalized)
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def csi_index_factor_profile(
    fund_name: str | None,
) -> tuple[str, str, dict[str, float]]:
    name = str(fund_name or "")
    if "红利" in name and ("低波" in name or "低波动" in name):
        return (
            "csi_dividend_low_volatility_index",
            "中证红利低波指数",
            DIVIDEND_LOW_VOL_INDEX_FACTOR_WEIGHTS,
        )
    return (
        "csi_index_fundamental",
        "中证指数基本面估值",
        GENERIC_INDEX_FACTOR_WEIGHTS,
    )


def source_freshness(
    source_as_of: date,
    analysis_end: date,
    *,
    full_freshness_days: int,
    stale_after_days: int,
) -> float:
    age = (analysis_end - source_as_of).days
    if age < 0:
        return 0.0
    if age <= full_freshness_days:
        return 1.0
    if age >= stale_after_days:
        return 0.0
    return 1.0 - (age - full_freshness_days) / (
        stale_after_days - full_freshness_days
    )
