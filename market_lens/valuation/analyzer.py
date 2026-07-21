from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any

from market_lens.types import (
    FundHolding,
    FundNavPoint,
    FundProductInfo,
    StockBar,
    StockDividendPlan,
    StockDividendSummary,
    StockFinancialIndicator,
    StockIndustryValuationSnapshot,
    StockPeerComparison,
    StockProfile,
    StockValuationPoint,
)
from market_lens.valuation.assessment import build_fund_assessment, build_stock_assessment
from market_lens.valuation.framework import analyze_fund_valuation, analyze_stock_valuation
from market_lens.valuation.metrics import (
    annualized_return,
    format_pct,
    max_drawdown,
    percentile_rank,
    simple_return,
    valuation_label,
)


def analyze_stock(
    symbol: str,
    bars: list[StockBar],
    valuations: list[StockValuationPoint],
    name: str | None = None,
    profile: StockProfile | None = None,
    financials: list[StockFinancialIndicator] | None = None,
    peers: dict[str, list[StockPeerComparison]] | None = None,
    dividends: dict[str, list[StockDividendPlan] | list[StockDividendSummary]] | None = None,
    industry_valuation: StockIndustryValuationSnapshot | None = None,
    industry_valuation_error: str | None = None,
    financials_error: str | None = None,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    latest_bar = bars[-1] if bars else None
    latest_valuation = valuations[-1] if valuations else None
    financials = financials or []
    peers = peers or {}
    dividends = dividends or {}
    latest_financial = financials[-1] if financials else None
    retrieved_at = retrieved_at or datetime.now(UTC)

    pe_percentile = percentile_rank(
        [item.pe_ttm for item in valuations],
        latest_valuation.pe_ttm if latest_valuation else None,
    )
    pb_percentile = percentile_rank(
        [item.pb for item in valuations],
        latest_valuation.pb if latest_valuation else None,
    )
    close_values = [item.close for item in bars]

    total_return = None
    annualized = None
    if bars:
        total_return = simple_return(bars[0].close, bars[-1].close)
        annualized = annualized_return(bars[0].close, bars[-1].close, bars[0].date, bars[-1].date)

    valuation_framework = analyze_stock_valuation(valuations)
    peer_summary = summarize_peer_comparison(symbol, peers)
    industry_valuation_summary = summarize_industry_valuation(
        symbol,
        industry_valuation,
        error=industry_valuation_error,
    )
    dividend_summary = summarize_dividends(
        plans=[item for item in dividends.get("plans", []) if isinstance(item, StockDividendPlan)],
        summaries=[
            item
            for item in dividends.get("summaries", [])
            if isinstance(item, StockDividendSummary)
        ],
        latest_price=latest_bar.close if latest_bar else None,
        as_of=latest_bar.date if latest_bar else None,
    )
    fundamental_summary = {
        "as_of": latest_financial.date.isoformat() if latest_financial else None,
        "report_type": latest_financial.report_type if latest_financial else None,
        "roe_weighted": latest_financial.roe_weighted if latest_financial else None,
        "roe_deducted_weighted": (
            latest_financial.roe_deducted_weighted if latest_financial else None
        ),
        "parent_netprofit_growth_pct": (
            latest_financial.parent_netprofit_growth_pct if latest_financial else None
        ),
        "revenue_growth_pct": latest_financial.revenue_growth_pct if latest_financial else None,
        "gross_margin_pct": latest_financial.gross_margin_pct if latest_financial else None,
        "net_margin_pct": latest_financial.net_margin_pct if latest_financial else None,
    }
    factor_data = summarize_financial_factor_data(
        financials,
        analysis_as_of=latest_bar.date if latest_bar else None,
        retrieved_at=retrieved_at,
        error=financials_error,
    )
    industry_summary = {
        "em_industry": profile.em_industry if profile else None,
        "csrc_industry": profile.csrc_industry if profile else None,
        "security_type": profile.security_type if profile else None,
    }

    result = {
        "asset_type": "stock",
        "code": symbol,
        "name": name or latest_valuation.name if latest_valuation else name,
        "as_of": latest_bar.date.isoformat() if latest_bar else None,
        "latest_price": latest_bar.close if latest_bar else None,
        "valuation": {
            "as_of": latest_valuation.date.isoformat() if latest_valuation else None,
            "pe_ttm": latest_valuation.pe_ttm if latest_valuation else None,
            "pb": latest_valuation.pb if latest_valuation else None,
            "pe_ttm_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
            "pe_ttm_label": valuation_label(pe_percentile),
            "pb_label": valuation_label(pb_percentile),
            "industry": industry_summary,
            "fundamentals": fundamental_summary,
            "factor_data": factor_data,
            "peer_comparison": peer_summary,
            "industry_valuation": industry_valuation_summary,
            "dividend": dividend_summary,
            **valuation_framework,
        },
        "performance": {
            "sample_size": len(bars),
            "total_return": total_return,
            "annualized_return": annualized,
            "max_drawdown": max_drawdown(close_values),
            "total_return_text": format_pct(total_return),
            "annualized_return_text": format_pct(annualized),
            "max_drawdown_text": format_pct(max_drawdown(close_values)),
        },
        "notes": [
            "Stock valuation history may not cover a full 10 years for every symbol.",
            "Composite valuation score is based on available historical valuation percentiles.",
            "Stock fundamentals and peer comparison are sourced from Eastmoney F10 when available.",
            "Industry valuation percentiles enter supported stock models when sample gates pass.",
            "Fundamental quality is scored separately and does not alter the valuation level.",
            "This is a research summary, not investment advice.",
        ],
        "latest_raw": asdict(latest_valuation) if latest_valuation else None,
    }
    result["assessment"] = build_stock_assessment(
        valuations,
        analysis_as_of=latest_bar.date if latest_bar else None,
        retrieved_at=retrieved_at,
        factor_data=factor_data,
        industry_valuation=industry_valuation_summary,
        financials=financials,
        stock_profile=profile,
    )
    return result


def summarize_peer_comparison(
    symbol: str,
    peers: dict[str, list[StockPeerComparison]],
) -> dict[str, Any]:
    return {
        "valuation": summarize_peer_table(
            symbol,
            peers.get("valuation", []),
            {
                "pe_ttm": "pe_ttm",
                "pb_mrq": "pb_mrq",
                "peg": "peg",
            },
        ),
        "growth": summarize_peer_table(
            symbol,
            peers.get("growth", []),
            {
                "net_profit_growth_ttm": "net_profit_growth_ttm",
                "revenue_growth_ttm": "revenue_growth_ttm",
            },
        ),
        "dupont": summarize_peer_table(
            symbol,
            peers.get("dupont", []),
            {
                "roe_avg": "roe_avg",
            },
        ),
    }


def summarize_industry_valuation(
    symbol: str,
    snapshot: StockIndustryValuationSnapshot | None,
    error: str | None = None,
    minimum_sample_size: int = 10,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "status": "error" if error else "unavailable",
            "source": "eastmoney_datacenter",
            "as_of": None,
            "board_code": None,
            "board_name": None,
            "industry_level": "eastmoney_valuation_board",
            "parent_fallback_available": False,
            "sample_size": 0,
            "target_present": False,
            "metrics": {},
            "reason": error or "industry_snapshot_unavailable",
        }

    target = next((row for row in snapshot.rows if row.code == symbol), None)
    status = "available" if snapshot.rows else "empty"
    return {
        "status": status,
        "source": snapshot.source,
        "as_of": snapshot.date.isoformat(),
        "board_code": snapshot.board_code,
        "board_name": snapshot.board_name,
        "original_board_code": snapshot.original_board_code,
        "industry_level": "eastmoney_valuation_board",
        "parent_fallback_available": False,
        "sample_size": len(snapshot.rows),
        "target_present": target is not None,
        "minimum_sample_size": minimum_sample_size,
        "metrics": {
            "pe_ttm": summarize_industry_metric(
                snapshot.rows,
                target,
                "pe_ttm",
                minimum_sample_size,
            ),
            "pb": summarize_industry_metric(
                snapshot.rows,
                target,
                "pb",
                minimum_sample_size,
            ),
        },
        "reason": None if snapshot.rows else "industry_snapshot_empty",
    }


def summarize_industry_metric(
    rows: tuple[StockValuationPoint, ...],
    target: StockValuationPoint | None,
    attribute: str,
    minimum_sample_size: int,
) -> dict[str, Any]:
    values = [
        float(value)
        for row in rows
        if isinstance((value := getattr(row, attribute, None)), int | float)
        and isfinite(value)
        and value > 0
    ]
    target_value = getattr(target, attribute, None) if target else None
    target_is_valid = (
        isinstance(target_value, int | float) and isfinite(target_value) and target_value > 0
    )
    eligible = target_is_valid and len(values) >= minimum_sample_size
    reason = None
    if target is None:
        reason = "target_not_in_industry_snapshot"
    elif not target_is_valid:
        reason = "target_value_missing_or_non_positive"
    elif len(values) < minimum_sample_size:
        reason = "insufficient_industry_sample"

    percentile = percentile_rank(values, float(target_value)) if eligible else None
    return {
        "value": float(target_value) if target_is_valid else target_value,
        "percentile": percentile,
        "eligible": eligible,
        "valid_sample_size": len(values),
        "excluded_sample_size": len(rows) - len(values),
        "reason": reason,
    }


def summarize_peer_table(
    symbol: str,
    rows: list[StockPeerComparison],
    fields: dict[str, str],
) -> dict[str, Any]:
    target = next((item for item in rows if item.code == symbol), None)
    values: dict[str, Any] = {
        "sample_size": len(rows),
        "rank": target.rank if target else None,
        "target": asdict(target) if target else None,
        "percentiles": {},
    }
    for output_key, attr in fields.items():
        current = getattr(target, attr, None) if target else None
        values["percentiles"][output_key] = percentile_rank(
            [getattr(item, attr, None) for item in rows],
            current,
        )
    return values


def summarize_dividends(
    plans: list[StockDividendPlan],
    summaries: list[StockDividendSummary],
    latest_price: float | None,
    as_of: Any,
) -> dict[str, Any]:
    trailing_cash = None
    if as_of is not None:
        start_date = as_of - timedelta(days=365)
        cash_values = [
            item.cash_per_share
            for item in plans
            if item.cash_per_share is not None
            and item.ex_dividend_date is not None
            and start_date <= item.ex_dividend_date <= as_of
        ]
        if cash_values:
            trailing_cash = sum(cash_values)

    dividend_yield = (
        trailing_cash / latest_price if trailing_cash is not None and latest_price else None
    )
    return {
        "trailing_12m_cash_per_share": trailing_cash,
        "dividend_yield": dividend_yield,
        "latest_plan": serialize_dividend_plan(plans[0]) if plans else None,
        "latest_year_summary": asdict(summaries[0]) if summaries else None,
    }


def serialize_dividend_plan(plan: StockDividendPlan) -> dict[str, Any]:
    return {
        "notice_date": plan.notice_date.isoformat() if plan.notice_date else None,
        "plan": plan.plan,
        "progress": plan.progress,
        "ex_dividend_date": plan.ex_dividend_date.isoformat() if plan.ex_dividend_date else None,
        "cash_per_share": plan.cash_per_share,
    }


FINANCIAL_COMMON_FIELDS = {
    "roe_weighted_pct": "roe_weighted",
    "roe_deducted_weighted_pct": "roe_deducted_weighted",
    "parent_net_profit_growth_pct": "parent_netprofit_growth_pct",
    "revenue_growth_pct": "revenue_growth_pct",
    "gross_margin_pct": "gross_margin_pct",
    "net_margin_pct": "net_margin_pct",
}
FINANCIAL_SCOPE_FIELDS = {
    "general_non_financial": {
        "roic_pct": "roic_pct",
        "fcff_backward_cny": "fcff_backward_cny",
        "fcff_forward_cny": "fcff_forward_cny",
    },
    "bank": {
        "net_interest_margin_pct": "net_interest_margin_pct",
        "net_interest_spread_pct": "net_interest_spread_pct",
        "non_performing_loan_ratio_pct": "non_performing_loan_ratio_pct",
        "provision_coverage_ratio_pct": "provision_coverage_ratio_pct",
        "capital_adequacy_ratio_pct": "capital_adequacy_ratio_pct",
        "tier1_capital_adequacy_ratio_pct": "tier1_capital_adequacy_ratio_pct",
        "core_tier1_capital_adequacy_ratio_pct": "core_tier1_capital_adequacy_ratio_pct",
    },
    "insurance": {
        "solvency_adequacy_ratio_pct": "solvency_adequacy_ratio_pct",
        "new_business_value_cny": "new_business_value_cny",
        "new_business_value_margin_pct": "new_business_value_margin_pct",
    },
    "securities": {
        "risk_coverage_ratio_pct": "risk_coverage_ratio_pct",
        "liquidity_coverage_ratio_pct": "liquidity_coverage_ratio_pct",
        "net_stable_funding_ratio_pct": "net_stable_funding_ratio_pct",
        "net_capital_to_net_assets_pct": "net_capital_to_net_assets_pct",
    },
}


def summarize_financial_factor_data(
    financials: list[StockFinancialIndicator],
    analysis_as_of: date | None,
    retrieved_at: datetime,
    error: str | None = None,
) -> dict[str, Any]:
    usable_rows = [
        item
        for item in financials
        if analysis_as_of is None
        or (
            item.date <= analysis_as_of
            and (item.notice_date is None or item.notice_date <= analysis_as_of)
        )
    ]
    excluded_future_rows = len(financials) - len(usable_rows)
    latest = usable_rows[-1] if usable_rows else None
    scope = financial_model_scope(latest.org_type if latest else None)
    scope_fields = FINANCIAL_SCOPE_FIELDS.get(scope, {})
    required_values = serialize_financial_fields(latest, scope_fields)
    extra_reasons: list[str] = []
    if excluded_future_rows:
        extra_reasons.append(f"future_dated_rows_excluded:{excluded_future_rows}")
    if latest and latest.notice_date is None:
        extra_reasons.append("publication_date_unavailable")
    if latest and scope == "unknown":
        extra_reasons.append("unsupported_financial_org_type")

    diagnostic = build_source_diagnostic(
        source="eastmoney_f10_key_financial_indicators",
        analysis_as_of=analysis_as_of,
        source_as_of=latest.date if latest else None,
        retrieved_at=retrieved_at,
        required_values=required_values,
        stale_after_days=550,
        error=error,
        extra_reasons=extra_reasons,
    )
    scoring_eligible = scope in FINANCIAL_SCOPE_FIELDS and diagnostic["status"] in {
        "available",
        "partial",
    }
    return {
        "model_scope": scope,
        "org_type": latest.org_type if latest else None,
        "diagnostic": diagnostic,
        "latest": {
            "report_date": latest.date.isoformat() if latest else None,
            "report_type": latest.report_type if latest else None,
            "notice_date": (
                latest.notice_date.isoformat() if latest and latest.notice_date else None
            ),
            "source_updated_at": (
                latest.source_updated_at.isoformat()
                if latest and latest.source_updated_at
                else None
            ),
            "common": serialize_financial_fields(latest, FINANCIAL_COMMON_FIELDS),
            "industry_specific": required_values,
        },
        "history": [
            {
                "report_date": item.date.isoformat(),
                "report_type": item.report_type,
                "notice_date": item.notice_date.isoformat() if item.notice_date else None,
                "values": serialize_financial_fields(item, scope_fields),
            }
            for item in usable_rows
        ],
        "scoring_eligible": scoring_eligible,
        "scoring_reason": (
            "factor_level_model_rules_apply"
            if scoring_eligible
            else f"source_status_{diagnostic['status']}"
        ),
        "fcff_semantics": (
            "Eastmoney annual FCFF_BACK and FCFF_FORWARD source fields; "
            "not treated as TTM and not converted to free-cash-flow yield."
            if scope == "general_non_financial"
            else None
        ),
    }


def financial_model_scope(org_type: str | None) -> str:
    normalized = (org_type or "").strip()
    return {
        "通用": "general_non_financial",
        "银行": "bank",
        "保险": "insurance",
        "证券": "securities",
    }.get(normalized, "unknown")


def serialize_financial_fields(
    row: StockFinancialIndicator | None,
    fields: dict[str, str],
) -> dict[str, float | None]:
    return {
        output_name: getattr(row, attribute, None) if row else None
        for output_name, attribute in fields.items()
    }


def build_source_diagnostic(
    *,
    source: str,
    analysis_as_of: date | None,
    source_as_of: date | None,
    retrieved_at: datetime,
    required_values: dict[str, Any],
    stale_after_days: int,
    error: str | None = None,
    extra_reasons: list[str] | None = None,
) -> dict[str, Any]:
    reasons = list(extra_reasons or [])
    missing_fields = [key for key, value in required_values.items() if value is None]
    age_days = (
        (analysis_as_of - source_as_of).days
        if analysis_as_of is not None and source_as_of is not None
        else None
    )

    if error:
        status = "error"
        reasons.append(f"source_error:{error}")
    elif source_as_of is None:
        status = "unavailable"
        reasons.append("source_data_unavailable")
    elif age_days is not None and age_days < 0:
        status = "invalid"
        reasons.append("source_date_after_analysis_date")
    elif age_days is not None and age_days > stale_after_days:
        status = "stale"
        reasons.append(f"source_stale:{age_days}_days")
    elif missing_fields:
        status = "partial"
    else:
        status = "available"

    if missing_fields:
        reasons.append("missing_fields:" + ",".join(missing_fields))
    return {
        "status": status,
        "source": source,
        "analysis_as_of": analysis_as_of.isoformat() if analysis_as_of else None,
        "source_as_of": source_as_of.isoformat() if source_as_of else None,
        "retrieved_at": retrieved_at.astimezone(UTC).isoformat(),
        "age_days": age_days,
        "stale_after_days": stale_after_days,
        "missing_fields": missing_fields,
        "degradation_reasons": reasons,
    }


def analyze_fund(
    code: str,
    nav_points: list[FundNavPoint],
    name: str | None = None,
    holdings: list[FundHolding] | None = None,
    holding_analyses: dict[str, dict[str, Any]] | None = None,
    product_info: FundProductInfo | None = None,
    product_info_error: str | None = None,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    latest = nav_points[-1] if nav_points else None
    retrieved_at = retrieved_at or datetime.now(UTC)
    nav_values = [item.unit_nav for item in nav_points]
    total_return = None
    annualized = None
    if nav_points:
        total_return = simple_return(nav_points[0].unit_nav, nav_points[-1].unit_nav)
        annualized = annualized_return(
            nav_points[0].unit_nav,
            nav_points[-1].unit_nav,
            nav_points[0].date,
            nav_points[-1].date,
        )

    valuation = analyze_fund_valuation(
        nav_points,
        name=name,
        holdings=holdings,
        holding_analyses=holding_analyses,
    )
    valuation["product_data"] = summarize_fund_product_data(
        product_info,
        analysis_as_of=latest.date if latest else None,
        retrieved_at=retrieved_at,
        error=product_info_error,
    )

    result = {
        "asset_type": "fund",
        "code": code,
        "name": name,
        "as_of": latest.date.isoformat() if latest else None,
        "latest_unit_nav": latest.unit_nav if latest else None,
        "latest_cumulative_nav": latest.cumulative_nav if latest else None,
        "performance": {
            "sample_size": len(nav_points),
            "total_return": total_return,
            "annualized_return": annualized,
            "max_drawdown": max_drawdown(nav_values),
            "total_return_text": format_pct(total_return),
            "annualized_return_text": format_pct(annualized),
            "max_drawdown_text": format_pct(max_drawdown(nav_values)),
        },
        "valuation": valuation,
        "notes": [
            "Fund NAV performance is not the same as holding-level valuation.",
            (
                "Holding-level valuation uses the latest disclosed top holdings "
                "and their reported weights."
            ),
            "ROE and growth are quality context; they are not treated as cheapness scores.",
            "Low top-holdings coverage or an old report date lowers confidence.",
            "This is a research summary, not investment advice.",
        ],
    }
    result["assessment"] = build_fund_assessment(result, retrieved_at=retrieved_at)
    return result


def summarize_fund_product_data(
    product: FundProductInfo | None,
    analysis_as_of: date | None,
    retrieved_at: datetime,
    error: str | None = None,
) -> dict[str, Any]:
    required_values = {
        "management_fee_pct": product.management_fee_pct if product else None,
        "custody_fee_pct": product.custody_fee_pct if product else None,
        "period_end_net_assets_cny": product.period_end_net_assets_cny if product else None,
    }
    diagnostic = build_source_diagnostic(
        source="eastmoney_fund_mobile_detail",
        analysis_as_of=analysis_as_of,
        source_as_of=product.scale_report_date if product else None,
        retrieved_at=retrieved_at,
        required_values=required_values,
        stale_after_days=190,
        error=error,
    )
    return {
        "diagnostic": diagnostic,
        "fund_type": product.fund_type if product else None,
        "establishment_date": (
            product.establishment_date.isoformat()
            if product and product.establishment_date
            else None
        ),
        "benchmark": product.benchmark if product else None,
        "fees": {
            "management_fee_pct": product.management_fee_pct if product else None,
            "custody_fee_pct": product.custody_fee_pct if product else None,
            "sales_service_fee_pct": product.sales_service_fee_pct if product else None,
        },
        "scale": {
            "report_date": (
                product.scale_report_date.isoformat()
                if product and product.scale_report_date
                else None
            ),
            "period_end_net_assets_cny": (
                product.period_end_net_assets_cny if product else None
            ),
            "source_field": "ENDNAV",
        },
        "scoring_eligible": False,
        "scoring_reason": "read_only_pending_product_model_validation",
    }
