from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

AssetType = Literal["stock", "fund"]
SearchAssetType = Literal["stock", "fund", "index"]


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
    roe_weighted: float | None
    roe_deducted_weighted: float | None
    parent_netprofit_growth_pct: float | None
    revenue_growth_pct: float | None
    gross_margin_pct: float | None
    net_margin_pct: float | None
    raw: dict[str, Any]


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
