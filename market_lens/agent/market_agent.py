from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from typing import Any

from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    is_a_share_symbol,
    stock_bars_from_valuations,
)
from market_lens.types import (
    FundHolding,
    FundHoldingsRoute,
    StockIndustryValuationSnapshot,
    StockValuationPoint,
)
from market_lens.valuation.analyzer import analyze_fund, analyze_stock
from market_lens.valuation.assessment import build_fund_assessment
from market_lens.valuation.framework import analyze_index_price_proxy


class MarketAnalysisAgent:
    """Business agent that calls fixed tools and returns structured analysis."""

    def __init__(self, data_client: EastmoneyClient | None = None) -> None:
        self.data_client = data_client or EastmoneyClient()

    def analyze(
        self,
        asset_type: str,
        code: str,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        retrieved_at = datetime.now(UTC)
        if asset_type == "stock":
            all_valuations = self.data_client.get_stock_valuation(code)
            stock_name = next((item.name for item in reversed(all_valuations) if item.name), None)
            valuations = [item for item in all_valuations if start <= item.date <= end]
            try:
                bars = self.data_client.get_stock_history(code, start=start, end=end)
            except EastmoneyError:
                bars = stock_bars_from_valuations(valuations)
            if not bars:
                raise ValueError(
                    f"No stock price data found for {code}. "
                    "If this is a fund code, choose asset_type='fund'."
                )
            profile = None
            financials = []
            financials_error = None
            peers = {}
            dividends = {}
            try:
                profile = self.data_client.get_stock_profile(code)
            except EastmoneyError:
                pass
            try:
                financials = [
                    item
                    for item in self.data_client.get_stock_financial_indicators(code)
                    if item.date <= end
                ]
            except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
                financials_error = str(exc)
            try:
                peers = self.data_client.get_stock_peer_comparison(code)
            except EastmoneyError:
                pass
            try:
                dividends = self.data_client.get_stock_dividends(code)
            except EastmoneyError:
                pass
            industry_valuation, industry_valuation_error = self._load_industry_valuation(
                valuations
            )
            return analyze_stock(
                code,
                bars,
                valuations,
                name=stock_name,
                profile=profile,
                financials=financials,
                peers=peers,
                dividends=dividends,
                industry_valuation=industry_valuation,
                industry_valuation_error=industry_valuation_error,
                financials_error=financials_error,
                retrieved_at=retrieved_at,
            )
        if asset_type == "fund":
            try:
                nav_points = self.data_client.get_exchange_fund_price_nav(
                    code,
                    start=start,
                    end=end,
                )
            except EastmoneyError:
                nav_points = []
            fund_data_source = "exchange_price_history" if nav_points else "fund_nav_history"
            if not nav_points:
                nav_points = self.data_client.get_fund_nav(code, start=start, end=end)
            if not nav_points:
                raise ValueError(f"No fund NAV data found for {code}.")
            fund_name = self.data_client.get_fund_name(code)
            product_info = None
            product_info_error = None
            try:
                product_info = self.data_client.get_fund_product_info(code)
            except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
                product_info_error = str(exc)
            holdings_route = None
            try:
                holdings_route = self.data_client.get_fund_holdings_route(
                    code,
                    fund_name=fund_name,
                )
                holdings = holdings_route.holdings
            except EastmoneyError:
                holdings = []
            holding_analyses = self._analyze_fund_holdings(holdings, end=end)
            result = analyze_fund(
                code,
                nav_points,
                name=fund_name,
                holdings=holdings,
                holding_analyses=holding_analyses,
                product_info=product_info,
                product_info_error=product_info_error,
                retrieved_at=retrieved_at,
            )
            apply_holdings_route_method(result["valuation"], holdings_route)
            route_metadata = serialize_holdings_route(holdings_route)
            result["holdings_route"] = route_metadata
            result["valuation"]["holdings_route"] = route_metadata
            result["notes"].insert(0, holdings_route_note(holdings_route))

            index_candidate = None
            if result["valuation"].get("score") is None:
                if (
                    holdings_route
                    and holdings_route.tracking
                    and holdings_route.tracking.index_code
                ):
                    try:
                        candidates = self.data_client.search_assets(
                            holdings_route.tracking.index_code,
                            limit=5,
                            include_indexes=True,
                        )
                    except EastmoneyError:
                        candidates = []
                    index_candidate = next(
                        (
                            item
                            for item in candidates
                            if item.asset_type == "index"
                            and item.code == holdings_route.tracking.index_code
                        ),
                        None,
                    )
                if index_candidate is None:
                    index_candidate = self.data_client.find_index_for_fund(fund_name or code)
            if index_candidate and index_candidate.quote_id:
                try:
                    index_bars = self.data_client.get_index_history(
                        index_candidate.quote_id,
                        start=start,
                        end=end,
                    )
                except EastmoneyError:
                    index_bars = []
                if index_bars:
                    product_data = result["valuation"].get("product_data")
                    result["valuation"] = analyze_index_price_proxy(
                        index_bars=index_bars,
                        index_code=index_candidate.code,
                        index_name=index_candidate.name,
                        index_quote_id=index_candidate.quote_id,
                    )
                    result["valuation"]["holdings_route"] = route_metadata
                    result["valuation"]["product_data"] = product_data
            result["data_source"] = fund_data_source
            if fund_data_source == "exchange_price_history":
                result["notes"].insert(
                    0,
                    "Exchange-traded fund performance uses adjusted market price history.",
                )
            if result["valuation"].get("method") == "index_price_percentile_proxy":
                result["notes"].insert(
                    0,
                    "ETF valuation currently uses tracked-index price percentile as a proxy.",
                )
            result["assessment"] = build_fund_assessment(
                result,
                retrieved_at=retrieved_at,
            )
            return result
        raise ValueError("asset_type must be 'stock' or 'fund'")

    def _analyze_fund_holdings(
        self,
        holdings: list[FundHolding],
        end: date,
    ) -> dict[str, dict[str, Any]]:
        supported = [item for item in holdings if is_supported_holding_stock(item.code)]
        if not supported:
            return {}

        analyses: dict[str, dict[str, Any]] = {}
        worker_count = min(4, len(supported))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._analyze_holding_stock, holding, end): holding.code
                for holding in supported
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    analysis = future.result()
                except (EastmoneyError, ValueError):
                    continue
                if analysis is not None:
                    analyses[code] = analysis
        return analyses

    def _analyze_holding_stock(
        self,
        holding: FundHolding,
        end: date,
    ) -> dict[str, Any] | None:
        valuations = [
            item for item in self.data_client.get_stock_valuation(holding.code) if item.date <= end
        ]
        bars = stock_bars_from_valuations(valuations)
        if not bars:
            return None

        profile = None
        financials = []
        peers = {}
        dividends = {}
        try:
            profile = self.data_client.get_stock_profile(holding.code)
        except EastmoneyError:
            pass
        try:
            financials = [
                item
                for item in self.data_client.get_stock_financial_indicators(holding.code)
                if item.date <= end
            ]
        except EastmoneyError:
            pass
        try:
            peers = self.data_client.get_stock_peer_comparison(holding.code)
        except EastmoneyError:
            pass
        try:
            dividends = self.data_client.get_stock_dividends(holding.code)
        except EastmoneyError:
            pass
        return analyze_stock(
            holding.code,
            bars,
            valuations,
            name=holding.name,
            profile=profile,
            financials=financials,
            peers=peers,
            dividends=dividends,
        )

    def _load_industry_valuation(
        self,
        valuations: list[StockValuationPoint],
    ) -> tuple[StockIndustryValuationSnapshot | None, str | None]:
        latest = valuations[-1] if valuations else None
        if latest is None:
            return None, "valuation_history_unavailable"
        if not latest.board_code:
            return None, "industry_board_code_unavailable"
        try:
            snapshot = self.data_client.get_stock_industry_valuation_snapshot(
                latest.board_code,
                latest.date,
                board_name=latest.board_name,
            )
        except (EastmoneyError, ValueError) as exc:
            return None, str(exc)
        return snapshot, None


def is_supported_holding_stock(code: str) -> bool:
    try:
        return is_a_share_symbol(code)
    except ValueError:
        return False


def serialize_holdings_route(route: FundHoldingsRoute | None) -> dict[str, Any]:
    if route is None:
        return {
            "source": "unavailable",
            "scope": "unavailable",
            "as_of": None,
            "coverage": 0.0,
            "fallback_reasons": [],
            "fund_type": None,
            "tracked_index_code": None,
            "tracked_index_name": None,
            "target_etf_code": None,
            "target_etf_name": None,
        }
    tracking = route.tracking
    return {
        "source": route.source,
        "scope": route.scope,
        "as_of": route.as_of.isoformat() if route.as_of else None,
        "coverage": route.coverage,
        "fallback_reasons": list(route.fallback_reasons),
        "fund_type": tracking.fund_type if tracking else None,
        "tracked_index_code": tracking.index_code if tracking else None,
        "tracked_index_name": tracking.index_name if tracking else None,
        "target_etf_code": tracking.target_etf_code if tracking else None,
        "target_etf_name": tracking.target_etf_name if tracking else None,
    }


def apply_holdings_route_method(
    valuation: dict[str, Any],
    route: FundHoldingsRoute | None,
) -> None:
    if route is None:
        return
    if route.scope == "tracked_index_top10":
        valuation.update(
            {
                "method": "index_constituents_weighted_multi_factor",
                "profile": "index_fund",
                "profile_name": "指数基金",
            }
        )
    elif route.scope == "target_etf_top10":
        valuation.update(
            {
                "method": "target_etf_holdings_weighted_multi_factor",
                "profile": "index_fund",
                "profile_name": "指数基金",
            }
        )


def holdings_route_note(route: FundHoldingsRoute | None) -> str:
    if route is None:
        return "Fund holdings routing was unavailable; valuation confidence is limited."
    if route.scope == "tracked_index_top10":
        return (
            "Valuation uses official top constituents of the tracked index, "
            f"dated {route.as_of.isoformat() if route.as_of else 'unknown'}."
        )
    if route.scope == "target_etf_top10":
        return (
            "Official index holdings were unavailable; valuation falls back to the latest "
            "disclosed top holdings of the target ETF."
        )
    if route.scope == "unresolved_index_fund":
        return (
            "The tracked index relationship could not be resolved, so direct fund holdings "
            "were not used as a substitute."
        )
    return "Valuation uses the fund's latest disclosed direct stock holdings."
