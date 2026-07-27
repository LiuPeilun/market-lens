from __future__ import annotations

import re
import unicodedata
from datetime import date

from market_lens.types import (
    CsiIndexConstituentWeight,
    CsiIndexValuationPoint,
    FundIndexDataRoute,
    FundTrackingInfo,
)


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
