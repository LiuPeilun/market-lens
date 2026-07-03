from __future__ import annotations

from datetime import date
from typing import Any

from market_lens.data.eastmoney import EastmoneyClient
from market_lens.valuation.analyzer import analyze_fund, analyze_stock
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
        if asset_type == "stock":
            bars = self.data_client.get_stock_history(code, start=start, end=end)
            if not bars:
                raise ValueError(
                    f"No stock price data found for {code}. "
                    "If this is a fund code, choose asset_type='fund'."
                )
            all_valuations = self.data_client.get_stock_valuation(code)
            stock_name = next((item.name for item in reversed(all_valuations) if item.name), None)
            valuations = [item for item in all_valuations if start <= item.date <= end]
            return analyze_stock(code, bars, valuations, name=stock_name)
        if asset_type == "fund":
            nav_points = self.data_client.get_exchange_fund_price_nav(code, start=start, end=end)
            fund_data_source = "exchange_price_history" if nav_points else "fund_nav_history"
            if not nav_points:
                nav_points = self.data_client.get_fund_nav(code, start=start, end=end)
            if not nav_points:
                raise ValueError(f"No fund NAV data found for {code}.")
            fund_name = self.data_client.get_fund_name(code)
            result = analyze_fund(code, nav_points, name=fund_name)
            index_candidate = self.data_client.find_index_for_fund(fund_name or code)
            if index_candidate and index_candidate.quote_id:
                index_bars = self.data_client.get_index_history(
                    index_candidate.quote_id,
                    start=start,
                    end=end,
                )
                if index_bars:
                    result["valuation"] = analyze_index_price_proxy(
                        index_bars=index_bars,
                        index_code=index_candidate.code,
                        index_name=index_candidate.name,
                        index_quote_id=index_candidate.quote_id,
                    )
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
            return result
        raise ValueError("asset_type must be 'stock' or 'fund'")
