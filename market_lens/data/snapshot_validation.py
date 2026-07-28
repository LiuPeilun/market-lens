from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

from market_lens.types import FundNavPoint, StockBar, StockValuationPoint

STOCK_HISTORY_SNAPSHOT_VERSION = "stock_history_v1"
STOCK_VALUATION_SNAPSHOT_VERSION = "stock_valuation_v1"
FUND_NAV_SNAPSHOT_VERSION = "fund_nav_v1"
STOCK_HISTORY_DATASET = "stock_history"
STOCK_VALUATION_DATASET = "stock_valuation_history"
FUND_NAV_DATASET = "fund_nav_history"
EXCHANGE_FUND_PRICE_DATASET = "exchange_fund_price_history"
EASTMONEY_PUSH2HIS_SOURCE = "eastmoney_push2his"
EASTMONEY_DATACENTER_SOURCE = "eastmoney_datacenter"
EASTMONEY_PINGZHONGDATA_SOURCE = "eastmoney_pingzhongdata"
EASTMONEY_F10_NAV_SOURCE = "eastmoney_f10_nav"


def serialize_stock_bar(row: StockBar) -> dict[str, Any]:
    return {
        "date": row.date.isoformat(),
        "open": row.open,
        "close": row.close,
        "high": row.high,
        "low": row.low,
        "volume": row.volume,
        "amount": row.amount,
        "amplitude_pct": row.amplitude_pct,
        "change_pct": row.change_pct,
        "change_amount": row.change_amount,
        "turnover_pct": row.turnover_pct,
    }


def decode_stock_bars(payload: Any) -> list[StockBar]:
    if not isinstance(payload, list):
        raise TypeError("Stock history snapshot payload must be a list")
    return [
        StockBar(
            date=_snapshot_date(row, "date"),
            open=_snapshot_number(row, "open"),
            close=_snapshot_number(row, "close"),
            high=_snapshot_number(row, "high"),
            low=_snapshot_number(row, "low"),
            volume=_snapshot_number(row, "volume"),
            amount=_snapshot_number(row, "amount"),
            amplitude_pct=_snapshot_optional_number(row, "amplitude_pct"),
            change_pct=_snapshot_optional_number(row, "change_pct"),
            change_amount=_snapshot_optional_number(row, "change_amount"),
            turnover_pct=_snapshot_optional_number(row, "turnover_pct"),
        )
        for row in _snapshot_rows(payload)
    ]


def validate_stock_bar_snapshot(
    rows: list[StockBar],
    *,
    start: date,
    end: date,
    period: str = "daily",
    today: date | None = None,
) -> bool:
    tail_tolerance_days = {
        "daily": 10,
        "weekly": 21,
        "monthly": 62,
    }.get(period)
    if not rows or start > end or tail_tolerance_days is None:
        return False
    latest_allowed = min(end, today or date.today())
    if not _has_strictly_increasing_dates(rows):
        return False
    if (latest_allowed - rows[-1].date).days > tail_tolerance_days:
        return False
    for row in rows:
        if not start <= row.date <= latest_allowed:
            return False
        prices = (row.open, row.close, row.high, row.low)
        if not all(isfinite(value) and value > 0 for value in prices):
            return False
        if row.low > min(row.open, row.close) or row.high < max(row.open, row.close):
            return False
        if row.low > row.high:
            return False
        if not isfinite(row.volume) or row.volume < 0:
            return False
        if not isfinite(row.amount) or row.amount < 0:
            return False
        if not _valid_optional_finite(row.amplitude_pct, minimum=0):
            return False
        if not _valid_optional_finite(row.change_pct):
            return False
        if not _valid_optional_finite(row.change_amount):
            return False
        if not _valid_optional_finite(row.turnover_pct, minimum=0):
            return False
    return True


def serialize_stock_valuation_point(row: StockValuationPoint) -> dict[str, Any]:
    return {
        "date": row.date.isoformat(),
        "code": row.code,
        "name": row.name,
        "close": row.close,
        "market_cap": row.market_cap,
        "pe_ttm": row.pe_ttm,
        "pe_static": row.pe_static,
        "pb": row.pb,
        "ps_ttm": row.ps_ttm,
        "pcf_ocf_ttm": row.pcf_ocf_ttm,
        "peg": row.peg,
        "board_code": row.board_code,
        "board_name": row.board_name,
        "original_board_code": row.original_board_code,
    }


def decode_stock_valuation_points(payload: Any) -> list[StockValuationPoint]:
    if not isinstance(payload, list):
        raise TypeError("Stock valuation snapshot payload must be a list")
    return [
        StockValuationPoint(
            date=_snapshot_date(row, "date"),
            code=_snapshot_text(row, "code", required=True),
            name=_snapshot_text(row, "name"),
            close=_snapshot_optional_number(row, "close"),
            market_cap=_snapshot_optional_number(row, "market_cap"),
            pe_ttm=_snapshot_optional_number(row, "pe_ttm"),
            pe_static=_snapshot_optional_number(row, "pe_static"),
            pb=_snapshot_optional_number(row, "pb"),
            ps_ttm=_snapshot_optional_number(row, "ps_ttm"),
            pcf_ocf_ttm=_snapshot_optional_number(row, "pcf_ocf_ttm"),
            peg=_snapshot_optional_number(row, "peg"),
            raw={},
            board_code=_snapshot_text(row, "board_code"),
            board_name=_snapshot_text(row, "board_name"),
            original_board_code=_snapshot_text(row, "original_board_code"),
        )
        for row in _snapshot_rows(payload)
    ]


def validate_stock_valuation_snapshot(
    rows: list[StockValuationPoint],
    *,
    expected_code: str,
    today: date | None = None,
) -> bool:
    if not rows or not _has_strictly_increasing_dates(rows):
        return False
    latest_allowed = today or date.today()
    valuation_fields = (
        "pe_ttm",
        "pe_static",
        "pb",
        "ps_ttm",
        "pcf_ocf_ttm",
        "peg",
    )
    for row in rows:
        if row.code != expected_code or row.date > latest_allowed:
            return False
        if not _valid_optional_finite(row.close, minimum=0, strict_minimum=True):
            return False
        if not _valid_optional_finite(row.market_cap, minimum=0, strict_minimum=True):
            return False
        values = [getattr(row, field) for field in valuation_fields]
        if not all(_valid_optional_finite(value) for value in values):
            return False
        if not any(value is not None for value in values):
            return False
    return True


def serialize_fund_nav_point(row: FundNavPoint) -> dict[str, Any]:
    return {
        "date": row.date.isoformat(),
        "unit_nav": row.unit_nav,
        "cumulative_nav": row.cumulative_nav,
        "daily_growth_pct": row.daily_growth_pct,
        "subscribe_status": row.subscribe_status,
        "redeem_status": row.redeem_status,
    }


def decode_fund_nav_points(payload: Any) -> list[FundNavPoint]:
    if not isinstance(payload, list):
        raise TypeError("Fund NAV snapshot payload must be a list")
    return [
        FundNavPoint(
            date=_snapshot_date(row, "date"),
            unit_nav=_snapshot_optional_number(row, "unit_nav"),
            cumulative_nav=_snapshot_optional_number(row, "cumulative_nav"),
            daily_growth_pct=_snapshot_optional_number(row, "daily_growth_pct"),
            subscribe_status=_snapshot_text(row, "subscribe_status"),
            redeem_status=_snapshot_text(row, "redeem_status"),
        )
        for row in _snapshot_rows(payload)
    ]


def validate_fund_nav_snapshot(
    rows: list[FundNavPoint],
    *,
    start: date,
    end: date,
    today: date | None = None,
) -> bool:
    if not rows or start > end:
        return False
    latest_allowed = min(end, today or date.today())
    if not _has_strictly_increasing_dates(rows):
        return False
    if (latest_allowed - rows[-1].date).days > 10:
        return False
    for row in rows:
        if not start <= row.date <= latest_allowed:
            return False
        if not _valid_optional_finite(row.unit_nav, minimum=0, strict_minimum=True):
            return False
        if row.unit_nav is None:
            return False
        if not _valid_optional_finite(
            row.cumulative_nav,
            minimum=0,
            strict_minimum=True,
        ):
            return False
        if not _valid_optional_finite(row.daily_growth_pct):
            return False
    return True


def _snapshot_rows(payload: list[Any]) -> list[dict[str, Any]]:
    if not all(isinstance(row, dict) for row in payload):
        raise TypeError("Snapshot rows must be objects")
    return payload


def _snapshot_date(row: dict[str, Any], key: str) -> date:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"Snapshot field {key} must be a date string")
    return date.fromisoformat(value)


def _snapshot_number(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Snapshot field {key} must be numeric")
    return float(value)


def _snapshot_optional_number(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Snapshot field {key} must be numeric or null")
    return float(value)


def _snapshot_text(
    row: dict[str, Any],
    key: str,
    *,
    required: bool = False,
) -> str | None:
    value = row.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value):
        raise TypeError(f"Snapshot field {key} must be text")
    return value


def _has_strictly_increasing_dates(rows: list[Any]) -> bool:
    dates = [row.date for row in rows]
    return all(
        previous < current
        for previous, current in zip(dates, dates[1:], strict=False)
    )


def _valid_optional_finite(
    value: float | None,
    *,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> bool:
    if value is None:
        return True
    if not isfinite(value):
        return False
    if minimum is None:
        return True
    return value > minimum if strict_minimum else value >= minimum
