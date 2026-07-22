from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any

from market_lens.types import (
    ReitDistribution,
    ReitFinancialSnapshot,
    ReitPeriodicReportNotice,
    ReitPriceBar,
    ReitProfile,
    StockBalanceSheet,
    StockCashFlowStatement,
    StockIncomeStatement,
    StockProfile,
)
from market_lens.valuation.routing import (
    DeterministicAssetRoute,
    FundProductProfile,
    route_asset_model,
)

RESEARCH_SCHEMA_VERSION = "1"
STOCK_BALANCE_DATA_FIELDS = (
    "total_assets_cny",
    "total_current_assets_cny",
    "monetary_funds_cny",
    "accounts_receivable_cny",
    "inventory_cny",
    "contract_asset_cny",
    "total_liabilities_cny",
    "total_current_liabilities_cny",
    "accounts_payable_cny",
    "contract_liability_cny",
    "short_term_borrowings_cny",
    "current_portion_noncurrent_liabilities_cny",
    "long_term_borrowings_cny",
    "bonds_payable_cny",
    "total_equity_cny",
)
STOCK_INCOME_DATA_FIELDS = (
    "total_operating_revenue_cny",
    "operating_cost_cny",
    "sales_expense_cny",
    "management_expense_cny",
    "finance_expense_cny",
    "research_expense_cny",
    "development_expense_cny",
    "operating_profit_cny",
    "parent_net_profit_cny",
    "deducted_parent_net_profit_cny",
    "income_tax_cny",
    "total_operating_revenue_yoy_pct",
    "research_expense_yoy_pct",
    "parent_net_profit_yoy_pct",
)
STOCK_CASH_FLOW_DATA_FIELDS = (
    "sales_services_cash_cny",
    "cash_paid_to_staff_cny",
    "net_operating_cash_flow_cny",
    "capital_expenditure_cash_cny",
    "investment_cash_paid_cny",
    "net_investing_cash_flow_cny",
    "borrowings_received_cash_cny",
    "debt_repaid_cash_cny",
    "dividends_interest_paid_cash_cny",
    "net_financing_cash_flow_cny",
    "cash_equivalents_increase_cny",
    "ending_cash_cny",
)


def build_stock_research_context(
    *,
    analysis_as_of: date,
    stock_profile: StockProfile | None,
    financial_scope: str | None,
    balance_sheets: list[StockBalanceSheet],
    income_statements: list[StockIncomeStatement],
    cash_flow_statements: list[StockCashFlowStatement],
    errors: dict[str, str],
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile,
        financial_scope=financial_scope,
    )
    retrieved_at = retrieved_at or datetime.now(UTC)
    if route.main_model in {"bank", "insurance", "securities"}:
        detailed_financials: dict[str, Any] = {
            "status": "not_applicable",
            "reason": "financial_specialist_uses_existing_key_indicator_adapter",
            "scoring_eligible": False,
        }
    else:
        detailed_financials = {
            "balance_sheet": stock_statement_dataset(
                balance_sheets,
                data_fields=STOCK_BALANCE_DATA_FIELDS,
                analysis_as_of=analysis_as_of,
                error=errors.get("balance_sheet"),
            ),
            "income_statement": stock_statement_dataset(
                income_statements,
                data_fields=STOCK_INCOME_DATA_FIELDS,
                analysis_as_of=analysis_as_of,
                error=errors.get("income_statement"),
            ),
            "cash_flow_statement": stock_statement_dataset(
                cash_flow_statements,
                data_fields=STOCK_CASH_FLOW_DATA_FIELDS,
                analysis_as_of=analysis_as_of,
                error=errors.get("cash_flow_statement"),
            ),
            "scoring_eligible": False,
        }
    return research_context(
        analysis_as_of=analysis_as_of,
        route=route,
        datasets={
            "detailed_financials": detailed_financials,
        },
        retrieved_at=retrieved_at,
    )


def build_fund_research_context(
    *,
    analysis_as_of: date,
    product_profile: FundProductProfile | None,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    route = route_asset_model(
        declared_asset_type="fund",
        fund_product_profile=product_profile,
    )
    return research_context(
        analysis_as_of=analysis_as_of,
        route=route,
        datasets={},
        retrieved_at=retrieved_at or datetime.now(UTC),
    )


def build_reit_research_context(
    *,
    analysis_as_of: date,
    profile: ReitProfile,
    prices: list[ReitPriceBar],
    financials: list[ReitFinancialSnapshot],
    distributions: list[ReitDistribution],
    notices: list[ReitPeriodicReportNotice],
    errors: dict[str, str],
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    route = route_asset_model(
        declared_asset_type="fund",
        reit_profile=profile,
    )
    retrieved_at = retrieved_at or datetime.now(UTC)
    return research_context(
        analysis_as_of=analysis_as_of,
        route=route,
        datasets={
            "reit_profile": reit_profile_dataset(profile),
            "exchange_price": reit_price_dataset(
                prices,
                analysis_as_of=analysis_as_of,
                error=errors.get("exchange_price"),
            ),
            "financials": reit_financial_dataset(
                financials,
                analysis_as_of=analysis_as_of,
                error=errors.get("financials"),
            ),
            "distributions": reit_distribution_dataset(
                distributions,
                analysis_as_of=analysis_as_of,
                error=errors.get("distributions"),
            ),
            "periodic_reports": reit_notice_dataset(
                notices,
                analysis_as_of=analysis_as_of,
                error=errors.get("periodic_reports"),
            ),
        },
        retrieved_at=retrieved_at,
    )


def research_context(
    *,
    analysis_as_of: date,
    route: DeterministicAssetRoute,
    datasets: dict[str, Any],
    retrieved_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "analysis_as_of": analysis_as_of.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "route": serialize_route(route),
        "datasets": datasets,
        "scoring_eligible": False,
    }


def serialize_route(route: DeterministicAssetRoute) -> dict[str, Any]:
    return {
        "asset_type": route.asset_type,
        "main_model": route.main_model,
        "style_overlays": list(route.style_overlays),
        "reason": route.reason,
        "evidence": list(route.evidence),
        "warnings": list(route.warnings),
        "scoring_eligible": False,
    }


def stock_statement_dataset(
    rows: list[StockBalanceSheet | StockIncomeStatement | StockCashFlowStatement],
    *,
    data_fields: tuple[str, ...],
    analysis_as_of: date,
    error: str | None,
) -> dict[str, Any]:
    considered = sorted(
        (row for row in rows if row.report_date <= analysis_as_of),
        key=lambda row: row.report_date,
    )
    eligible = [
        row
        for row in considered
        if row.notice_date is not None and row.notice_date <= analysis_as_of
    ]
    missing_notice_count = sum(row.notice_date is None for row in considered)
    future_notice_count = sum(
        row.notice_date is not None and row.notice_date > analysis_as_of
        for row in considered
    )
    items = [serialize_dataclass(row, exclude={"raw"}) for row in eligible]
    latest = eligible[-1] if eligible else None
    status = dataset_status(
        items,
        error=error,
        degraded=missing_notice_count > 0 or future_notice_count > 0,
    )
    return {
        "status": status,
        "source": "eastmoney_f10_detailed_financial_statement",
        "source_as_of": latest.report_date.isoformat() if latest else None,
        "available_at": (
            latest.notice_date.isoformat()
            if latest is not None and latest.notice_date is not None
            else None
        ),
        "unit": "CNY unless field suffix is _pct",
        "coverage": ratio(len(eligible), len(considered)),
        "field_coverage": latest_field_coverage(latest, data_fields),
        "report_count": len(items),
        "excluded": {
            "missing_notice_date": missing_notice_count,
            "notice_after_analysis_as_of": future_notice_count,
        },
        "items": items,
        "error": error,
        "scoring_eligible": False,
    }


def reit_profile_dataset(profile: ReitProfile) -> dict[str, Any]:
    return {
        "status": "available",
        "source": profile.source,
        "source_as_of": (
            profile.scale_report_date.isoformat() if profile.scale_report_date else None
        ),
        "available_at": None,
        "unit": {"period_end_net_assets_cny": "CNY"},
        "coverage": 1.0,
        "item": serialize_dataclass(profile, exclude={"raw", "scoring_eligible"}),
        "limitations": [
            "profile metadata does not expose a publication date and is not "
            "historical scoring evidence"
        ],
        "error": None,
        "scoring_eligible": False,
    }


def reit_price_dataset(
    rows: list[ReitPriceBar],
    *,
    analysis_as_of: date,
    error: str | None,
) -> dict[str, Any]:
    eligible = sorted(
        (row for row in rows if row.date <= analysis_as_of),
        key=lambda row: row.date,
    )
    latest = eligible[-1] if eligible else None
    return {
        "status": dataset_status(eligible, error=error),
        "source": "eastmoney_push2his",
        "source_as_of": latest.date.isoformat() if latest else None,
        "available_at": latest.date.isoformat() if latest else None,
        "unit": {"price": "CNY per unit", "volume": "exchange units", "amount": "CNY"},
        "coverage": 1.0 if eligible else 0.0,
        "sample_size": len(eligible),
        "range": {
            "start": eligible[0].date.isoformat() if eligible else None,
            "end": latest.date.isoformat() if latest else None,
        },
        "latest": (
            serialize_dataclass(latest, exclude={"scoring_eligible"}) if latest else None
        ),
        "current_period_complete": None,
        "error": error,
        "scoring_eligible": False,
    }


def reit_financial_dataset(
    rows: list[ReitFinancialSnapshot],
    *,
    analysis_as_of: date,
    error: str | None,
) -> dict[str, Any]:
    considered = sorted(
        (row for row in rows if row.report_date <= analysis_as_of),
        key=lambda row: row.report_date,
    )
    eligible = [
        row
        for row in considered
        if row.point_in_time_eligible
        and row.notice_date is not None
        and row.notice_date <= analysis_as_of
    ]
    return {
        "status": dataset_status(
            eligible,
            error=error,
            degraded=len(eligible) < len(considered),
        ),
        "source": "eastmoney_fund_financial",
        "source_as_of": eligible[-1].report_date.isoformat() if eligible else None,
        "available_at": eligible[-1].notice_date.isoformat() if eligible else None,
        "unit": "CNY; growth fields use percent",
        "coverage": ratio(len(eligible), len(considered)),
        "report_count": len(eligible),
        "items": [
            serialize_dataclass(row, exclude={"raw", "scoring_eligible"})
            for row in eligible
        ],
        "excluded_without_point_in_time_notice": len(considered) - len(eligible),
        "limitations": [
            "DISPROFIT is not treated as AFFO",
            "occupancy, rent growth, and underlying leverage are unavailable",
        ],
        "error": error,
        "scoring_eligible": False,
    }


def reit_distribution_dataset(
    rows: list[ReitDistribution],
    *,
    analysis_as_of: date,
    error: str | None,
) -> dict[str, Any]:
    considered = sorted(
        (
            row
            for row in rows
            if row.ex_dividend_date is None
            or row.ex_dividend_date <= analysis_as_of
        ),
        key=lambda row: (
            row.ex_dividend_date or date.min,
            row.available_date or date.min,
        ),
    )
    eligible = [
        row
        for row in considered
        if row.point_in_time_eligible
        and row.available_date is not None
        and row.available_date <= analysis_as_of
    ]
    return {
        "status": dataset_status(
            eligible,
            error=error,
            degraded=len(eligible) < len(considered),
        ),
        "source": "eastmoney_fund_distribution",
        "source_as_of": (
            eligible[-1].ex_dividend_date.isoformat()
            if eligible and eligible[-1].ex_dividend_date
            else None
        ),
        "available_at": (
            eligible[-1].available_date.isoformat()
            if eligible and eligible[-1].available_date
            else None
        ),
        "unit": {"cash_per_unit_cny": "CNY per fund unit"},
        "coverage": ratio(len(eligible), len(considered)),
        "distribution_count": len(eligible),
        "items": [
            serialize_dataclass(row, exclude={"raw_row", "scoring_eligible"})
            for row in eligible
        ],
        "excluded_without_matched_announcement": len(considered) - len(eligible),
        "error": error,
        "scoring_eligible": False,
    }


def reit_notice_dataset(
    rows: list[ReitPeriodicReportNotice],
    *,
    analysis_as_of: date,
    error: str | None,
) -> dict[str, Any]:
    canonical = sorted(
        (
            row
            for row in rows
            if row.is_canonical and row.publish_date <= analysis_as_of
        ),
        key=lambda row: (row.publish_date, row.announcement_id),
    )
    return {
        "status": dataset_status(canonical, error=error),
        "source": "eastmoney_fund_announcement",
        "source_as_of": canonical[-1].publish_date.isoformat() if canonical else None,
        "available_at": canonical[-1].publish_date.isoformat() if canonical else None,
        "unit": "date",
        "coverage": ratio(len(canonical), len(rows)),
        "notice_count": len(canonical),
        "items": [
            serialize_dataclass(row, exclude={"raw", "scoring_eligible"})
            for row in canonical
        ],
        "excluded_noncanonical_or_future": len(rows) - len(canonical),
        "error": error,
        "scoring_eligible": False,
    }


def serialize_dataclass(value: Any, *, exclude: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for definition in fields(value):
        if definition.name in exclude:
            continue
        item = getattr(value, definition.name)
        result[definition.name] = item.isoformat() if isinstance(item, date) else item
    return result


def latest_field_coverage(value: Any, field_names: tuple[str, ...]) -> float:
    if value is None:
        return 0.0
    present = sum(getattr(value, field_name, None) is not None for field_name in field_names)
    return ratio(present, len(field_names))


def dataset_status(
    items: list[Any],
    *,
    error: str | None,
    degraded: bool = False,
) -> str:
    if items and (error or degraded):
        return "partial"
    if items:
        return "available"
    if error:
        return "error"
    return "unavailable"


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
