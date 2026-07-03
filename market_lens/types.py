from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

AssetType = Literal["stock", "fund"]


@dataclass(frozen=True)
class AssetSearchResult:
    asset_type: AssetType
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


@dataclass(frozen=True)
class FundNavPoint:
    date: date
    unit_nav: float | None
    cumulative_nav: float | None
    daily_growth_pct: float | None
    subscribe_status: str | None
    redeem_status: str | None
