from __future__ import annotations

from datetime import date, timedelta
from threading import Event

import pytest

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.agent.stage_executor import StageExecutor
from market_lens.api.schemas import AnalysisResult
from market_lens.data.eastmoney import EastmoneyError
from market_lens.types import (
    AssetSearchResult,
    FundHolding,
    FundHoldingsRoute,
    FundNavPoint,
    FundProductInfo,
    FundTrackingInfo,
    StockBar,
    StockValuationPoint,
)
from market_lens.valuation.fallback_matrix import (
    FALLBACK_MATRICES,
    attach_fallback_traces,
    new_fallback_trace,
    stable_reason_code,
)

START = date(2026, 1, 1)
END = date(2026, 7, 24)


def stock_bar(point_date: date, close: float = 10.0) -> StockBar:
    return StockBar(
        date=point_date,
        open=close,
        close=close,
        high=close,
        low=close,
        volume=100.0,
        amount=1000.0,
        amplitude_pct=None,
        change_pct=None,
        change_amount=None,
        turnover_pct=None,
    )


def fund_nav(point_date: date, value: float) -> FundNavPoint:
    return FundNavPoint(
        date=point_date,
        unit_nav=value,
        cumulative_nav=value,
        daily_growth_pct=None,
        subscribe_status=None,
        redeem_status=None,
    )


def stock_valuation_rows() -> list[StockValuationPoint]:
    first_date = END - timedelta(days=299)
    return [
        StockValuationPoint(
            date=first_date + timedelta(days=index),
            code="600000",
            name="Test stock",
            close=10.0 + index,
            market_cap=None,
            pe_ttm=10.0 + index,
            pe_static=None,
            pb=1.0 + index / 10,
            ps_ttm=2.0 + index / 10,
            pcf_ocf_ttm=5.0 + index,
            peg=None,
            raw={},
        )
        for index in range(300)
    ]


class StockClientBase:
    def consume_lkg_events(self) -> list[dict[str, object]]:
        return []

    def get_stock_profile(self, code: str) -> object:
        raise EastmoneyError("profile offline")

    def get_stock_financial_indicators(self, code: str) -> list[object]:
        return []

    def get_stock_peer_comparison(self, code: str) -> dict[str, list[object]]:
        return {}

    def get_stock_dividends(self, code: str) -> dict[str, list[object]]:
        return {}

    def get_stock_balance_sheets(
        self,
        code: str,
        *,
        max_reports: int,
    ) -> list[object]:
        return []

    def get_stock_income_statements(
        self,
        code: str,
        *,
        max_reports: int,
    ) -> list[object]:
        return []

    def get_stock_cash_flow_statements(
        self,
        code: str,
        *,
        max_reports: int,
    ) -> list[object]:
        return []


class FundClientBase:
    def consume_lkg_events(self) -> list[dict[str, object]]:
        return []

    def get_fund_product_info(self, code: str) -> object:
        raise EastmoneyError("product source offline")


def active_holdings_route() -> FundHoldingsRoute:
    return FundHoldingsRoute(
        holdings=[
            FundHolding(
                rank=1,
                code="600000",
                name="Test stock",
                weight_pct=80.0,
                shares_10k=None,
                market_value_10k=None,
                report_date=date(2026, 6, 30),
            )
        ],
        source="eastmoney_fund_holdings",
        scope="fund_direct_top10",
        as_of=date(2026, 6, 30),
        coverage=0.8,
        tracking=None,
    )


def tracked_index_fixture() -> tuple[FundHoldingsRoute, AssetSearchResult]:
    tracking = FundTrackingInfo(
        fund_code="510300",
        fund_name="CSI 300 ETF",
        fund_type="index",
        index_code="000300",
        index_name="CSI 300",
        target_etf_code=None,
        target_etf_name=None,
    )
    route = FundHoldingsRoute(
        holdings=[],
        source="unavailable",
        scope="unresolved_index_fund",
        as_of=None,
        coverage=0.0,
        tracking=tracking,
    )
    candidate = AssetSearchResult(
        asset_type="index",
        code="000300",
        name="CSI 300",
        market="Shanghai",
        quote_id="1.000300",
        source_type="index",
        raw={},
    )
    return route, candidate


def holding_analyses() -> dict[str, dict[str, object]]:
    return {
        "600000": {
            "valuation": {
                "pe_ttm": 10.0,
                "pb": 1.2,
                "pe_ttm_percentile": 0.2,
                "pb_percentile": 0.3,
                "peer_comparison": {
                    "valuation": {"percentiles": {"pe_ttm": 0.25}}
                },
                "fundamentals": {
                    "roe_weighted": 12.0,
                    "parent_netprofit_growth_pct": 8.0,
                    "revenue_growth_pct": 7.0,
                },
                "dividend": {"dividend_yield": 0.03},
            },
            "assessment": {"dimensions": {"quality": {"score": 70.0}}},
        }
    }


def test_fallback_matrix_definitions_are_ordered_and_bounded() -> None:
    expected = {
        "stock": [
            "stock_valuation_history",
            "stock_price_history",
            "valuation_price_projection",
            "stock_terminal",
        ],
        "fund": [
            "exchange_fund_price_history",
            "fund_nav_history",
            "fund_holdings_valuation",
            "fund_index_matrix",
            "fund_terminal",
        ],
        "index": [
            "official_index_fundamentals",
            "target_etf_nav_history",
            "sina_index_price_history",
            "eastmoney_index_price_history",
            "index_price_position_proxy",
            "index_terminal",
        ],
    }

    for key, matrix in FALLBACK_MATRICES.items():
        step_keys = [step.key for step in matrix.steps]
        assert step_keys == expected[key]
        assert len(step_keys) == len(set(step_keys))
        assert all(step.timeout_budget_seconds > 0 for step in matrix.steps)
        assert all(step.source for step in matrix.steps)
        assert all(step.admission_condition for step in matrix.steps)
        assert all(step.output_method for step in matrix.steps)
        assert all(step.stop_condition for step in matrix.steps)


def test_fallback_trace_serializes_stable_reason_codes() -> None:
    trace = new_fallback_trace("stock")
    trace.record(
        "stock_valuation_history",
        "unavailable",
        reason="Stock source disconnected: volatile upstream detail",
    )
    trace.record(
        "stock_price_history",
        "available",
        selected=True,
    )
    trace.finish(terminal_reason="Fallback complete: ignored detail")
    result = {"assessment": {"data_quality": {}}}

    attach_fallback_traces(result, trace)

    serialized = result["fallback_matrices"]["stock"]
    assert serialized["terminal_reason"] == "fallback_complete"
    assert serialized["selected_step"] == "stock_price_history"
    assert serialized["selected_method"] == "stock_price_history"
    assert serialized["steps"][0]["admission_condition"]
    assert serialized["steps"][0]["output_method"] == "fundamental_valuation"
    assert serialized["steps"][0]["success_method"] == "fundamental_valuation"
    assert serialized["steps"][0]["reason"] == "stock_source_disconnected"
    assert result["assessment"]["data_quality"]["fallback_matrices"] == (
        result["fallback_matrices"]
    )
    assert stable_reason_code("UPSTREAM timeout: host=private") == "upstream_timeout"


def test_stock_valuation_failure_with_price_returns_unavailable_assessment() -> None:
    class Client(StockClientBase):
        def get_stock_valuation(self, code: str) -> list[StockValuationPoint]:
            raise EastmoneyError("valuation offline")

        def get_stock_history(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            return [stock_bar(start), stock_bar(end, 11.0)]

    result = MarketAnalysisAgent(Client()).analyze("stock", "600000", START, END)

    assert result["assessment"]["status"] == "unavailable"
    assert result["performance"]["sample_size"] == 2
    trace = result["fallback_matrices"]["stock"]
    assert trace["selected_step"] == "stock_terminal"
    assert trace["steps"][0]["reason"] == "stock_valuation_history_unavailable"


def test_stock_price_failure_uses_verified_valuation_closes() -> None:
    class Client(StockClientBase):
        def get_stock_valuation(self, code: str) -> list[StockValuationPoint]:
            return stock_valuation_rows()

        def get_stock_history(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            raise EastmoneyError("price offline")

    rows = stock_valuation_rows()
    result = MarketAnalysisAgent(Client()).analyze(
        "stock",
        "600000",
        rows[0].date,
        END,
    )

    assert result["assessment"]["status"] == "complete"
    assert result["performance"]["sample_size"] == 300
    trace = result["fallback_matrices"]["stock"]
    assert trace["selected_step"] == "stock_valuation_history"
    assert trace["steps"][2]["status"] == "available"


def test_stock_price_timeout_preserves_verified_valuation_result() -> None:
    release = Event()

    class Client(StockClientBase):
        def get_stock_valuation(self, code: str) -> list[StockValuationPoint]:
            return stock_valuation_rows()

        def get_stock_history(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            release.wait(timeout=0.2)
            return [stock_bar(start), stock_bar(end, 11.0)]

    rows = stock_valuation_rows()
    agent = MarketAnalysisAgent(
        Client(),
        StageExecutor({("stock", "stock_price_history"): 0.01}),
    )
    try:
        result = agent.analyze("stock", "600000", rows[0].date, END)
    finally:
        release.set()

    AnalysisResult.model_validate(result)
    trace = result["fallback_matrices"]["stock"]
    assert result["assessment"]["status"] == "complete"
    assert result["performance"]["sample_size"] == 300
    assert trace["version"] == "fallback-matrix-v2"
    assert trace["timeout_policy"] == "hard_stage_deadline"
    assert trace["steps"][1]["reason"] == "stock_price_history_timeout"
    assert trace["steps"][1]["details"]["deadline_enforced"] is True
    assert trace["steps"][2]["status"] == "available"
    assert result["assessment"]["data_quality"]["stage_timeouts"][0]["step"] == (
        "stock_price_history"
    )


def test_stock_without_price_or_valuation_returns_terminal_result() -> None:
    class Client(StockClientBase):
        def get_stock_valuation(self, code: str) -> list[StockValuationPoint]:
            raise EastmoneyError("valuation offline")

        def get_stock_history(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            raise EastmoneyError("price offline")

    result = MarketAnalysisAgent(Client()).analyze("stock", "600000", START, END)

    assert result["valuation"]["score"] is None
    assert result["assessment"]["status"] == "unavailable"
    assert "stock_price_data_unavailable" in result["assessment"]["fallback_reasons"]
    assert result["fallback_matrices"]["stock"]["selected_step"] == "stock_terminal"


def test_stock_empty_critical_sources_return_terminal_result() -> None:
    class Client(StockClientBase):
        def get_stock_valuation(self, code: str) -> list[StockValuationPoint]:
            return []

        def get_stock_history(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            return []

    result = MarketAnalysisAgent(Client()).analyze("stock", "600000", START, END)

    AnalysisResult.model_validate(result)
    assert result["valuation"]["score"] is None
    assert result["assessment"]["status"] == "unavailable"
    assert result["fallback_matrices"]["stock"]["selected_step"] == "stock_terminal"
    assert result["fallback_matrices"]["stock"]["steps"][0]["reason"] == (
        "stock_valuation_history_empty"
    )
    assert result["fallback_matrices"]["stock"]["steps"][1]["reason"] == (
        "stock_price_history_empty"
    )


def test_fund_without_exchange_price_or_nav_returns_terminal_result() -> None:
    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            raise EastmoneyError("exchange price offline")

        def get_fund_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            raise EastmoneyError("nav offline")

    result = MarketAnalysisAgent(Client()).analyze("fund", "000001", START, END)

    assert result["assessment"]["status"] == "unavailable"
    assert result["fallback_matrices"]["fund"]["selected_step"] == "fund_terminal"
    assert result["fallback_matrices"]["index"]["terminal_reason"] == (
        "tracked_index_not_applicable"
    )
    AnalysisResult.model_validate(result)


def test_fund_timeout_routes_return_stable_terminal_result() -> None:
    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            raise EastmoneyError(
                "Read timeout from https://private-upstream.example/quote"
            )

        def get_fund_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            raise EastmoneyError(
                "Read timeout from https://private-upstream.example/nav"
            )

    result = MarketAnalysisAgent(Client()).analyze("fund", "000001", START, END)

    AnalysisResult.model_validate(result)
    serialized = str(result)
    assert result["assessment"]["status"] == "unavailable"
    assert result["assessment"]["fallback_reasons"] == [
        "fund_nav_data_unavailable",
        "exchange_fund_price_history_unavailable",
        "fund_nav_history_unavailable",
    ]
    assert result["fallback_matrices"]["fund"]["selected_step"] == "fund_terminal"
    assert "private-upstream.example" not in serialized
    assert "Read timeout" not in serialized


def test_reit_profile_failure_returns_terminal_result() -> None:
    class Client:
        def get_fund_product_info(self, code: str) -> FundProductInfo:
            return FundProductInfo(
                fund_code=code,
                fund_name="Test REIT",
                fund_type="Reits",
                establishment_date=date(2022, 1, 1),
                scale_report_date=date(2026, 6, 30),
                period_end_net_assets_cny=1_000_000_000.0,
                management_fee_pct=0.2,
                custody_fee_pct=0.05,
                sales_service_fee_pct=None,
                benchmark=None,
                raw={},
            )

        def get_reit_profile(self, code: str) -> object:
            raise EastmoneyError(f"REIT profile disconnected for {code}")

    result = MarketAnalysisAgent(Client()).analyze(  # type: ignore[arg-type]
        "fund",
        "180101",
        START,
        END,
    )

    AnalysisResult.model_validate(result)
    assert result["name"] == "Test REIT"
    assert result["assessment"]["profile"] == "reit_basic"
    assert result["assessment"]["status"] == "unavailable"
    assert result["assessment"]["fallback_reasons"] == ["reit_profile_unavailable"]
    assert result["fallback_matrices"]["fund"]["selected_step"] == "fund_terminal"
    assert result["fallback_matrices"]["fund"]["terminal_reason"] == (
        "reit_profile_unavailable"
    )


def test_fund_name_failure_preserves_valid_holdings_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = active_holdings_route()

    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [fund_nav(start, 1.0), fund_nav(end, 1.1)]

        def get_fund_name(self, code: str) -> str:
            raise EastmoneyError("name offline")

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            return route

    monkeypatch.setattr(
        MarketAnalysisAgent,
        "_analyze_fund_holdings",
        lambda self, holdings, end: holding_analyses(),
    )

    result = MarketAnalysisAgent(Client()).analyze("fund", "000001", START, END)

    assert result["assessment"]["dimensions"]["valuation"]["score"] is not None
    assert result["fallback_matrices"]["fund"]["selected_step"] == (
        "fund_holdings_valuation"
    )
    assert all(
        step["status"] == "skipped"
        for step in result["fallback_matrices"]["index"]["steps"]
    )
    source = next(
        item
        for item in result["assessment"]["data_quality"]["sources"]
        if item["key"] == "fund_name"
    )
    assert source["reason"] == "fund_name_unavailable"


def test_holdings_route_failure_is_preserved_in_assessment() -> None:
    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [fund_nav(start, 1.0), fund_nav(end, 1.1)]

        def get_fund_name(self, code: str) -> str:
            return "Active fund"

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            raise EastmoneyError("holdings upstream detail")

        def find_index_for_fund(self, name: str) -> None:
            return None

    result = MarketAnalysisAgent(Client()).analyze("fund", "000001", START, END)

    assert result["assessment"]["status"] == "unavailable"
    assert "fund_holdings_route_unavailable" in (
        result["assessment"]["fallback_reasons"]
    )
    assert result["holdings_route"]["fallback_reasons"] == [
        "fund_holdings_route_unavailable"
    ]
    assert result["fallback_matrices"]["fund"]["selected_step"] == "fund_terminal"


def test_fund_holdings_timeout_preserves_nav_performance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = Event()
    route = active_holdings_route()

    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [fund_nav(start, 1.0), fund_nav(end, 1.1)]

        def get_fund_name(self, code: str) -> str:
            return "Active fund"

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            return route

        def find_index_for_fund(self, name: str) -> None:
            return None

    def blocked_holdings_analysis(
        self: MarketAnalysisAgent,
        holdings: list[FundHolding],
        end: date,
    ) -> dict[str, dict[str, object]]:
        del self, holdings, end
        release.wait(timeout=0.2)
        return holding_analyses()

    monkeypatch.setattr(
        MarketAnalysisAgent,
        "_analyze_fund_holdings",
        blocked_holdings_analysis,
    )
    agent = MarketAnalysisAgent(
        Client(),
        StageExecutor({("fund", "fund_holdings_valuation"): 0.01}),
    )
    try:
        result = agent.analyze("fund", "000001", START, END)
    finally:
        release.set()

    AnalysisResult.model_validate(result)
    assert result["performance"]["sample_size"] == 2
    assert result["assessment"]["status"] == "unavailable"
    assert result["fallback_matrices"]["fund"]["steps"][2]["reason"] == (
        "fund_holdings_valuation_timeout"
    )
    assert "fund_holdings_valuation_timeout" in (
        result["assessment"]["fallback_reasons"]
    )


def test_official_index_failure_uses_index_price_proxy() -> None:
    route, candidate = tracked_index_fixture()

    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [fund_nav(start, 1.0), fund_nav(end, 1.1)]

        def get_fund_name(self, code: str) -> str:
            return "CSI 300 ETF"

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            return route

        def get_csi_index_valuation_history(self, index_code: str) -> list[object]:
            raise EastmoneyError("official source offline")

        def search_assets(
            self,
            keyword: str,
            *,
            limit: int,
            include_indexes: bool,
        ) -> list[AssetSearchResult]:
            return [candidate]

        def get_sina_index_history(
            self,
            index_code: str,
            quote_id: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            return [stock_bar(start, 3000.0), stock_bar(end, 3200.0)]

    result = MarketAnalysisAgent(Client()).analyze("fund", "510300", START, END)

    assert result["assessment"]["method"] == "price_position_proxy"
    assert result["assessment"]["status"] == "degraded"
    assert result["fallback_matrices"]["fund"]["selected_step"] == "fund_index_matrix"
    assert result["fallback_matrices"]["index"]["selected_step"] == (
        "index_price_position_proxy"
    )
    assert result["index_data_route"]["fallback_reasons"] == [
        "official_index_valuation_unavailable"
    ]


def test_index_provider_failures_continue_to_eastmoney_price_proxy() -> None:
    route, candidate = tracked_index_fixture()

    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [fund_nav(start, 1.0), fund_nav(end, 1.1)]

        def get_fund_name(self, code: str) -> str:
            return "CSI 300 ETF"

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            return route

        def get_csi_index_valuation_history(self, index_code: str) -> list[object]:
            raise EastmoneyError("official index request timed out")

        def search_assets(
            self,
            keyword: str,
            *,
            limit: int,
            include_indexes: bool,
        ) -> list[AssetSearchResult]:
            return [candidate]

        def get_sina_index_history(
            self,
            index_code: str,
            quote_id: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            raise TypeError("malformed Sina response")

        def get_index_history(
            self,
            quote_id: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            return [stock_bar(start, 3000.0), stock_bar(end, 3200.0)]

    result = MarketAnalysisAgent(Client()).analyze("fund", "510300", START, END)

    AnalysisResult.model_validate(result)
    index_trace = result["fallback_matrices"]["index"]
    assert result["assessment"]["status"] == "degraded"
    assert result["assessment"]["method"] == "price_position_proxy"
    assert index_trace["selected_step"] == "index_price_position_proxy"
    assert index_trace["steps"][2]["reason"] == "sina_index_price_history_unavailable"
    assert index_trace["steps"][3]["status"] == "available"
    assert "malformed Sina response" not in str(result)


def test_sina_index_timeout_continues_to_eastmoney_price_proxy() -> None:
    release = Event()
    route, candidate = tracked_index_fixture()

    class Client(FundClientBase):
        def get_exchange_fund_price_nav(
            self,
            code: str,
            *,
            start: date,
            end: date,
        ) -> list[FundNavPoint]:
            return [fund_nav(start, 1.0), fund_nav(end, 1.1)]

        def get_fund_name(self, code: str) -> str:
            return "CSI 300 ETF"

        def get_fund_holdings_route(
            self,
            code: str,
            *,
            fund_name: str | None,
            analysis_end: date,
        ) -> FundHoldingsRoute:
            return route

        def get_csi_index_valuation_history(self, index_code: str) -> list[object]:
            raise EastmoneyError("official source offline")

        def search_assets(
            self,
            keyword: str,
            *,
            limit: int,
            include_indexes: bool,
        ) -> list[AssetSearchResult]:
            return [candidate]

        def get_sina_index_history(
            self,
            index_code: str,
            quote_id: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            release.wait(timeout=0.2)
            return [stock_bar(start, 3000.0), stock_bar(end, 3200.0)]

        def get_index_history(
            self,
            quote_id: str,
            *,
            start: date,
            end: date,
        ) -> list[StockBar]:
            return [stock_bar(start, 3000.0), stock_bar(end, 3200.0)]

    agent = MarketAnalysisAgent(
        Client(),
        StageExecutor({("index", "sina_index_price_history"): 0.01}),
    )
    try:
        result = agent.analyze("fund", "510300", START, END)
    finally:
        release.set()

    AnalysisResult.model_validate(result)
    index_trace = result["fallback_matrices"]["index"]
    assert result["assessment"]["status"] == "degraded"
    assert result["assessment"]["method"] == "price_position_proxy"
    assert index_trace["steps"][2]["reason"] == "sina_index_price_history_timeout"
    assert index_trace["steps"][3]["status"] == "available"
    assert index_trace["selected_step"] == "index_price_position_proxy"
