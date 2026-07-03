from __future__ import annotations

from datetime import date
from typing import Any

from market_lens.data.eastmoney import EastmoneyClient
from market_lens.valuation.analyzer import analyze_fund, analyze_stock


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
            nav_points = self.data_client.get_fund_nav(code, start=start, end=end)
            if not nav_points:
                raise ValueError(f"No fund NAV data found for {code}.")
            fund_name = self.data_client.get_fund_name(code)
            return analyze_fund(code, nav_points, name=fund_name)
        raise ValueError("asset_type must be 'stock' or 'fund'")
