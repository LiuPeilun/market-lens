from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any

from market_lens.agent.stage_executor import (
    StageBudget,
    StageExecutor,
    StageTimeoutError,
)
from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    is_a_share_symbol,
    stock_bars_from_valuations,
)
from market_lens.errors import InvalidRequestError
from market_lens.types import (
    AssetSearchResult,
    FundHolding,
    FundHoldingsRoute,
    FundHoldingsSnapshot,
    FundIndexDataRoute,
    FundNavPoint,
    ReitDistribution,
    ReitFinancialSnapshot,
    ReitPeriodicReportNotice,
    ReitPriceBar,
    ReitProfile,
    StockBar,
    StockIndustryValuationSnapshot,
    StockValuationPoint,
)
from market_lens.valuation.analyzer import analyze_fund, analyze_stock
from market_lens.valuation.assessment import (
    build_fund_assessment,
    build_unavailable_assessment,
)
from market_lens.valuation.fallback_matrix import (
    FallbackTrace,
    attach_fallback_traces,
    new_fallback_trace,
    stable_reason_code,
)
from market_lens.valuation.framework import analyze_index_price_proxy
from market_lens.valuation.index_data import (
    analyze_csi_index_valuation,
    build_csi_fund_index_data_route,
    serialize_fund_index_data_route,
    unavailable_fund_index_data_route,
)
from market_lens.valuation.metrics import (
    annualized_return,
    format_pct,
    max_drawdown,
    simple_return,
)
from market_lens.valuation.research_context import (
    build_fund_research_context,
    build_reit_research_context,
    build_stock_research_context,
)
from market_lens.valuation.routing import route_asset_model

MAX_FUND_HOLDING_ANALYSES = 30


class MarketAnalysisAgent:
    """Business agent that calls fixed tools and returns structured analysis."""

    def __init__(
        self,
        data_client: EastmoneyClient | None = None,
        stage_executor: StageExecutor | None = None,
    ) -> None:
        self.data_client = data_client or EastmoneyClient()
        self.stage_executor = stage_executor or StageExecutor()

    def analyze(
        self,
        asset_type: str,
        code: str,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        retrieved_at = datetime.now(UTC)
        if asset_type == "stock":
            stock_trace = new_fallback_trace("stock")
            consume_lkg_events(self.data_client)
            try:
                all_valuations = self.stage_executor.run(
                    "stock",
                    "stock_valuation_history",
                    lambda: self.data_client.get_stock_valuation(code),
                )
            except StageTimeoutError as exc:
                all_valuations = []
                record_stage_timeout(
                    stock_trace,
                    "stock_valuation_history",
                    exc,
                )
            except (EastmoneyError, KeyError, TypeError, ValueError):
                all_valuations = []
                stock_trace.record(
                    "stock_valuation_history",
                    "unavailable",
                    reason="stock_valuation_history_unavailable",
                )
            else:
                stock_trace.record(
                    "stock_valuation_history",
                    "available" if all_valuations else "unavailable",
                    reason=None if all_valuations else "stock_valuation_history_empty",
                )
            stock_name = next((item.name for item in reversed(all_valuations) if item.name), None)
            valuations = [item for item in all_valuations if start <= item.date <= end]
            try:
                bars = self.stage_executor.run(
                    "stock",
                    "stock_price_history",
                    lambda: self.data_client.get_stock_history(
                        code,
                        start=start,
                        end=end,
                    ),
                )
            except StageTimeoutError as exc:
                bars = []
                record_stage_timeout(
                    stock_trace,
                    "stock_price_history",
                    exc,
                )
            except (EastmoneyError, KeyError, TypeError, ValueError):
                bars = []
                stock_trace.record(
                    "stock_price_history",
                    "unavailable",
                    reason="stock_price_history_unavailable",
                )
            else:
                stock_trace.record(
                    "stock_price_history",
                    "available" if bars else "unavailable",
                    reason=None if bars else "stock_price_history_empty",
                )
            if not bars:
                try:
                    bars = self.stage_executor.run(
                        "stock",
                        "valuation_price_projection",
                        lambda: stock_bars_from_valuations(valuations),
                    )
                except StageTimeoutError as exc:
                    bars = []
                    record_stage_timeout(
                        stock_trace,
                        "valuation_price_projection",
                        exc,
                    )
                else:
                    stock_trace.record(
                        "valuation_price_projection",
                        "available" if bars else "unavailable",
                        reason=(
                            None
                            if bars
                            else "valuation_price_projection_unavailable"
                        ),
                    )
            else:
                stock_trace.record(
                    "valuation_price_projection",
                    "skipped",
                    reason="stock_price_history_selected",
                )
            if not bars:
                stock_trace.record(
                    "stock_terminal",
                    "available",
                    reason="stock_price_data_unavailable",
                    selected=True,
                )
                stock_trace.finish(terminal_reason="stock_price_data_unavailable")
                result = build_terminal_market_result(
                    asset_type="stock",
                    code=code,
                    analysis_as_of=end,
                    retrieved_at=retrieved_at,
                    fallback_reasons=list(
                        dict.fromkeys(
                            [
                                "stock_price_data_unavailable",
                                *stock_trace.reason_codes(),
                            ]
                        )
                    ),
                )
                attach_fallback_traces(result, stock_trace)
                append_stage_timeout_reasons(result, stock_trace)
                return result
            critical_lkg_events = consume_lkg_events(self.data_client)
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
            result = analyze_stock(
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
            financial_scope = str(
                ((result.get("valuation") or {}).get("factor_data") or {}).get(
                    "model_scope"
                )
                or "unknown"
            )
            candidate_route = route_asset_model(
                declared_asset_type="stock",
                stock_profile=profile,
                financial_scope=financial_scope,
            )
            detailed_rows, detailed_errors = self._load_stock_detailed_financials(
                code,
                main_model=candidate_route.main_model,
            )
            result["research"] = build_stock_research_context(
                analysis_as_of=bars[-1].date,
                stock_profile=profile,
                financial_scope=financial_scope,
                balance_sheets=detailed_rows["balance_sheet"],
                income_statements=detailed_rows["income_statement"],
                cash_flow_statements=detailed_rows["cash_flow_statement"],
                errors=detailed_errors,
                retrieved_at=retrieved_at,
            )
            consume_lkg_events(self.data_client)
            valuation_score = (
                ((result.get("assessment") or {}).get("dimensions") or {})
                .get("valuation", {})
                .get("score")
            )
            if is_finite_number(valuation_score):
                stock_trace.record(
                    "stock_valuation_history",
                    "available",
                    selected=True,
                )
                stock_trace.record(
                    "stock_terminal",
                    "skipped",
                    reason="stock_fundamental_valuation_selected",
                )
            else:
                stock_trace.record(
                    "stock_terminal",
                    "available",
                    reason="stock_valuation_score_unavailable",
                    selected=True,
                )
                stock_trace.finish(terminal_reason="stock_valuation_score_unavailable")
                append_assessment_fallback_reasons(
                    result,
                    ["stock_valuation_score_unavailable"],
                )
            attach_fallback_traces(result, stock_trace)
            append_stage_timeout_reasons(result, stock_trace)
            apply_last_known_good_diagnostics(result, critical_lkg_events)
            return result
        if asset_type == "fund":
            fund_trace = new_fallback_trace("fund")
            index_trace = new_fallback_trace("index")
            product_info = None
            product_info_error = None
            try:
                product_info = self.data_client.get_fund_product_info(code)
            except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
                product_info_error = str(exc)
            if (
                product_info is not None
                and str(product_info.fund_type or "").strip().casefold() == "reits"
            ):
                mark_fund_steps_skipped_for_reit(fund_trace)
                try:
                    reit_profile = self.data_client.get_reit_profile(code)
                except (EastmoneyError, KeyError, TypeError, ValueError):
                    fund_trace.record(
                        "fund_terminal",
                        "available",
                        reason="reit_profile_unavailable",
                        selected=True,
                    )
                    fund_trace.finish(terminal_reason="reit_profile_unavailable")
                    result = build_terminal_market_result(
                        asset_type="fund",
                        code=code,
                        name=product_info.fund_name,
                        profile="reit_basic",
                        analysis_as_of=end,
                        retrieved_at=retrieved_at,
                        fallback_reasons=["reit_profile_unavailable"],
                    )
                else:
                    result = self._analyze_reit(
                        reit_profile,
                        start=start,
                        end=end,
                        retrieved_at=retrieved_at,
                    )
                    fund_trace.record(
                        "fund_terminal",
                        "available",
                        reason="reit_production_model_unavailable",
                        selected=True,
                    )
                    fund_trace.finish(
                        terminal_reason="reit_production_model_unavailable"
                    )
                attach_fallback_traces(result, fund_trace)
                return result

            consume_lkg_events(self.data_client)
            try:
                nav_points = self.stage_executor.run(
                    "fund",
                    "exchange_fund_price_history",
                    lambda: self.data_client.get_exchange_fund_price_nav(
                        code,
                        start=start,
                        end=end,
                    ),
                )
            except StageTimeoutError as exc:
                nav_points = []
                record_stage_timeout(
                    fund_trace,
                    "exchange_fund_price_history",
                    exc,
                )
            except (EastmoneyError, KeyError, TypeError, ValueError):
                nav_points = []
                fund_trace.record(
                    "exchange_fund_price_history",
                    "unavailable",
                    reason="exchange_fund_price_history_unavailable",
                )
            else:
                fund_trace.record(
                    "exchange_fund_price_history",
                    "available" if nav_points else "unavailable",
                    reason=None if nav_points else "exchange_fund_price_history_not_applicable",
                    selected=bool(nav_points),
                )
            fund_data_source = "exchange_price_history" if nav_points else "fund_nav_history"
            if not nav_points:
                try:
                    nav_points = self.stage_executor.run(
                        "fund",
                        "fund_nav_history",
                        lambda: self.data_client.get_fund_nav(
                            code,
                            start=start,
                            end=end,
                        ),
                    )
                except StageTimeoutError as exc:
                    nav_points = []
                    record_stage_timeout(
                        fund_trace,
                        "fund_nav_history",
                        exc,
                    )
                except (EastmoneyError, KeyError, TypeError, ValueError):
                    nav_points = []
                    fund_trace.record(
                        "fund_nav_history",
                        "unavailable",
                        reason="fund_nav_history_unavailable",
                    )
                else:
                    fund_trace.record(
                        "fund_nav_history",
                        "available" if nav_points else "unavailable",
                        reason=None if nav_points else "fund_nav_history_empty",
                        selected=bool(nav_points),
                    )
            else:
                fund_trace.record(
                    "fund_nav_history",
                    "skipped",
                    reason="exchange_fund_price_history_selected",
                )
            if not nav_points:
                fund_trace.record(
                    "fund_holdings_valuation",
                    "skipped",
                    reason="fund_nav_data_unavailable",
                )
                fund_trace.record(
                    "fund_index_matrix",
                    "skipped",
                    reason="fund_nav_data_unavailable",
                )
                fund_trace.record(
                    "fund_terminal",
                    "available",
                    reason="fund_nav_data_unavailable",
                    selected=True,
                )
                fund_trace.finish(terminal_reason="fund_nav_data_unavailable")
                result = build_terminal_market_result(
                    asset_type="fund",
                    code=code,
                    analysis_as_of=end,
                    retrieved_at=retrieved_at,
                    fallback_reasons=list(
                        dict.fromkeys(
                            [
                                "fund_nav_data_unavailable",
                                *fund_trace.reason_codes(),
                            ]
                        )
                    ),
                )
                mark_index_trace_not_applicable(index_trace)
                attach_fallback_traces(result, fund_trace, index_trace)
                append_stage_timeout_reasons(result, fund_trace, index_trace)
                return result
            critical_lkg_events = consume_lkg_events(self.data_client)
            fund_name_error = None
            try:
                fund_name = self.data_client.get_fund_name(code)
            except (EastmoneyError, KeyError, TypeError, ValueError):
                fund_name = None
                fund_name_error = "fund_name_unavailable"
            holdings_budget = self.stage_executor.start_budget(
                "fund",
                "fund_holdings_valuation",
            )
            holdings_route = None
            holdings_route_reasons: list[str] = []
            holdings_timeout_reason = None
            try:
                holdings_route = self.stage_executor.run_remaining(
                    holdings_budget,
                    lambda: self.data_client.get_fund_holdings_route(
                        code,
                        fund_name=fund_name,
                        analysis_end=end,
                    ),
                )
                holdings = holdings_route.holdings
            except StageTimeoutError as exc:
                holdings = []
                holdings_timeout_reason = exc.reason_code
                holdings_route_reasons.append(exc.reason_code)
            except (EastmoneyError, KeyError, TypeError, ValueError):
                holdings = []
                holdings_route_reasons.append("fund_holdings_route_unavailable")
            try:
                holding_analyses = (
                    self.stage_executor.run_remaining(
                        holdings_budget,
                        lambda: self._analyze_fund_holdings(holdings, end=end),
                    )
                    if holdings
                    else {}
                )
            except StageTimeoutError as exc:
                holding_analyses = {}
                holdings_timeout_reason = exc.reason_code
                if exc.reason_code not in holdings_route_reasons:
                    holdings_route_reasons.append(exc.reason_code)

            index_budget = self.stage_executor.start_budget(
                "fund",
                "fund_index_matrix",
            )
            try:
                index_data_route, official_index_valuation = self.stage_executor.run(
                    "index",
                    "official_index_fundamentals",
                    lambda: self._load_official_index_valuation(
                        holdings_route,
                        fund_name=fund_name,
                        analysis_end=end,
                    ),
                    parent=index_budget,
                )
            except StageTimeoutError as exc:
                tracking = holdings_route.tracking if holdings_route else None
                index_data_route = unavailable_fund_index_data_route(
                    tracking,
                    exc.reason_code,
                )
                official_index_valuation = None
            index_candidate, index_bars, benchmark_source = self._load_tracked_index_history(
                holdings_route,
                start=start,
                end=end,
                trace=index_trace,
                parent_budget=index_budget,
            )
            result = analyze_fund(
                code,
                nav_points,
                name=fund_name,
                holdings=holdings,
                holding_analyses=holding_analyses,
                product_info=product_info,
                product_info_error=product_info_error,
                holdings_route=holdings_route,
                benchmark_bars=index_bars,
                benchmark_source=benchmark_source,
                data_source=fund_data_source,
                retrieved_at=retrieved_at,
            )
            apply_holdings_route_method(result["valuation"], holdings_route)
            route_metadata = serialize_holdings_route(
                holdings_route,
                fallback_reasons=holdings_route_reasons,
            )
            result["holdings_route"] = route_metadata
            result["valuation"]["holdings_route"] = route_metadata
            holdings_score = result["valuation"].get("score")

            official_index_valuation_used = bool(
                official_index_valuation
                and official_index_valuation.get("score") is not None
            )
            if index_data_route.scope == "unavailable":
                index_trace.record(
                    "official_index_fundamentals",
                    "unavailable",
                    reason=first_stable_reason(
                        index_data_route.fallback_reasons,
                        default="official_index_fundamentals_unavailable",
                    ),
                )
            else:
                index_trace.record(
                    "official_index_fundamentals",
                    "available",
                    selected=official_index_valuation_used,
                    reason=(
                        None
                        if official_index_valuation_used
                        else "official_index_scoring_gates_not_met"
                    ),
                )
            if official_index_valuation_used and official_index_valuation is not None:
                previous_valuation = result["valuation"]
                result["valuation"] = official_index_valuation
                preserve_fund_valuation_context(
                    result["valuation"],
                    previous_valuation,
                    route_metadata,
                )
                index_data_route = replace(
                    index_data_route,
                    scoring_eligible=True,
                )

            fund_trace.record(
                "fund_holdings_valuation",
                "available" if is_finite_number(holdings_score) else "unavailable",
                reason=(
                    None
                    if is_finite_number(holdings_score)
                    else holdings_timeout_reason or "holdings_valuation_unavailable"
                ),
                selected=bool(
                    is_finite_number(holdings_score)
                    and not official_index_valuation_used
                ),
            )
            if result["valuation"].get("score") is None:
                if index_candidate is None:
                    try:
                        index_candidate = self.stage_executor.run_remaining(
                            index_budget,
                            lambda: self.data_client.find_index_for_fund(
                                fund_name or code
                            ),
                        )
                    except StageTimeoutError as exc:
                        index_candidate = None
                        record_stage_timeout(
                            index_trace,
                            "eastmoney_index_price_history",
                            exc,
                        )
                    except (EastmoneyError, KeyError, TypeError, ValueError):
                        index_candidate = None
                    if index_candidate and index_candidate.quote_id:
                        try:
                            index_bars = self.stage_executor.run(
                                "index",
                                "eastmoney_index_price_history",
                                lambda: self.data_client.get_index_history(
                                    index_candidate.quote_id,
                                    start=start,
                                    end=end,
                                ),
                                parent=index_budget,
                            )
                        except StageTimeoutError as exc:
                            index_bars = []
                            record_stage_timeout(
                                index_trace,
                                "eastmoney_index_price_history",
                                exc,
                            )
                        except (EastmoneyError, KeyError, TypeError, ValueError):
                            index_bars = []
                            index_trace.record(
                                "eastmoney_index_price_history",
                                "unavailable",
                                reason="eastmoney_index_price_history_unavailable",
                            )
                        if index_bars:
                            benchmark_source = "tracked_index_price_history"
                            index_trace.record(
                                "eastmoney_index_price_history",
                                "available",
                            )
                index_proxy_sources = {
                    "target_etf_nav_history",
                    "tracked_index_price_history",
                    "sina_index_price_history",
                }
                if (
                    index_candidate
                    and index_candidate.quote_id
                    and index_bars
                    and benchmark_source in index_proxy_sources
                ):
                    previous_valuation = result["valuation"]
                    try:
                        result["valuation"] = self.stage_executor.run(
                            "index",
                            "index_price_position_proxy",
                            lambda: analyze_index_price_proxy(
                                index_bars=index_bars,
                                index_code=index_candidate.code,
                                index_name=index_candidate.name,
                                index_quote_id=index_candidate.quote_id,
                            ),
                            parent=index_budget,
                        )
                    except StageTimeoutError as exc:
                        record_stage_timeout(
                            index_trace,
                            "index_price_position_proxy",
                            exc,
                        )
                    else:
                        preserve_fund_valuation_context(
                            result["valuation"],
                            previous_valuation,
                            route_metadata,
                        )
            proxy_used = (
                result["valuation"].get("status") == "proxy_valuation"
                and is_finite_number(result["valuation"].get("score"))
            )
            if proxy_used:
                index_trace.record(
                    "index_price_position_proxy",
                    "available",
                    selected=True,
                )
            elif (
                not official_index_valuation_used
                and not index_trace.was_recorded("index_price_position_proxy")
            ):
                index_trace.record(
                    "index_price_position_proxy",
                    "unavailable",
                    reason="index_price_position_proxy_unavailable",
                )
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
            index_data_metadata = serialize_fund_index_data_route(index_data_route)
            index_data_metadata["fallback_reasons"] = stable_reason_codes(
                index_data_metadata.get("fallback_reasons")
            )
            result["index_data_route"] = index_data_metadata
            result["valuation"]["index_data_route"] = index_data_metadata
            if official_index_valuation_used:
                result["notes"] = official_index_valuation_notes(result["notes"])
                result["notes"].insert(
                    0,
                    "Valuation uses the tracked index's official CSI PE TTM historical "
                    "percentile; complete monthly constituent weights validate index coverage.",
                )
                result["notes"].insert(
                    1,
                    "Disclosed top holdings are used only for the separate underlying-quality "
                    "dimension and do not determine the index valuation score.",
                )
            else:
                result["notes"].insert(0, holdings_route_note(holdings_route))
            result["assessment"] = build_fund_assessment(
                result,
                retrieved_at=retrieved_at,
            )
            final_score = (
                ((result.get("assessment") or {}).get("dimensions") or {})
                .get("valuation", {})
                .get("score")
            )
            index_relevant = bool(
                holdings_route
                and holdings_route.tracking
                and holdings_route.tracking.index_code
            )
            if official_index_valuation_used or proxy_used:
                fund_trace.record(
                    "fund_index_matrix",
                    "available",
                    selected=True,
                )
                fund_trace.record(
                    "fund_terminal",
                    "skipped",
                    reason="fund_index_matrix_selected",
                )
                index_trace.record(
                    "index_terminal",
                    "skipped",
                    reason="index_valuation_selected",
                )
            elif is_finite_number(final_score):
                fund_trace.record(
                    "fund_index_matrix",
                    "skipped",
                    reason="fund_holdings_valuation_selected",
                )
                fund_trace.record(
                    "fund_terminal",
                    "skipped",
                    reason="fund_holdings_valuation_selected",
                )
                if not index_relevant:
                    mark_index_trace_not_applicable(index_trace)
                else:
                    index_trace.record(
                        "index_terminal",
                        "available",
                        reason="index_valuation_unavailable",
                        selected=True,
                    )
                    index_trace.finish(terminal_reason="index_valuation_unavailable")
            else:
                fund_trace.record(
                    "fund_index_matrix",
                    "unavailable",
                    reason="index_fallback_unavailable",
                )
                fund_trace.record(
                    "fund_terminal",
                    "available",
                    reason="fund_valuation_unavailable",
                    selected=True,
                )
                fund_trace.finish(terminal_reason="fund_valuation_unavailable")
                append_assessment_fallback_reasons(
                    result,
                    ["fund_valuation_unavailable", *holdings_route_reasons],
                )
                if index_relevant:
                    index_trace.record(
                        "index_terminal",
                        "available",
                        reason="index_valuation_unavailable",
                        selected=True,
                    )
                    index_trace.finish(terminal_reason="index_valuation_unavailable")
                else:
                    mark_index_trace_not_applicable(index_trace)
            if fund_name_error:
                append_optional_source_diagnostic(
                    result,
                    key="fund_name",
                    source="eastmoney_pingzhongdata",
                    reason=fund_name_error,
                    retrieved_at=retrieved_at,
                )
            product_profile = (
                ((result.get("valuation") or {}).get("product_data") or {}).get(
                    "profile"
                )
            )
            if product_profile not in {"etf", "etf_linked", "index_fund", "active_fund"}:
                product_profile = None
            result["research"] = build_fund_research_context(
                analysis_as_of=nav_points[-1].date,
                product_profile=product_profile,
                retrieved_at=retrieved_at,
            )
            consume_lkg_events(self.data_client)
            attach_fallback_traces(result, fund_trace, index_trace)
            append_stage_timeout_reasons(result, fund_trace, index_trace)
            apply_last_known_good_diagnostics(result, critical_lkg_events)
            return result
        raise InvalidRequestError(
            "unsupported_asset_type",
            "asset_type must be 'stock' or 'fund'",
        )

    def _load_stock_detailed_financials(
        self,
        code: str,
        *,
        main_model: str,
    ) -> tuple[dict[str, list[Any]], dict[str, str]]:
        empty_rows: dict[str, list[Any]] = {
            "balance_sheet": [],
            "income_statement": [],
            "cash_flow_statement": [],
        }
        if main_model in {"bank", "insurance", "securities"}:
            return empty_rows, {}

        loaders = {
            "balance_sheet": lambda: self.data_client.get_stock_balance_sheets(
                code,
                max_reports=8,
            ),
            "income_statement": lambda: self.data_client.get_stock_income_statements(
                code,
                max_reports=8,
            ),
            "cash_flow_statement": lambda: self.data_client.get_stock_cash_flow_statements(
                code,
                max_reports=8,
            ),
        }
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(loader): key for key, loader in loaders.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    empty_rows[key] = future.result()
                except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
                    errors[key] = str(exc)
        return empty_rows, errors

    def _analyze_reit(
        self,
        profile: ReitProfile,
        *,
        start: date,
        end: date,
        retrieved_at: datetime,
    ) -> dict[str, Any]:
        loaders = {
            "exchange_price": lambda: self.data_client.get_reit_price_history(
                profile.fund_code,
                start=start,
                end=end,
            ),
            "financials": lambda: self.data_client.get_reit_financials(
                profile.fund_code
            ),
            "distributions": lambda: self.data_client.get_reit_distributions(
                profile.fund_code
            ),
            "periodic_reports": lambda: self.data_client.get_reit_notices(
                profile.fund_code
            ),
        }
        loaded: dict[str, list[Any]] = {key: [] for key in loaders}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(loader): key for key, loader in loaders.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    loaded[key] = future.result()
                except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
                    errors[key] = str(exc)

        prices: list[ReitPriceBar] = sorted(
            (item for item in loaded["exchange_price"] if item.date <= end),
            key=lambda item: item.date,
        )
        financials: list[ReitFinancialSnapshot] = sorted(
            (item for item in loaded["financials"] if item.report_date <= end),
            key=lambda item: item.report_date,
        )
        distributions: list[ReitDistribution] = loaded["distributions"]
        notices: list[ReitPeriodicReportNotice] = loaded["periodic_reports"]
        research = build_reit_research_context(
            analysis_as_of=end,
            profile=profile,
            prices=prices,
            financials=financials,
            distributions=distributions,
            notices=notices,
            errors=errors,
            retrieved_at=retrieved_at,
        )
        latest_price = prices[-1] if prices else None
        price_values = [item.close for item in prices]
        total_return = (
            simple_return(prices[0].close, prices[-1].close) if prices else None
        )
        annualized = (
            annualized_return(
                prices[0].close,
                prices[-1].close,
                prices[0].date,
                prices[-1].date,
            )
            if prices
            else None
        )
        latest_reported_nav = next(
            (
                item.period_end_unit_nav_cny
                for item in reversed(financials)
                if item.point_in_time_eligible
                and item.notice_date is not None
                and item.notice_date <= end
            ),
            None,
        )
        analysis_date = latest_price.date if latest_price else end
        drawdown = max_drawdown(price_values)
        result = {
            "asset_type": "fund",
            "code": profile.fund_code,
            "name": profile.fund_name,
            "data_source": "reit_exchange_and_disclosures",
            "as_of": analysis_date.isoformat(),
            "latest_price": latest_price.close if latest_price else None,
            "latest_reported_unit_nav": latest_reported_nav,
            "valuation": {
                "as_of": analysis_date.isoformat(),
                "status": "research_only",
                "method": "reit_basic_research_only",
                "profile": "reit_basic",
                "profile_name": "REIT basic research (not scored)",
                "score": None,
                "level": "unknown",
                "level_zh": "未评分",
                "confidence": 0.0,
                "confidence_label": "未评分",
                "missing_factors": [
                    "affo",
                    "occupancy",
                    "rent_growth",
                    "underlying_asset_leverage",
                ],
                "required_future_data": [
                    "point-in-time AFFO or distributable cash flow",
                    "occupancy and rent growth",
                    "underlying asset debt and leverage",
                ],
            },
            "performance": {
                "sample_size": len(prices),
                "total_return": total_return,
                "annualized_return": annualized,
                "max_drawdown": drawdown,
                "total_return_text": format_pct(total_return),
                "annualized_return_text": format_pct(annualized),
                "max_drawdown_text": format_pct(drawdown),
            },
            "research": research,
            "notes": [
                "REIT output is research-only and is not connected to production scoring.",
                "Exchange price is not replaced by sparse fund NAV history.",
                "Reported unit NAV is only exposed with a matched periodic-report notice date.",
                "AFFO, occupancy, rent growth, and underlying leverage remain unavailable.",
                "This is a research summary, not investment advice.",
            ],
        }
        fallback_reasons = [
            "reit_production_model_unavailable",
            *(f"source_error:{key}" for key in sorted(errors)),
        ]
        result["assessment"] = build_unavailable_assessment(
            profile="reit_basic",
            analysis_as_of=analysis_date,
            retrieved_at=retrieved_at,
            source_as_of=latest_price.date if latest_price else None,
            sources=[
                {
                    "key": key,
                    "source": "eastmoney_reit_data",
                    "status": (
                        "error"
                        if key in errors
                        else "available"
                        if loaded[key]
                        else "unavailable"
                    ),
                    "source_as_of": (
                        latest_price.date.isoformat()
                        if key == "exchange_price" and latest_price
                        else None
                    ),
                    "retrieved_at": retrieved_at.isoformat(),
                    "reason": errors.get(key),
                }
                for key in loaders
            ],
            warnings=[
                "REIT production valuation model is not available.",
                *(f"{key}: {message}" for key, message in sorted(errors.items())),
            ],
            fallback_reasons=fallback_reasons,
            routing=research.get("route"),
        )
        return result

    def _load_official_index_valuation(
        self,
        route: FundHoldingsRoute | None,
        *,
        fund_name: str | None,
        analysis_end: date,
    ) -> tuple[FundIndexDataRoute, dict[str, Any] | None]:
        index_data_route = self._load_fund_index_data_route(
            route,
            analysis_end=analysis_end,
        )
        valuation = (
            analyze_csi_index_valuation(
                index_data_route,
                fund_name=fund_name,
                analysis_end=analysis_end,
            )
            if index_data_route.scope != "unavailable"
            else None
        )
        return index_data_route, valuation

    def _load_fund_index_data_route(
        self,
        route: FundHoldingsRoute | None,
        *,
        analysis_end: date,
    ) -> FundIndexDataRoute:
        tracking = route.tracking if route else None
        if tracking is None:
            return unavailable_fund_index_data_route(
                None,
                "fund_tracking_relationship_unavailable",
            )
        if not tracking.index_code or not tracking.index_name:
            return unavailable_fund_index_data_route(
                tracking,
                "tracked_index_identity_incomplete",
            )

        try:
            valuation_points = self.data_client.get_csi_index_valuation_history(
                tracking.index_code
            )
        except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
            return unavailable_fund_index_data_route(
                tracking,
                f"official_index_valuation_unavailable: {exc}",
            )
        try:
            constituent_weights = self.data_client.get_csi_index_full_weights(
                tracking.index_code
            )
        except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
            return unavailable_fund_index_data_route(
                tracking,
                f"official_index_complete_weights_unavailable: {exc}",
            )
        try:
            return build_csi_fund_index_data_route(
                tracking,
                valuation_points,
                constituent_weights,
                analysis_end=analysis_end,
            )
        except ValueError as exc:
            return unavailable_fund_index_data_route(
                tracking,
                f"official_index_route_rejected: {exc}",
            )

    def _load_tracked_index_history(
        self,
        route: FundHoldingsRoute | None,
        *,
        start: date,
        end: date,
        trace: FallbackTrace,
        parent_budget: StageBudget,
    ) -> tuple[AssetSearchResult | None, list[StockBar], str]:
        index_code = route.tracking.index_code if route and route.tracking else None
        if not index_code:
            for step_key in (
                "target_etf_nav_history",
                "sina_index_price_history",
                "eastmoney_index_price_history",
            ):
                trace.record(
                    step_key,
                    "skipped",
                    reason="tracked_index_not_applicable",
                )
            return None, [], "unavailable"
        try:
            candidates = self.stage_executor.run_remaining(
                parent_budget,
                lambda: self.data_client.search_assets(
                    index_code,
                    limit=5,
                    include_indexes=True,
                ),
            )
        except StageTimeoutError as exc:
            for step_key in (
                "target_etf_nav_history",
                "sina_index_price_history",
                "eastmoney_index_price_history",
            ):
                record_stage_timeout(trace, step_key, exc)
            return None, [], "unavailable"
        except (EastmoneyError, KeyError, TypeError, ValueError):
            candidates = []
        candidate = next(
            (
                item
                for item in candidates
                if item.asset_type == "index" and item.code == index_code
            ),
            None,
        )
        target_etf_code = (
            route.tracking.target_etf_code if route and route.tracking else None
        )
        if target_etf_code:
            try:
                target_nav = self.stage_executor.run(
                    "index",
                    "target_etf_nav_history",
                    lambda: self.data_client.get_fund_nav(
                        target_etf_code,
                        start=start,
                        end=end,
                    ),
                    parent=parent_budget,
                )
            except StageTimeoutError as exc:
                target_nav = []
                record_stage_timeout(
                    trace,
                    "target_etf_nav_history",
                    exc,
                )
            except (EastmoneyError, KeyError, TypeError, ValueError):
                target_nav = []
                trace.record(
                    "target_etf_nav_history",
                    "unavailable",
                    reason="target_etf_nav_history_unavailable",
                )
            target_bars = fund_nav_points_as_bars(target_nav)
            if target_bars:
                trace.record(
                    "target_etf_nav_history",
                    "available",
                )
                return candidate, target_bars, "target_etf_nav_history"
            if not trace.was_recorded("target_etf_nav_history"):
                trace.record(
                    "target_etf_nav_history",
                    "unavailable",
                    reason="target_etf_nav_history_empty",
                )
        else:
            trace.record(
                "target_etf_nav_history",
                "skipped",
                reason="target_etf_not_applicable",
            )

        if candidate and candidate.quote_id:
            try:
                bars = self.stage_executor.run(
                    "index",
                    "sina_index_price_history",
                    lambda: self.data_client.get_sina_index_history(
                        index_code,
                        candidate.quote_id,
                        start=start,
                        end=end,
                    ),
                    parent=parent_budget,
                )
            except StageTimeoutError as exc:
                bars = []
                record_stage_timeout(
                    trace,
                    "sina_index_price_history",
                    exc,
                )
            except (EastmoneyError, KeyError, TypeError, ValueError):
                bars = []
                trace.record(
                    "sina_index_price_history",
                    "unavailable",
                    reason="sina_index_price_history_unavailable",
                )
            if bars:
                trace.record(
                    "sina_index_price_history",
                    "available",
                )
                trace.record(
                    "eastmoney_index_price_history",
                    "skipped",
                    reason="sina_index_price_history_selected",
                )
                return candidate, bars, "sina_index_price_history"
            if not trace.was_recorded("sina_index_price_history"):
                trace.record(
                    "sina_index_price_history",
                    "unavailable",
                    reason="sina_index_price_history_empty",
                )
            try:
                bars = self.stage_executor.run(
                    "index",
                    "eastmoney_index_price_history",
                    lambda: self.data_client.get_index_history(
                        candidate.quote_id,
                        start=start,
                        end=end,
                    ),
                    parent=parent_budget,
                )
            except StageTimeoutError as exc:
                bars = []
                record_stage_timeout(
                    trace,
                    "eastmoney_index_price_history",
                    exc,
                )
            except (EastmoneyError, KeyError, TypeError, ValueError):
                bars = []
                trace.record(
                    "eastmoney_index_price_history",
                    "unavailable",
                    reason="eastmoney_index_price_history_unavailable",
                )
            if bars:
                trace.record(
                    "eastmoney_index_price_history",
                    "available",
                )
                return candidate, bars, "tracked_index_price_history"
            if not trace.was_recorded("eastmoney_index_price_history"):
                trace.record(
                    "eastmoney_index_price_history",
                    "unavailable",
                    reason="eastmoney_index_price_history_empty",
                )
        else:
            trace.record(
                "sina_index_price_history",
                "skipped",
                reason="tracked_index_quote_unavailable",
            )
            trace.record(
                "eastmoney_index_price_history",
                "skipped",
                reason="tracked_index_quote_unavailable",
            )
        return candidate, [], "unavailable"

    def _analyze_fund_holdings(
        self,
        holdings: list[FundHolding],
        end: date,
    ) -> dict[str, dict[str, Any]]:
        supported = sorted(
            (item for item in holdings if is_supported_holding_stock(item.code)),
            key=lambda item: (-(item.weight_pct or 0.0), item.rank),
        )[:MAX_FUND_HOLDING_ANALYSES]
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


def consume_lkg_events(data_client: Any) -> list[dict[str, Any]]:
    consume = getattr(data_client, "consume_lkg_events", None)
    if not callable(consume):
        return []
    events = consume()
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isfinite(value)
    )


def stable_reason_codes(reasons: Any) -> list[str]:
    if not isinstance(reasons, list | tuple):
        return []
    return list(
        dict.fromkeys(
            code
            for reason in reasons
            if (code := stable_reason_code(str(reason)))
        )
    )


def first_stable_reason(reasons: Any, *, default: str) -> str:
    normalized = stable_reason_codes(reasons)
    return normalized[0] if normalized else default


def record_stage_timeout(
    trace: FallbackTrace,
    step_key: str,
    exc: StageTimeoutError,
) -> None:
    trace.record(
        step_key,
        "unavailable",
        reason=exc.reason_code,
        details={
            "deadline_enforced": True,
            "limiting_matrix": exc.budget.matrix_key,
            "limiting_step": exc.budget.step_key,
            "timeout_seconds": exc.budget.timeout_seconds,
        },
    )


def append_stage_timeout_reasons(
    result: dict[str, Any],
    *traces: FallbackTrace,
) -> None:
    diagnostics = []
    for trace in traces:
        serialized = trace.serialize()
        for step in serialized["steps"]:
            reason = step.get("reason")
            if not isinstance(reason, str) or not reason.endswith("_timeout"):
                continue
            details = step.get("details") or {}
            diagnostics.append(
                {
                    "matrix": trace.matrix.key,
                    "step": step["key"],
                    "reason": reason,
                    "timeout_budget_seconds": step["timeout_budget_seconds"],
                    "limiting_matrix": details.get("limiting_matrix"),
                    "limiting_step": details.get("limiting_step"),
                    "limiting_timeout_seconds": details.get("timeout_seconds"),
                }
            )
    if not diagnostics:
        return
    append_assessment_fallback_reasons(
        result,
        [item["reason"] for item in diagnostics],
    )
    assessment = result.get("assessment")
    if not isinstance(assessment, dict):
        return
    data_quality = assessment.setdefault("data_quality", {})
    data_quality["stage_timeouts"] = diagnostics


def append_assessment_fallback_reasons(
    result: dict[str, Any],
    reasons: list[str],
) -> None:
    assessment = result.get("assessment")
    if not isinstance(assessment, dict):
        return
    normalized = stable_reason_codes(reasons)
    existing = stable_reason_codes(assessment.get("fallback_reasons"))
    assessment["fallback_reasons"] = list(dict.fromkeys([*existing, *normalized]))
    confidence_detail = assessment.get("confidence_detail")
    if isinstance(confidence_detail, dict):
        existing_detail = stable_reason_codes(confidence_detail.get("reasons"))
        confidence_detail["reasons"] = list(
            dict.fromkeys([*existing_detail, *normalized])
        )


def append_optional_source_diagnostic(
    result: dict[str, Any],
    *,
    key: str,
    source: str,
    reason: str,
    retrieved_at: datetime,
) -> None:
    assessment = result.get("assessment")
    if not isinstance(assessment, dict):
        return
    data_quality = assessment.setdefault("data_quality", {})
    sources = data_quality.setdefault("sources", [])
    reason_code = stable_reason_code(reason) or "optional_source_unavailable"
    sources.append(
        {
            "key": key,
            "source": source,
            "status": "unavailable",
            "source_as_of": None,
            "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
            "reason": reason_code,
        }
    )
    warnings = data_quality.setdefault("warnings", [])
    warning = f"Optional source '{key}' is unavailable ({reason_code})."
    if warning not in warnings:
        warnings.append(warning)


def mark_index_trace_not_applicable(trace: FallbackTrace) -> None:
    for step in trace.matrix.steps:
        trace.record(
            step.key,
            "skipped",
            reason="tracked_index_not_applicable",
        )
    trace.finish(terminal_reason="tracked_index_not_applicable")


def mark_fund_steps_skipped_for_reit(trace: FallbackTrace) -> None:
    for step in trace.matrix.steps:
        if step.key == "fund_terminal":
            continue
        trace.record(
            step.key,
            "skipped",
            reason="reit_product_selected",
        )


def build_terminal_market_result(
    *,
    asset_type: str,
    code: str,
    name: str | None = None,
    profile: str | None = None,
    analysis_as_of: date,
    retrieved_at: datetime,
    fallback_reasons: list[str],
) -> dict[str, Any]:
    reasons = stable_reason_codes(fallback_reasons)
    warning = "No verified market dataset can support a numeric valuation."
    result: dict[str, Any] = {
        "asset_type": asset_type,
        "code": code,
        "name": name,
        "as_of": analysis_as_of.isoformat(),
        "valuation": {
            "status": "unavailable",
            "method": "unavailable",
            "score": None,
            "confidence": 0.0,
            "missing_factors": [],
        },
        "performance": {
            "sample_size": 0,
            "total_return": None,
            "annualized_return": None,
            "max_drawdown": None,
            "total_return_text": "N/A",
            "annualized_return_text": "N/A",
            "max_drawdown_text": "N/A",
        },
        "notes": [
            warning,
            "This is a research summary, not investment advice.",
        ],
    }
    if asset_type == "stock":
        result["latest_price"] = None
        resolved_profile = profile or "stock"
    else:
        result.update(
            {
                "data_source": "unavailable",
                "holdings_route": serialize_holdings_route(
                    None,
                    fallback_reasons=reasons,
                ),
                "latest_unit_nav": None,
                "latest_cumulative_nav": None,
            }
        )
        result["valuation"]["holdings_route"] = result["holdings_route"]
        resolved_profile = profile or "fund"
    result["assessment"] = build_unavailable_assessment(
        profile=resolved_profile,
        analysis_as_of=analysis_as_of,
        retrieved_at=retrieved_at,
        sources=[
            {
                "key": f"{asset_type}_primary_market_data",
                "source": "deterministic_fallback_matrix",
                "status": "unavailable",
                "source_as_of": None,
                "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
                "reasons": reasons,
            }
        ],
        warnings=[warning],
        fallback_reasons=reasons,
    )
    return result


def apply_last_known_good_diagnostics(
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    if not events:
        return
    snapshots = [dict(event) for event in events]
    result["last_known_good"] = {
        "used": True,
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
    }
    notes = result.setdefault("notes", [])
    note = (
        "One or more critical market datasets use validated last-known-good snapshots; "
        "see assessment.data_quality for source dates and snapshot ages."
    )
    if note not in notes:
        notes.insert(0, note)

    assessment = result.get("assessment")
    if not isinstance(assessment, dict):
        return
    fallback_reasons = assessment.setdefault("fallback_reasons", [])
    if "last_known_good_snapshot" not in fallback_reasons:
        fallback_reasons.append("last_known_good_snapshot")

    data_quality = assessment.setdefault("data_quality", {})
    sources = data_quality.setdefault("sources", [])
    warnings = data_quality.setdefault("warnings", [])
    for event in snapshots:
        sources.append(
            {
                "key": event.get("dataset"),
                "source": event.get("source"),
                "status": "last_known_good",
                "source_as_of": event.get("source_as_of"),
                "retrieved_at": event.get("snapshot_retrieved_at"),
                "snapshot_age_seconds": event.get("snapshot_age_seconds"),
                "row_count": event.get("row_count"),
                "payload_sha256": event.get("payload_sha256"),
                "validator_version": event.get("validator_version"),
                "request_identity": event.get("identity"),
                "reason": event.get("fallback_reason"),
            }
        )
    warning = (
        "Validated last-known-good market data was used because the live source was "
        "unavailable or invalid; the snapshot is not current live data."
    )
    if warning not in warnings:
        warnings.append(warning)

    valuation = (assessment.get("dimensions") or {}).get("valuation") or {}
    score = valuation.get("score")
    if isinstance(score, int | float) and not isinstance(score, bool) and isfinite(score):
        assessment["status"] = "degraded"
        assessment["method"] = "last_known_good"


def preserve_fund_valuation_context(
    target: dict[str, Any],
    previous: dict[str, Any],
    holdings_route: dict[str, Any],
) -> None:
    target["holdings_route"] = holdings_route
    target["product_data"] = previous.get("product_data")
    for key in ("portfolio", "holdings"):
        if previous.get(key) is not None:
            target[key] = previous[key]


def official_index_valuation_notes(notes: list[str]) -> list[str]:
    obsolete = (
        "Holding-level valuation uses the selected audited holdings snapshot "
        "and its reported weights."
    )
    quality_warning = "Low analyzed equity coverage or an old report date lowers confidence."
    return [
        (
            "Low analyzed equity coverage or an old report date lowers only the separate "
            "underlying-quality confidence."
            if note == quality_warning
            else note
        )
        for note in notes
        if note != obsolete
    ]


def is_supported_holding_stock(code: str) -> bool:
    try:
        return is_a_share_symbol(code)
    except ValueError:
        return False


def fund_nav_points_as_bars(points: list[FundNavPoint]) -> list[StockBar]:
    bars: list[StockBar] = []
    for point in points:
        value = point.unit_nav
        if value is None or value <= 0:
            continue
        bars.append(
            StockBar(
                date=point.date,
                open=value,
                close=value,
                high=value,
                low=value,
                volume=0.0,
                amount=0.0,
                amplitude_pct=None,
                change_pct=point.daily_growth_pct,
                change_amount=None,
                turnover_pct=None,
            )
        )
    return bars


def serialize_holdings_route(
    route: FundHoldingsRoute | None,
    *,
    fallback_reasons: list[str] | None = None,
) -> dict[str, Any]:
    route_reasons = list(route.fallback_reasons) if route else []
    normalized_reasons = stable_reason_codes(
        [*route_reasons, *(fallback_reasons or [])]
    )
    if route is None:
        return {
            "source": "unavailable",
            "scope": "unavailable",
            "as_of": None,
            "coverage": 0.0,
            "fallback_reasons": normalized_reasons,
            "fund_type": None,
            "tracked_index_code": None,
            "tracked_index_name": None,
            "target_etf_code": None,
            "target_etf_name": None,
            "equity_allocation_pct": None,
            "nav_equity_exposure": None,
            "unexplained_equity_weight_pct": None,
            "latest_top10": None,
            "full_disclosure": None,
        }
    tracking = route.tracking
    return {
        "source": route.source,
        "scope": route.scope,
        "as_of": route.as_of.isoformat() if route.as_of else None,
        "coverage": route.coverage,
        "fallback_reasons": normalized_reasons,
        "fund_type": tracking.fund_type if tracking else None,
        "tracked_index_code": tracking.index_code if tracking else None,
        "tracked_index_name": tracking.index_name if tracking else None,
        "target_etf_code": tracking.target_etf_code if tracking else None,
        "target_etf_name": tracking.target_etf_name if tracking else None,
        "equity_allocation_pct": route.equity_allocation_pct,
        "nav_equity_exposure": route.nav_equity_exposure,
        "unexplained_equity_weight_pct": route.unexplained_equity_weight_pct,
        "latest_top10": serialize_holdings_snapshot(route.latest_top10),
        "full_disclosure": serialize_holdings_snapshot(route.full_disclosure),
    }


def serialize_holdings_snapshot(
    snapshot: FundHoldingsSnapshot | None,
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "source": snapshot.source,
        "scope": snapshot.scope,
        "as_of": snapshot.as_of.isoformat(),
        "count": len(snapshot.holdings),
        "total_nav_weight_pct": snapshot.total_nav_weight_pct,
        "equity_allocation_pct": snapshot.equity_allocation_pct,
        "equity_coverage": snapshot.equity_coverage,
        "unexplained_equity_weight_pct": snapshot.unexplained_equity_weight_pct,
    }


def apply_holdings_route_method(
    valuation: dict[str, Any],
    route: FundHoldingsRoute | None,
) -> None:
    if route is None:
        return
    if route.scope in {"tracked_index_top10", "tracked_index_full_weights"}:
        valuation.update(
            {
                "method": "index_constituents_weighted_multi_factor",
                "profile": "index_fund",
                "profile_name": "指数基金",
            }
        )
    elif route.scope in {"target_etf_top10", "target_etf_full_disclosure"}:
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
    if route.scope == "tracked_index_full_weights":
        return (
            "Underlying holdings use complete official monthly weights for the tracked "
            f"CSI index, dated {route.as_of.isoformat() if route.as_of else 'unknown'}."
        )
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
    if route.scope == "target_etf_full_disclosure":
        return (
            "Underlying holdings use the target ETF's latest complete annual or semiannual "
            "stock disclosure; the latest quarterly top ten is retained separately."
        )
    if route.scope == "fund_full_disclosure":
        return (
            "Holding-level analysis uses the fund's latest complete annual or semiannual "
            "stock disclosure and same-date equity allocation."
        )
    if route.scope == "unresolved_index_fund":
        return (
            "The tracked index relationship could not be resolved, so direct fund holdings "
            "were not used as a substitute."
        )
    return "Valuation uses the fund's latest disclosed direct stock holdings."
