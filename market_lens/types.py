from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

AssetType = Literal["stock", "fund"]
SearchAssetType = Literal["stock", "fund", "index"]
CommodityMainContractKey = Literal[
    "copper",
    "aluminum",
    "gold",
    "rebar",
    "hot_rolled_coil",
    "iron_ore",
    "coking_coal",
    "coke",
]
CommodityHistoryPeriod = Literal["daily", "weekly", "monthly"]
ReitHistoryPeriod = Literal["daily", "weekly", "monthly"]
ReitReportKind = Literal["annual", "semiannual", "q1", "q2", "q3", "q4"]
StockFinancialReportScope = Literal["annual", "all"]
StockFinancialCompanyType = Literal["general", "bank", "insurance", "securities"]


@dataclass(frozen=True)
class AssetSearchResult:
    asset_type: SearchAssetType
    code: str
    name: str
    market: str | None
    quote_id: str | None
    source_type: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class StockBar:
    date: date
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    amplitude_pct: float | None
    change_pct: float | None
    change_amount: float | None
    turnover_pct: float | None


@dataclass(frozen=True)
class CommodityMainContractSpec:
    key: CommodityMainContractKey
    product_code: str
    name_zh: str
    exchange: str
    quote_id: str
    source_code: str
    source_market: int
    currency: str
    price_unit: str
    contract_multiplier: float
    contract_multiplier_unit: str
    series_kind: str
    roll_method: str
    price_adjustment: str
    source: str


@dataclass(frozen=True)
class CommodityFuturesBar:
    key: CommodityMainContractKey
    quote_id: str
    period: CommodityHistoryPeriod
    date: date
    open: float
    close: float
    high: float
    low: float
    volume_lots: float
    amount_cny: float | None
    amplitude_pct: float | None
    change_pct: float | None
    change_amount: float | None
    is_complete: bool | None
    source: str


@dataclass(frozen=True)
class ReitProfile:
    fund_code: str
    fund_name: str
    full_name: str
    fund_type: str
    establishment_date: date | None
    term_text: str | None
    scale_report_date: date | None
    period_end_net_assets_cny: float | None
    exchange: str
    quote_id: str
    source: str
    raw: dict[str, Any]
    scoring_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReitPriceBar:
    fund_code: str
    fund_name: str
    exchange: str
    quote_id: str
    period: ReitHistoryPeriod
    date: date
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount_cny: float | None
    amplitude_pct: float | None
    change_pct: float | None
    change_amount: float | None
    turnover_pct: float | None
    is_complete: bool | None
    source: str
    scoring_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReitPeriodicReportNotice:
    fund_code: str
    title: str
    category: str
    publish_date: date
    attachment_type: str | None
    announcement_id: str
    attachment_url: str
    report_date: date | None
    report_kind: ReitReportKind | None
    is_canonical: bool
    source: str
    raw: dict[str, Any]
    scoring_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReitFinancialSnapshot:
    fund_code: str
    report_date: date
    report_kind: ReitReportKind | None
    notice_date: date | None
    realized_income_cny: float | None
    net_profit_cny: float | None
    unit_profit_cny: float | None
    net_asset_growth_pct: float | None
    fund_net_asset_growth_pct: float | None
    distributable_profit_cny: float | None
    distributable_profit_per_unit_cny: float | None
    period_end_net_assets_cny: float | None
    period_end_unit_nav_cny: float | None
    fund_share_nav_growth_pct: float | None
    point_in_time_eligible: bool
    source: str
    raw: dict[str, Any]
    scoring_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReitDistribution:
    fund_code: str
    year: int
    record_date: date | None
    ex_dividend_date: date | None
    cash_per_unit_cny: float
    payment_date: date | None
    announcement_date: date | None
    available_date: date | None
    point_in_time_eligible: bool
    source: str
    raw_row: tuple[str, ...]
    scoring_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class StockValuationPoint:
    date: date
    code: str
    name: str | None
    close: float | None
    market_cap: float | None
    pe_ttm: float | None
    pe_static: float | None
    pb: float | None
    ps_ttm: float | None
    pcf_ocf_ttm: float | None
    peg: float | None
    raw: dict[str, Any]
    board_code: str | None = None
    board_name: str | None = None
    original_board_code: str | None = None


@dataclass(frozen=True)
class StockIndustryValuationSnapshot:
    date: date
    board_code: str
    board_name: str | None
    original_board_code: str | None
    rows: tuple[StockValuationPoint, ...]
    source: str = "eastmoney_datacenter"


@dataclass(frozen=True)
class StockProfile:
    code: str
    name: str | None
    em_industry: str | None
    csrc_industry: str | None
    security_type: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class StockFinancialIndicator:
    date: date
    report_type: str | None
    notice_date: date | None
    source_updated_at: date | None
    org_type: str | None
    roe_weighted: float | None
    roe_deducted_weighted: float | None
    parent_netprofit_growth_pct: float | None
    revenue_growth_pct: float | None
    gross_margin_pct: float | None
    net_margin_pct: float | None
    roic_pct: float | None
    fcff_backward_cny: float | None
    fcff_forward_cny: float | None
    net_interest_margin_pct: float | None
    net_interest_spread_pct: float | None
    non_performing_loan_ratio_pct: float | None
    provision_coverage_ratio_pct: float | None
    capital_adequacy_ratio_pct: float | None
    tier1_capital_adequacy_ratio_pct: float | None
    core_tier1_capital_adequacy_ratio_pct: float | None
    solvency_adequacy_ratio_pct: float | None
    new_business_value_cny: float | None
    new_business_value_margin_pct: float | None
    surrender_rate_pct: float | None
    risk_coverage_ratio_pct: float | None
    liquidity_coverage_ratio_pct: float | None
    net_stable_funding_ratio_pct: float | None
    net_capital_to_net_assets_pct: float | None
    net_capital_cny: float | None
    net_assets_cny: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class StockFinancialStatementBase:
    code: str
    report_date: date
    report_type: str | None
    report_name: str | None
    notice_date: date | None
    source_updated_at: date | None
    currency: str | None
    org_type: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class StockBalanceSheet(StockFinancialStatementBase):
    total_assets_cny: float | None
    total_current_assets_cny: float | None
    monetary_funds_cny: float | None
    accounts_receivable_cny: float | None
    inventory_cny: float | None
    contract_asset_cny: float | None
    total_liabilities_cny: float | None
    total_current_liabilities_cny: float | None
    accounts_payable_cny: float | None
    contract_liability_cny: float | None
    short_term_borrowings_cny: float | None
    current_portion_noncurrent_liabilities_cny: float | None
    long_term_borrowings_cny: float | None
    bonds_payable_cny: float | None
    total_equity_cny: float | None


@dataclass(frozen=True)
class StockIncomeStatement(StockFinancialStatementBase):
    total_operating_revenue_cny: float | None
    operating_cost_cny: float | None
    sales_expense_cny: float | None
    management_expense_cny: float | None
    finance_expense_cny: float | None
    research_expense_cny: float | None
    development_expense_cny: float | None
    operating_profit_cny: float | None
    parent_net_profit_cny: float | None
    deducted_parent_net_profit_cny: float | None
    income_tax_cny: float | None
    total_operating_revenue_yoy_pct: float | None
    research_expense_yoy_pct: float | None
    parent_net_profit_yoy_pct: float | None


@dataclass(frozen=True)
class StockCashFlowStatement(StockFinancialStatementBase):
    sales_services_cash_cny: float | None
    cash_paid_to_staff_cny: float | None
    net_operating_cash_flow_cny: float | None
    capital_expenditure_cash_cny: float | None
    investment_cash_paid_cny: float | None
    net_investing_cash_flow_cny: float | None
    borrowings_received_cash_cny: float | None
    debt_repaid_cash_cny: float | None
    dividends_interest_paid_cash_cny: float | None
    net_financing_cash_flow_cny: float | None
    cash_equivalents_increase_cny: float | None
    ending_cash_cny: float | None


@dataclass(frozen=True)
class StockPeerComparison:
    code: str
    name: str
    rank: int | None
    pe_ttm: float | None
    pb_mrq: float | None
    peg: float | None
    roe_avg: float | None
    net_profit_growth_ttm: float | None
    revenue_growth_ttm: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class StockDividendPlan:
    notice_date: date | None
    plan: str | None
    progress: str | None
    ex_dividend_date: date | None
    cash_per_share: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class StockDividendSummary:
    year: str
    total_dividend: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class FundHolding:
    rank: int
    code: str
    name: str
    weight_pct: float | None
    shares_10k: float | None
    market_value_10k: float | None
    report_date: date | None


@dataclass(frozen=True)
class FundTrackingInfo:
    fund_code: str
    fund_name: str | None
    fund_type: str | None
    index_code: str | None
    index_name: str | None
    target_etf_code: str | None
    target_etf_name: str | None


@dataclass(frozen=True)
class FundProductInfo:
    fund_code: str
    fund_name: str | None
    fund_type: str | None
    establishment_date: date | None
    scale_report_date: date | None
    period_end_net_assets_cny: float | None
    management_fee_pct: float | None
    custody_fee_pct: float | None
    sales_service_fee_pct: float | None
    benchmark: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class FundHoldingsRoute:
    holdings: list[FundHolding]
    source: str
    scope: str
    as_of: date | None
    coverage: float
    tracking: FundTrackingInfo | None
    fallback_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class FundNavPoint:
    date: date
    unit_nav: float | None
    cumulative_nav: float | None
    daily_growth_pct: float | None
    subscribe_status: str | None
    redeem_status: str | None
