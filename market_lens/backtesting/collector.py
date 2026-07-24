from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from market_lens.backtesting.models import BacktestDataError, snapshot_from_analysis
from market_lens.backtesting.universe import StockUniverseEntry, StockUniverseManifest
from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    stock_history_year_chunks,
)
from market_lens.types import (
    StockBar,
    StockFinancialIndicator,
    StockIndustryValuationSnapshot,
    StockProfile,
    StockValuationPoint,
)
from market_lens.valuation.analyzer import analyze_stock

RebalanceFrequency = Literal["monthly", "quarterly"]
COLLECTOR_VERSION = "stock-point-in-time-v2"


class StockBacktestCollector:
    def __init__(self, data_client: EastmoneyClient | None = None) -> None:
        self.data_client = data_client or EastmoneyClient()
        self._industry_cache: dict[
            tuple[str, date], StockIndustryValuationSnapshot | Exception
        ] = {}

    def collect(
        self,
        manifest: StockUniverseManifest,
        *,
        start: date,
        end: date,
        frequency: RebalanceFrequency = "monthly",
        horizons: tuple[int, ...] = (21, 63, 126, 252),
        benchmark_quote_id: str = "1.000300",
        strict: bool = True,
    ) -> dict[str, Any]:
        if end < start:
            raise BacktestDataError("collection end date cannot precede start date")
        if not horizons or any(item < 1 for item in horizons):
            raise BacktestDataError("collection horizons must be positive")
        scheduled_dates = generate_rebalance_dates(start, end, frequency)
        if not scheduled_dates:
            raise BacktestDataError("collection period contains no rebalance dates")
        manifest.validate_for_collection(scheduled_dates)
        retrieved_at = datetime.now(UTC)
        price_start = start - timedelta(days=14)
        price_end = end + timedelta(days=int(max(horizons) * 1.6) + 30)
        benchmark_source = "eastmoney_index_history"
        benchmark_error: str | None = None
        try:
            benchmark_bars = self.data_client.get_index_history(
                benchmark_quote_id,
                start=price_start,
                end=price_end,
            )
        except (EastmoneyError, ValueError) as exc:
            benchmark_error = str(exc)
            benchmark_bars = []
        if not benchmark_bars:
            benchmark_code = benchmark_quote_id.partition(".")[2]
            try:
                benchmark_bars = self.data_client.get_sina_index_history(
                    benchmark_code,
                    benchmark_quote_id,
                    start=price_start,
                    end=price_end,
                )
            except (AttributeError, EastmoneyError, ValueError) as exc:
                benchmark_error = "; ".join(
                    item for item in (benchmark_error, str(exc)) if item
                )
                benchmark_bars = []
            if benchmark_bars:
                benchmark_source = "sina_index_history"
        if not benchmark_bars:
            raise BacktestDataError(
                f"benchmark price history is unavailable for {benchmark_quote_id}: "
                f"{benchmark_error or 'empty response'}"
            )

        analyses: list[dict[str, Any]] = []
        prices: dict[str, list[dict[str, Any]]] = {}
        diagnostics: list[dict[str, Any]] = []
        for entry in manifest.entries:
            relevant_dates = [
                item for item in scheduled_dates if is_member_near_schedule(entry, item)
            ]
            if not relevant_dates:
                continue
            try:
                entry_analyses, entry_prices, entry_diagnostics = self._collect_entry(
                    manifest,
                    entry,
                    relevant_dates,
                    price_start,
                    price_end,
                    retrieved_at,
                )
                skipped = [
                    item for item in entry_diagnostics if item.get("status") != "available"
                ]
                if strict and skipped:
                    reasons = ",".join(
                        str(item.get("reason") or "unknown") for item in skipped
                    )
                    raise BacktestDataError(
                        f"strict collection rejected skipped snapshots: {reasons}"
                    )
            except (EastmoneyError, BacktestDataError, KeyError, TypeError, ValueError) as exc:
                if strict:
                    raise BacktestDataError(
                        f"failed to collect point-in-time data for {entry.code}: {exc}"
                    ) from exc
                diagnostics.append(
                    {"code": entry.code, "status": "error", "reason": str(exc)}
                )
                continue
            analyses.extend(entry_analyses)
            prices[f"stock:{entry.code}"] = entry_prices
            diagnostics.extend(entry_diagnostics)

        if strict and not analyses:
            raise BacktestDataError("stock backtest collection produced no snapshots")
        return {
            "schema_version": "backtest-dataset-1",
            "collector_version": COLLECTOR_VERSION,
            "collected_at": retrieved_at.isoformat(),
            "universe": {
                "name": manifest.name,
                "source": manifest.source,
                "point_in_time_verified": manifest.point_in_time_verified,
                "includes_delisted": manifest.includes_delisted,
                "historical_industry_verified": manifest.historical_industry_verified,
            },
            "collection": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "frequency": frequency,
                "scheduled_dates": [item.isoformat() for item in scheduled_dates],
                "horizons": list(horizons),
                "benchmark_quote_id": benchmark_quote_id,
                "benchmark_source": benchmark_source,
                "benchmark_price_sha256": price_history_sha256(benchmark_bars),
                "benchmark_request_range": {
                    "start": price_start.isoformat(),
                    "end": price_end.isoformat(),
                },
                "price_adjustment": "qfq",
                "strict": strict,
                "diagnostics": diagnostics,
            },
            "analyses": analyses,
            "prices": prices,
            "benchmark_prices": serialize_prices(benchmark_bars),
        }

    def _collect_entry(
        self,
        manifest: StockUniverseManifest,
        entry: StockUniverseEntry,
        scheduled_dates: list[date],
        price_start: date,
        price_end: date,
        retrieved_at: datetime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        price_source = "tencent_qfq_stock_history"
        price_request_ranges = stock_history_year_chunks(price_start, price_end)
        price_error: str | None = None
        try:
            bars = self.data_client.get_tencent_stock_history(
                entry.code,
                start=price_start,
                end=price_end,
            )
        except (AttributeError, EastmoneyError, ValueError) as exc:
            price_error = str(exc)
            bars = []
        if not bars:
            try:
                bars = self.data_client.get_stock_history(
                    entry.code,
                    start=price_start,
                    end=price_end,
                )
            except (AttributeError, EastmoneyError, ValueError) as exc:
                price_error = "; ".join(
                    item for item in (price_error, str(exc)) if item
                )
                bars = []
            if bars:
                price_source = "eastmoney_stock_history"
                price_request_ranges = [(price_start, price_end)]
        if not bars:
            raise BacktestDataError(
                f"stock price history is unavailable: {price_error or 'empty response'}"
            )
        valuations = self.data_client.get_stock_valuation(entry.code)
        if not valuations:
            raise BacktestDataError("stock valuation history is unavailable")
        financials: list[StockFinancialIndicator] = []
        financials_error: str | None = None
        try:
            financials = self.data_client.get_stock_financial_indicators(entry.code)
        except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
            financials_error = str(exc)

        analyses: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for scheduled_date in scheduled_dates:
            historical_bars = [item for item in bars if item.date <= scheduled_date]
            if not historical_bars:
                diagnostics.append(
                    snapshot_diagnostic(entry.code, scheduled_date, "skipped", "price_unavailable")
                )
                continue
            analysis_as_of = historical_bars[-1].date
            if (scheduled_date - analysis_as_of).days > 7:
                diagnostics.append(
                    snapshot_diagnostic(
                        entry.code,
                        scheduled_date,
                        "skipped",
                        "latest_price_more_than_7_days_old",
                    )
                )
                continue
            membership = entry.membership_at(analysis_as_of)
            industry = entry.industry_at(analysis_as_of)
            if membership is None or industry is None:
                diagnostics.append(
                    snapshot_diagnostic(
                        entry.code,
                        scheduled_date,
                        "skipped",
                        "membership_or_industry_unavailable_at_trade_date",
                    )
                )
                continue
            historical_valuations = [
                item for item in valuations if item.date <= analysis_as_of
            ]
            if not historical_valuations:
                diagnostics.append(
                    snapshot_diagnostic(
                        entry.code, scheduled_date, "skipped", "valuation_unavailable"
                    )
                )
                continue
            latest_valuation = historical_valuations[-1]
            if manifest.schema_version == "stock-universe-2":
                mismatch_reason = verified_industry_mismatch(
                    latest_valuation, industry, analysis_as_of
                )
                if mismatch_reason is not None:
                    diagnostics.append(
                        snapshot_diagnostic(
                            entry.code, scheduled_date, "skipped", mismatch_reason
                        )
                    )
                    continue
            known_financials = financials_available_as_of(financials, analysis_as_of)
            profile = StockProfile(
                code=entry.code,
                name=entry.name,
                em_industry=industry.em_industry,
                csrc_industry=industry.csrc_industry,
                security_type="A-share",
                raw={
                    "source": industry.source,
                    "effective_from": industry.start.isoformat(),
                    "effective_to": industry.end.isoformat() if industry.end else None,
                },
            )
            industry_snapshot, industry_error = self._industry_snapshot(latest_valuation)
            analysis = analyze_stock(
                entry.code,
                historical_bars,
                historical_valuations,
                name=entry.name,
                profile=profile,
                financials=known_financials,
                peers={},
                dividends={},
                industry_valuation=industry_snapshot,
                industry_valuation_error=industry_error,
                financials_error=financials_error,
                retrieved_at=retrieved_at,
            )
            analysis["backtest_provenance"] = {
                "point_in_time_verified": True,
                "method": COLLECTOR_VERSION,
                "universe_source": manifest.source,
                "membership_effective_from": membership.start.isoformat(),
                "industry_effective_from": industry.start.isoformat(),
                "membership_payload_sha256": membership.payload_sha256,
                "industry_payload_sha256": industry.payload_sha256,
                "financial_rule": "notice_date_required_and_not_after_analysis_as_of",
                "scheduled_date": scheduled_date.isoformat(),
            }
            data_quality = analysis["assessment"]["data_quality"]
            data_quality["sources"].extend(
                [
                    {
                        "key": "historical_universe_membership",
                        "source": manifest.source,
                        "status": "available",
                        "source_as_of": membership.start.isoformat(),
                        "payload_sha256": membership.payload_sha256,
                    },
                    {
                        "key": "historical_industry_classification",
                        "source": industry.source,
                        "status": "available",
                        "source_as_of": industry.start.isoformat(),
                        "payload_sha256": industry.payload_sha256,
                    },
                ]
            )
            snapshot_from_analysis(analysis)
            analyses.append(analysis)
            diagnostics.append(
                {
                    **snapshot_diagnostic(entry.code, scheduled_date, "available", None),
                    "analysis_as_of": analysis_as_of.isoformat(),
                    "price_source": price_source,
                    "price_sha256": price_history_sha256(bars),
                    "price_request_ranges": [
                        {
                            "start": range_start.isoformat(),
                            "end": range_end.isoformat(),
                        }
                        for range_start, range_end in price_request_ranges
                    ],
                    "financial_rows": len(known_financials),
                    "valuation_rows": len(historical_valuations),
                }
            )
        if not analyses:
            raise BacktestDataError("no valid snapshots were produced for stock")
        return analyses, serialize_prices(bars), diagnostics

    def _industry_snapshot(
        self,
        latest: StockValuationPoint,
    ) -> tuple[StockIndustryValuationSnapshot | None, str | None]:
        if not latest.board_code:
            return None, "industry_board_code_unavailable"
        key = (latest.board_code, latest.date)
        cached = self._industry_cache.get(key)
        if cached is None:
            try:
                cached = self.data_client.get_stock_industry_valuation_snapshot(
                    latest.board_code,
                    latest.date,
                    board_name=latest.board_name,
                )
            except (EastmoneyError, ValueError) as exc:
                cached = exc
            self._industry_cache[key] = cached
        if isinstance(cached, Exception):
            return None, str(cached)
        return cached, None


def financials_available_as_of(
    financials: list[StockFinancialIndicator],
    as_of: date,
) -> list[StockFinancialIndicator]:
    return [
        item
        for item in financials
        if item.date <= as_of
        and item.notice_date is not None
        and item.notice_date <= as_of
    ]


def generate_rebalance_dates(
    start: date,
    end: date,
    frequency: RebalanceFrequency,
) -> list[date]:
    if frequency not in {"monthly", "quarterly"}:
        raise ValueError("frequency must be monthly or quarterly")
    result: list[date] = []
    year = start.year
    month = start.month
    while date(year, month, 1) <= end:
        is_rebalance_month = frequency == "monthly" or month in {3, 6, 9, 12}
        if is_rebalance_month:
            candidate = date(year, month, monthrange(year, month)[1])
            if start <= candidate <= end:
                result.append(candidate)
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return result


def serialize_prices(bars: list[StockBar]) -> list[dict[str, Any]]:
    return [{"date": item.date.isoformat(), "close": item.close} for item in bars]


def price_history_sha256(bars: list[StockBar]) -> str:
    encoded = json.dumps(
        serialize_prices(bars),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_diagnostic(
    code: str,
    scheduled_date: date,
    status: str,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "code": code,
        "scheduled_date": scheduled_date.isoformat(),
        "status": status,
        "reason": reason,
    }


def is_member_near_schedule(entry: StockUniverseEntry, scheduled_date: date) -> bool:
    return any(
        entry.is_member(scheduled_date - timedelta(days=offset)) for offset in range(8)
    )


def verified_industry_mismatch(
    valuation: StockValuationPoint,
    industry: Any,
    analysis_as_of: date,
) -> str | None:
    if valuation.date != analysis_as_of:
        return "valuation_not_available_on_analysis_trade_date"
    if industry.source_as_of != analysis_as_of:
        return "industry_evidence_date_mismatch"
    if industry.board_code != valuation.board_code:
        return "industry_board_code_mismatch"
    if industry.em_industry != valuation.board_name:
        return "industry_board_name_mismatch"
    return None
