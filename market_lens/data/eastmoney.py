from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from math import isfinite
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from market_lens.config import settings
from market_lens.storage.sqlite_cache import SQLiteCache
from market_lens.types import (
    AssetSearchResult,
    AssetType,
    CommodityFuturesBar,
    CommodityHistoryPeriod,
    CommodityMainContractKey,
    CommodityMainContractSpec,
    FundHolding,
    FundHoldingsRoute,
    FundNavPoint,
    FundProductInfo,
    FundTrackingInfo,
    ReitDistribution,
    ReitFinancialSnapshot,
    ReitHistoryPeriod,
    ReitPeriodicReportNotice,
    ReitPriceBar,
    ReitProfile,
    ReitReportKind,
    StockBalanceSheet,
    StockBar,
    StockCashFlowStatement,
    StockDividendPlan,
    StockDividendSummary,
    StockFinancialCompanyType,
    StockFinancialIndicator,
    StockFinancialReportScope,
    StockIncomeStatement,
    StockIndustryValuationSnapshot,
    StockPeerComparison,
    StockProfile,
    StockValuationPoint,
)


class EastmoneyError(RuntimeError):
    pass


SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
KNOWN_FUND_MANAGERS = (
    "易方达",
    "华夏",
    "广发",
    "南方",
    "富国",
    "招商",
    "嘉实",
    "博时",
    "鹏华",
    "汇添富",
    "工银瑞信",
    "工银",
    "华泰柏瑞",
    "国泰",
    "天弘",
    "景顺长城",
    "景顺",
    "大成",
    "永赢",
    "长城",
    "泰康",
)

F10_FINANCIAL_COMPANY_TYPES: dict[StockFinancialCompanyType, int] = {
    "securities": 1,
    "insurance": 2,
    "bank": 3,
    "general": 4,
}
F10_FINANCIAL_REPORT_SCOPES: dict[StockFinancialReportScope, int] = {
    "all": 0,
    "annual": 1,
}
F10_FINANCIAL_STATEMENT_TABS = {
    "balance_sheet": "zcfzb",
    "income_statement": "lrb",
    "cash_flow_statement": "xjllb",
}
F10_FINANCIAL_DATES_PER_REQUEST = 5
REIT_FINANCIAL_FIELDS = (
    "FSRQ",
    "COMPROFIT",
    "NETPROFIT",
    "UNITPROFIT",
    "NGROWTH",
    "FNGROWTH",
    "DISPROFIT",
    "DIFUNTIPROFIT",
    "ENDNAV",
    "ENDUNITNAV",
    "FCNGROWTH",
)
REIT_PERIODIC_REPORT_PATTERN = re.compile(
    r"(20\d{2})年(年度|中期|半年度|第([一二三四1234])季度)报告$"
)
REIT_QUARTER_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4}

COMMODITY_MAIN_CONTRACTS: dict[CommodityMainContractKey, CommodityMainContractSpec] = {
    "copper": CommodityMainContractSpec(
        key="copper",
        product_code="CU",
        name_zh="沪铜主连",
        exchange="SHFE",
        quote_id="113.cum",
        source_code="cum",
        source_market=113,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=5.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "aluminum": CommodityMainContractSpec(
        key="aluminum",
        product_code="AL",
        name_zh="沪铝主连",
        exchange="SHFE",
        quote_id="113.alm",
        source_code="alm",
        source_market=113,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=5.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "gold": CommodityMainContractSpec(
        key="gold",
        product_code="AU",
        name_zh="沪金主连",
        exchange="SHFE",
        quote_id="113.aum",
        source_code="aum",
        source_market=113,
        currency="CNY",
        price_unit="CNY/gram",
        contract_multiplier=1000.0,
        contract_multiplier_unit="gram/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "rebar": CommodityMainContractSpec(
        key="rebar",
        product_code="RB",
        name_zh="螺纹钢主连",
        exchange="SHFE",
        quote_id="113.rbm",
        source_code="rbm",
        source_market=113,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=10.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "hot_rolled_coil": CommodityMainContractSpec(
        key="hot_rolled_coil",
        product_code="HC",
        name_zh="热卷主连",
        exchange="SHFE",
        quote_id="113.hcm",
        source_code="hcm",
        source_market=113,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=10.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "iron_ore": CommodityMainContractSpec(
        key="iron_ore",
        product_code="I",
        name_zh="铁矿石主连",
        exchange="DCE",
        quote_id="114.im",
        source_code="im",
        source_market=114,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=100.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "coking_coal": CommodityMainContractSpec(
        key="coking_coal",
        product_code="JM",
        name_zh="焦煤主连",
        exchange="DCE",
        quote_id="114.jmm",
        source_code="jmm",
        source_market=114,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=60.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
    "coke": CommodityMainContractSpec(
        key="coke",
        product_code="J",
        name_zh="焦炭主连",
        exchange="DCE",
        quote_id="114.jm",
        source_code="jm",
        source_market=114,
        currency="CNY",
        price_unit="CNY/metric_ton",
        contract_multiplier=100.0,
        contract_multiplier_unit="metric_ton/lot",
        series_kind="main_continuous",
        roll_method="eastmoney_provider_defined_main_contract",
        price_adjustment="none",
        source="eastmoney_push2his",
    ),
}


class EastmoneyClient:
    def __init__(self, cache: SQLiteCache | None = None) -> None:
        self.cache = cache or SQLiteCache(settings.db_path)
        self.timeout = settings.http_timeout
        self.retries = settings.http_retries
        self.headers = {
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "close",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }

    def get_stock_history(
        self,
        symbol: str,
        start: date,
        end: date,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> list[StockBar]:
        period_map = {"daily": "101", "weekly": "102", "monthly": "103"}
        adjust_map = {"none": "0", "qfq": "1", "hfq": "2"}
        if period not in period_map:
            raise ValueError("period must be one of: daily, weekly, monthly")
        if adjust not in adjust_map:
            raise ValueError("adjust must be one of: none, qfq, hfq")

        params = {
            "secid": infer_secid(symbol),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": period_map[period],
            "fqt": adjust_map[adjust],
            "beg": compact_date(start),
            "end": compact_date(end),
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
        payload = self._get_json(url, ttl_seconds=12 * 60 * 60)
        data = payload.get("data")
        if not data:
            return []
        klines = data.get("klines") or []
        return [parse_stock_kline(item) for item in klines]

    def get_commodity_main_contract_history(
        self,
        commodity: CommodityMainContractKey,
        start: date,
        end: date,
        period: CommodityHistoryPeriod = "daily",
    ) -> list[CommodityFuturesBar]:
        spec = commodity_main_contract_spec(commodity)
        period_map = {"daily": "101", "weekly": "102", "monthly": "103"}
        if period not in period_map:
            raise ValueError("period must be one of: daily, weekly, monthly")
        if start > end:
            raise ValueError("start must be on or before end")

        params = {
            "secid": spec.quote_id,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": period_map[period],
            "fqt": "0",
            "beg": compact_date(start),
            "end": compact_date(end),
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(
            params
        )
        payload = self._get_json(url, ttl_seconds=12 * 60 * 60)
        rows = validated_commodity_kline_rows(payload, spec, period=period)
        return [row for row in rows if start <= row.date <= end]

    def get_stock_valuation(self, symbol: str) -> list[StockValuationPoint]:
        stock_code = normalize_symbol(symbol)
        filter_value = quote(f'(SECURITY_CODE="{stock_code}")', safe="()=")
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "5000",
            "pageNumber": "1",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "ALL",
            "quoteColumns": "",
            "source": "WEB",
            "client": "WEB",
        }
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get?"
            + urlencode(params)
            + f"&filter={filter_value}"
        )
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        result = payload.get("result") or {}
        rows = result.get("data") or []
        points = [parse_stock_valuation_row(row) for row in rows]
        return sorted(points, key=lambda item: item.date)

    def get_stock_industry_valuation_snapshot(
        self,
        board_code: str,
        trade_date: date,
        board_name: str | None = None,
        page_size: int = 500,
    ) -> StockIndustryValuationSnapshot:
        normalized_board_code = str(board_code).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]+", normalized_board_code):
            raise ValueError(f"Invalid industry board code: {board_code!r}")
        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")

        first_payload = self._get_stock_industry_valuation_page(
            normalized_board_code,
            trade_date,
            page=1,
            page_size=page_size,
        )
        result = first_payload.get("result") or {}
        raw_rows = list(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        if pages > 20:
            raise EastmoneyError(
                f"Unexpected industry valuation pagination for {normalized_board_code}: {pages}"
            )
        for page in range(2, pages + 1):
            payload = self._get_stock_industry_valuation_page(
                normalized_board_code,
                trade_date,
                page=page,
                page_size=page_size,
            )
            raw_rows.extend((payload.get("result") or {}).get("data") or [])

        rows = [parse_stock_valuation_row(row) for row in raw_rows]
        mismatched_rows = [
            row
            for row in rows
            if row.board_code != normalized_board_code or row.date != trade_date
        ]
        if mismatched_rows:
            raise EastmoneyError(
                "Industry valuation response did not match the requested board and trade date"
            )

        unique_rows = {row.code: row for row in rows if row.code}
        sorted_rows = tuple(unique_rows[code] for code in sorted(unique_rows))
        first_row = sorted_rows[0] if sorted_rows else None
        resolved_board_name = board_name or (first_row.board_name if first_row else None)
        original_board_code = first_row.original_board_code if first_row else None
        return StockIndustryValuationSnapshot(
            date=trade_date,
            board_code=normalized_board_code,
            board_name=resolved_board_name,
            original_board_code=original_board_code,
            rows=sorted_rows,
        )

    def _get_stock_industry_valuation_page(
        self,
        board_code: str,
        trade_date: date,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        filter_value = quote(
            f'(BOARD_CODE="{board_code}")(TRADE_DATE=\'{trade_date.isoformat()}\')',
            safe="()='",
        )
        params = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": str(page_size),
            "pageNumber": str(page),
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "ALL",
            "quoteColumns": "",
            "source": "WEB",
            "client": "WEB",
        }
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get?"
            + urlencode(params)
            + f"&filter={filter_value}"
        )
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        if payload.get("success") is not True or not isinstance(payload.get("result"), dict):
            raise EastmoneyError(
                f"Unexpected industry valuation response for {board_code} on {trade_date}"
            )
        return payload

    def get_stock_profile(self, symbol: str) -> StockProfile | None:
        code = f10_stock_code(symbol)
        url = (
            "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?"
            + urlencode({"code": code})
        )
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        rows = payload.get("jbzl") or []
        if not rows:
            return None
        return parse_stock_profile(rows[0])

    def get_stock_financial_indicators(
        self,
        symbol: str,
        report_type: int = 1,
    ) -> list[StockFinancialIndicator]:
        code = f10_stock_code(symbol)
        url = (
            "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?"
            + urlencode({"type": str(report_type), "code": code})
        )
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        rows = payload.get("data") or []
        return sorted(
            (parse_stock_financial_indicator(row) for row in rows),
            key=lambda item: item.date,
        )

    def get_stock_balance_sheets(
        self,
        symbol: str,
        report_scope: StockFinancialReportScope = "annual",
        company_type: StockFinancialCompanyType = "general",
        max_reports: int | None = 20,
    ) -> list[StockBalanceSheet]:
        rows = self._get_stock_financial_statement_rows(
            symbol,
            statement="balance_sheet",
            report_scope=report_scope,
            company_type=company_type,
            max_reports=max_reports,
        )
        return sorted(
            (parse_stock_balance_sheet(row) for row in rows),
            key=lambda item: item.report_date,
        )

    def get_stock_income_statements(
        self,
        symbol: str,
        report_scope: StockFinancialReportScope = "annual",
        company_type: StockFinancialCompanyType = "general",
        max_reports: int | None = 20,
    ) -> list[StockIncomeStatement]:
        rows = self._get_stock_financial_statement_rows(
            symbol,
            statement="income_statement",
            report_scope=report_scope,
            company_type=company_type,
            max_reports=max_reports,
        )
        return sorted(
            (parse_stock_income_statement(row) for row in rows),
            key=lambda item: item.report_date,
        )

    def get_stock_cash_flow_statements(
        self,
        symbol: str,
        report_scope: StockFinancialReportScope = "annual",
        company_type: StockFinancialCompanyType = "general",
        max_reports: int | None = 20,
    ) -> list[StockCashFlowStatement]:
        rows = self._get_stock_financial_statement_rows(
            symbol,
            statement="cash_flow_statement",
            report_scope=report_scope,
            company_type=company_type,
            max_reports=max_reports,
        )
        return sorted(
            (parse_stock_cash_flow_statement(row) for row in rows),
            key=lambda item: item.report_date,
        )

    def _get_stock_financial_statement_rows(
        self,
        symbol: str,
        statement: str,
        report_scope: StockFinancialReportScope,
        company_type: StockFinancialCompanyType,
        max_reports: int | None,
    ) -> list[dict[str, Any]]:
        if statement not in F10_FINANCIAL_STATEMENT_TABS:
            raise ValueError(f"Unsupported financial statement: {statement!r}")
        if report_scope not in F10_FINANCIAL_REPORT_SCOPES:
            raise ValueError(f"Unsupported financial report scope: {report_scope!r}")
        if company_type not in F10_FINANCIAL_COMPANY_TYPES:
            raise ValueError(f"Unsupported financial company type: {company_type!r}")
        if max_reports is not None and max_reports <= 0:
            raise ValueError("max_reports must be positive or None")

        code = f10_stock_code(symbol)
        expected_security_code = normalize_symbol(symbol)
        tab = F10_FINANCIAL_STATEMENT_TABS[statement]
        company_type_code = F10_FINANCIAL_COMPANY_TYPES[company_type]
        report_date_type = F10_FINANCIAL_REPORT_SCOPES[report_scope]
        date_params = {
            "companyType": str(company_type_code),
            "reportDateType": str(report_date_type),
            "code": code,
        }
        date_url = (
            f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/{tab}DateAjaxNew?"
            + urlencode(date_params)
        )
        date_payload = self._get_json(date_url, ttl_seconds=24 * 60 * 60)
        date_rows = validated_f10_financial_rows(date_payload, f"{statement} dates")
        report_dates = unique_report_dates(date_rows)
        if max_reports is not None:
            report_dates = report_dates[:max_reports]

        rows: list[dict[str, Any]] = []
        for start in range(0, len(report_dates), F10_FINANCIAL_DATES_PER_REQUEST):
            date_chunk = report_dates[start : start + F10_FINANCIAL_DATES_PER_REQUEST]
            data_params = {
                "companyType": str(company_type_code),
                "reportDateType": str(report_date_type),
                "reportType": "1",
                "dates": ",".join(item.isoformat() for item in date_chunk),
                "code": code,
            }
            data_url = (
                f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/{tab}AjaxNew?"
                + urlencode(data_params)
            )
            payload = self._get_json(data_url, ttl_seconds=24 * 60 * 60)
            statement_rows = validated_f10_financial_rows(payload, statement)
            validate_f10_financial_statement_route(
                statement_rows,
                expected_security_code=expected_security_code,
                requested_dates=set(date_chunk),
                context=statement,
            )
            rows.extend(statement_rows)
        return deduplicate_financial_statement_rows(rows)

    def get_stock_peer_comparison(self, symbol: str) -> dict[str, list[StockPeerComparison]]:
        code = f10_stock_code(symbol)
        url = (
            "https://emweb.securities.eastmoney.com/PC_HSF10/IndustryAnalysis/PageAjax?"
            + urlencode({"code": code})
        )
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        return {
            "valuation": [
                parse_stock_peer_comparison(row)
                for row in (payload.get("gzbj") or [])
                if parse_stock_peer_comparison(row) is not None
            ],
            "growth": [
                parse_stock_peer_comparison(row)
                for row in (payload.get("czxbj") or [])
                if parse_stock_peer_comparison(row) is not None
            ],
            "dupont": [
                parse_stock_peer_comparison(row)
                for row in (payload.get("dbfxbj") or [])
                if parse_stock_peer_comparison(row) is not None
            ],
        }

    def get_stock_dividends(
        self,
        symbol: str,
    ) -> dict[str, list[StockDividendPlan] | list[StockDividendSummary]]:
        code = f10_stock_code(symbol)
        url = (
            "https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/PageAjax?"
            + urlencode({"code": code})
        )
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        return {
            "plans": [parse_stock_dividend_plan(row) for row in (payload.get("fhyx") or [])],
            "summaries": [
                parse_stock_dividend_summary(row) for row in (payload.get("lnfhrz") or [])
            ],
        }

    def get_fund_name(self, code: str) -> str | None:
        normalized_code = normalize_fund_code(code)
        url = f"https://fund.eastmoney.com/pingzhongdata/{normalized_code}.js"
        text = self._get_text(url, ttl_seconds=24 * 60 * 60)
        return parse_pingzhongdata_fund_name(text)

    def get_fund_holdings(self, code: str, top_n: int = 10) -> list[FundHolding]:
        normalized_code = normalize_fund_code(code)
        params = {
            "type": "jjcc",
            "code": normalized_code,
            "topline": str(top_n),
            "year": "",
            "month": "",
        }
        url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx?" + urlencode(params)
        text = self._get_text(url, ttl_seconds=24 * 60 * 60)
        content = parse_fund_archives_content(text)
        return parse_fund_holdings_table(content)[:top_n]

    def get_fund_tracking_info(self, code: str) -> FundTrackingInfo:
        normalized_code = normalize_fund_code(code)
        params = fund_mobile_params(normalized_code)
        detail = parse_fund_tracking_info(self._get_fund_detail_payload(normalized_code))
        if not detail.index_code:
            return detail

        position_url = (
            "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?"
            + urlencode(params)
        )
        try:
            position = parse_fund_position_payload(
                self._get_validated_json(
                    position_url,
                    ttl_seconds=24 * 60 * 60,
                    is_success=lambda payload: payload.get("ErrCode") == 0,
                )
            )
        except EastmoneyError:
            return detail
        return replace(
            detail,
            target_etf_code=position["target_etf_code"],
            target_etf_name=position["target_etf_name"],
        )

    def get_fund_product_info(self, code: str) -> FundProductInfo:
        normalized_code = normalize_fund_code(code)
        return parse_fund_product_info(self._get_fund_detail_payload(normalized_code))

    def get_reit_profile(self, code: str) -> ReitProfile:
        normalized_code = normalize_fund_code(code)
        payload = self._get_fund_detail_payload(normalized_code)
        return parse_reit_profile(payload, expected_code=normalized_code)

    def get_reit_price_history(
        self,
        code: str,
        start: date,
        end: date,
        period: ReitHistoryPeriod = "daily",
    ) -> list[ReitPriceBar]:
        period_map = {"daily": "101", "weekly": "102", "monthly": "103"}
        if period not in period_map:
            raise ValueError("period must be one of: daily, weekly, monthly")
        if start > end:
            raise ValueError("start must be on or before end")

        profile = self.get_reit_profile(code)
        params = {
            "secid": profile.quote_id,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": period_map[period],
            "fqt": "0",
            "beg": compact_date(start),
            "end": compact_date(end),
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(
            params
        )
        payload = self._get_json(url, ttl_seconds=12 * 60 * 60)
        rows = validated_reit_kline_rows(payload, profile, period=period)
        return [row for row in rows if start <= row.date <= end]

    def get_reit_notices(self, code: str) -> list[ReitPeriodicReportNotice]:
        profile = self.get_reit_profile(code)
        rows = self._get_reit_announcement_rows(profile.fund_code, category="3")
        return sorted(
            (parse_reit_periodic_report_notice(row) for row in rows),
            key=lambda item: (item.publish_date, item.announcement_id),
        )

    def get_reit_financials(self, code: str) -> list[ReitFinancialSnapshot]:
        profile = self.get_reit_profile(code)
        normalized_code = profile.fund_code
        base_params = {
            "fundcode": normalized_code,
            "showtype": "0",
            "year": "",
        }
        base_url = "https://api.fund.eastmoney.com/f10/GetArrayCwzb?" + urlencode(
            base_params
        )
        first_payload = self._get_json(base_url, ttl_seconds=24 * 60 * 60)
        first_rows, years, response_year = validated_reit_financial_payload(
            first_payload
        )

        raw_rows = list(first_rows)
        for year in years:
            if year == response_year:
                continue
            params = base_params | {"year": str(year)}
            url = "https://api.fund.eastmoney.com/f10/GetArrayCwzb?" + urlencode(params)
            payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
            rows, returned_years, returned_year = validated_reit_financial_payload(
                payload
            )
            if returned_year != year or returned_years != years:
                raise EastmoneyError(
                    f"Eastmoney REIT financial year route mismatch for {year}"
                )
            raw_rows.extend(rows)

        notices = [
            parse_reit_periodic_report_notice(row)
            for row in self._get_reit_announcement_rows(
                normalized_code,
                category="3",
            )
        ]
        snapshots: list[ReitFinancialSnapshot] = []
        for row in deduplicate_reit_financial_rows(raw_rows):
            report_date = parse_date(str(row["FSRQ"]))
            notice = select_reit_financial_notice(report_date, notices)
            snapshots.append(
                parse_reit_financial_snapshot(
                    normalized_code,
                    row,
                    notice=notice,
                )
            )
        return sorted(snapshots, key=lambda item: item.report_date)

    def get_reit_distributions(self, code: str) -> list[ReitDistribution]:
        profile = self.get_reit_profile(code)
        normalized_code = profile.fund_code
        url = f"https://fundf10.eastmoney.com/fhsp_{normalized_code}.html"
        text = self._get_text(url, ttl_seconds=24 * 60 * 60)
        distributions = parse_reit_distribution_table(normalized_code, text)
        announcement_rows = self._get_reit_announcement_rows(
            normalized_code,
            category="2",
        )
        return match_reit_distribution_announcements(distributions, announcement_rows)

    def _get_reit_announcement_rows(
        self,
        normalized_code: str,
        category: str,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        if category not in {"2", "3"}:
            raise ValueError("REIT announcement category must be 2 or 3")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        def fetch_page(page: int) -> tuple[list[dict[str, Any]], int, int]:
            params = {
                "fundcode": normalized_code,
                "pageIndex": str(page),
                "pageSize": str(page_size),
                "type": category,
            }
            url = "https://api.fund.eastmoney.com/f10/JJGG?" + urlencode(params)
            payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
            return validated_reit_announcement_page(
                payload,
                expected_code=normalized_code,
                expected_category=category,
                expected_page=page,
            )

        first_rows, total_count, response_page_size = fetch_page(1)
        pages = max(1, (total_count + response_page_size - 1) // response_page_size)
        if pages > 100:
            raise EastmoneyError(
                f"Unexpected REIT announcement pagination for {normalized_code}: {pages}"
            )
        rows = list(first_rows)
        for page in range(2, pages + 1):
            page_rows, page_total, page_response_size = fetch_page(page)
            if page_total != total_count or page_response_size != response_page_size:
                raise EastmoneyError("REIT announcement pagination changed during request")
            rows.extend(page_rows)
        return deduplicate_reit_announcement_rows(rows)

    def _get_fund_detail_payload(self, normalized_code: str) -> dict[str, Any]:
        detail_url = (
            "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNDetailInformation?"
            + urlencode(fund_mobile_params(normalized_code))
        )
        return self._get_validated_json(
            detail_url,
            ttl_seconds=24 * 60 * 60,
            is_success=lambda payload: payload.get("ErrCode") == 0,
        )

    def get_csi_index_top_holdings(
        self,
        index_code: str,
        top_n: int = 10,
    ) -> list[FundHolding]:
        normalized_code = str(index_code).strip().upper()
        if not re.fullmatch(r"[A-Z0-9.]+", normalized_code):
            raise ValueError(f"Invalid CSI index code: {index_code!r}")
        url = (
            "https://www.csindex.com.cn/csindex-home/index/weight/top10new/"
            f"{quote(normalized_code)}"
        )
        payload = self._get_validated_json(
            url,
            ttl_seconds=6 * 60 * 60,
            is_success=lambda value: str(value.get("code")) == "200",
        )
        return parse_csi_index_top_holdings(payload)[:top_n]

    def get_fund_holdings_route(
        self,
        code: str,
        top_n: int = 10,
        fund_name: str | None = None,
    ) -> FundHoldingsRoute:
        normalized_code = normalize_fund_code(code)
        fallback_reasons: list[str] = []
        tracking: FundTrackingInfo | None = None
        try:
            tracking = self.get_fund_tracking_info(normalized_code)
        except EastmoneyError as exc:
            fallback_reasons.append(f"tracking_info_unavailable: {exc}")

        resolved_name = fund_name or (tracking.fund_name if tracking else None)
        if (not tracking or not tracking.index_code) and looks_like_index_fund(resolved_name):
            fallback_reasons.append("index_fund_tracking_relationship_unresolved")
            return build_fund_holdings_route(
                [],
                source="unavailable",
                scope="unresolved_index_fund",
                tracking=tracking,
                fallback_reasons=fallback_reasons,
            )

        if tracking and tracking.index_code:
            try:
                holdings = self.get_csi_index_top_holdings(tracking.index_code, top_n=top_n)
            except (ValueError, EastmoneyError) as exc:
                fallback_reasons.append(f"official_index_holdings_unavailable: {exc}")
            else:
                if holdings:
                    return build_fund_holdings_route(
                        holdings,
                        source="csindex_official",
                        scope="tracked_index_top10",
                        tracking=tracking,
                        fallback_reasons=fallback_reasons,
                    )
                fallback_reasons.append("official_index_holdings_empty")

            if tracking.target_etf_code:
                try:
                    holdings = self.get_fund_holdings(
                        tracking.target_etf_code,
                        top_n=top_n,
                    )
                except (ValueError, EastmoneyError) as exc:
                    fallback_reasons.append(f"target_etf_holdings_unavailable: {exc}")
                else:
                    if holdings:
                        return build_fund_holdings_route(
                            holdings,
                            source="eastmoney_fund_disclosure",
                            scope="target_etf_top10",
                            tracking=tracking,
                            fallback_reasons=fallback_reasons,
                        )
                    fallback_reasons.append("target_etf_holdings_empty")

            if looks_like_feeder_fund(resolved_name):
                fallback_reasons.append("feeder_fund_target_etf_holdings_unresolved")
                return build_fund_holdings_route(
                    [],
                    source="unavailable",
                    scope="unresolved_index_fund",
                    tracking=tracking,
                    fallback_reasons=fallback_reasons,
                )

        holdings = self.get_fund_holdings(normalized_code, top_n=top_n)
        return build_fund_holdings_route(
            holdings,
            source="eastmoney_fund_disclosure",
            scope="fund_direct_top10",
            tracking=tracking,
            fallback_reasons=fallback_reasons,
        )

    def get_fund_nav(
        self,
        code: str,
        start: date,
        end: date,
        page_size: int = 200,
    ) -> list[FundNavPoint]:
        first_page = self._get_fund_nav_page(code, start, end, page=1, page_size=page_size)
        pages = int(first_page.get("pages") or 1)
        rows = list(first_page["rows"])
        for page in range(2, pages + 1):
            page_data = self._get_fund_nav_page(code, start, end, page=page, page_size=page_size)
            rows.extend(page_data["rows"])
        return sorted(rows, key=lambda item: item.date)

    def get_exchange_fund_price_nav(
        self,
        code: str,
        start: date,
        end: date,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> list[FundNavPoint]:
        period_map = {"daily": "101", "weekly": "102", "monthly": "103"}
        adjust_map = {"none": "0", "qfq": "1", "hfq": "2"}
        if period not in period_map:
            raise ValueError("period must be one of: daily, weekly, monthly")
        if adjust not in adjust_map:
            raise ValueError("adjust must be one of: none, qfq, hfq")

        secid = infer_exchange_fund_secid(code)
        if secid is None:
            return []

        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": period_map[period],
            "fqt": adjust_map[adjust],
            "beg": compact_date(start),
            "end": compact_date(end),
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
        payload = self._get_json(url, ttl_seconds=12 * 60 * 60)
        data = payload.get("data")
        if not data:
            return []
        klines = data.get("klines") or []
        bars = [parse_stock_kline(item) for item in klines]
        return [
            FundNavPoint(
                date=bar.date,
                unit_nav=bar.close,
                cumulative_nav=bar.close,
                daily_growth_pct=bar.change_pct,
                subscribe_status="场内价格",
                redeem_status="场内价格",
            )
            for bar in bars
        ]

    def get_index_history(
        self,
        quote_id: str,
        start: date,
        end: date,
        period: str = "daily",
    ) -> list[StockBar]:
        period_map = {"daily": "101", "weekly": "102", "monthly": "103"}
        if period not in period_map:
            raise ValueError("period must be one of: daily, weekly, monthly")

        params = {
            "secid": quote_id,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": period_map[period],
            "fqt": "1",
            "beg": compact_date(start),
            "end": compact_date(end),
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
        payload = self._get_json(url, ttl_seconds=12 * 60 * 60)
        data = payload.get("data")
        if not data:
            return []
        klines = data.get("klines") or []
        return [parse_stock_kline(item) for item in klines]

    def get_sina_index_history(
        self,
        index_code: str,
        quote_id: str,
        start: date,
        end: date,
    ) -> list[StockBar]:
        market = quote_id.partition(".")[0]
        symbol_prefix = {"0": "sz", "1": "sh"}.get(market)
        if symbol_prefix is None or not re.fullmatch(r"\d{6}", index_code):
            return []
        params = {
            "symbol": f"{symbol_prefix}{index_code}",
            "scale": "240",
            "ma": "no",
            "datalen": "1023",
        }
        url = (
            "https://quotes.sina.cn/cn/api/jsonp.php/var%20_data=/"
            "CN_MarketDataService.getKLineData?"
            + urlencode(params)
        )
        text = self._get_text(url, ttl_seconds=12 * 60 * 60)
        return [
            item
            for item in parse_sina_index_history(text)
            if start <= item.date <= end
        ]

    def search_assets(
        self,
        keyword: str,
        asset_type: AssetType | None = None,
        limit: int = 10,
        include_indexes: bool = False,
    ) -> list[AssetSearchResult]:
        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            return []

        results: list[AssetSearchResult] = []
        seen: set[tuple[AssetType, str]] = set()
        query_limit = max(limit, 20)
        for search_keyword in build_search_keywords(normalized_keyword):
            params = {
                "input": search_keyword,
                "type": "14",
                "token": SEARCH_TOKEN,
                "count": str(query_limit),
            }
            url = "https://searchapi.eastmoney.com/api/suggest/get?" + urlencode(params)
            payload = self._get_json(url, ttl_seconds=6 * 60 * 60)
            table = payload.get("QuotationCodeTable") or {}
            rows = table.get("Data") or []
            for row in rows:
                result = parse_asset_search_row(row)
                if result is None:
                    continue
                if result.asset_type == "index" and not include_indexes:
                    continue
                if asset_type is not None and result.asset_type != asset_type:
                    continue
                key = (result.asset_type, result.code)
                if key in seen:
                    continue
                seen.add(key)
                results.append(result)

        return rank_search_results(normalized_keyword, results)[:limit]

    def find_index_for_fund(
        self,
        fund_name: str | None,
        limit: int = 5,
    ) -> AssetSearchResult | None:
        for keyword in build_index_search_keywords(fund_name or ""):
            candidates = self.search_assets(keyword, limit=limit, include_indexes=True)
            indexes = [item for item in candidates if item.asset_type == "index"]
            if indexes:
                return rank_search_results(keyword, indexes)[0]
        return None

    def _get_fund_nav_page(
        self,
        code: str,
        start: date,
        end: date,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        normalized_code = normalize_fund_code(code)
        params = {
            "fundCode": normalized_code,
            "pageIndex": str(page),
            "pageSize": str(page_size),
            "startDate": iso_date(start),
            "endDate": iso_date(end),
        }
        url = "https://api.fund.eastmoney.com/f10/lsjz?" + urlencode(params)
        payload = self._get_json(url, ttl_seconds=24 * 60 * 60)
        if payload.get("ErrCode") != 0:
            message = payload.get("ErrMsg") or "unknown upstream error"
            raise EastmoneyError(f"Tiantian Fund NAV request failed: {message}")

        data = payload.get("Data")
        if not isinstance(data, dict):
            raise EastmoneyError("Unexpected Tiantian Fund NAV response")
        raw_rows = data.get("LSJZList")
        if not isinstance(raw_rows, list):
            raise EastmoneyError("Unexpected Tiantian Fund NAV response")

        rows = [item for raw_row in raw_rows if (item := parse_fund_nav_row(raw_row))]
        records = int(payload.get("TotalCount") or len(rows))
        response_page_size = int(payload.get("PageSize") or page_size)
        pages = max(1, (records + response_page_size - 1) // response_page_size)
        return {
            "records": records,
            "pages": pages,
            "curpage": int(payload.get("PageIndex") or page),
            "rows": rows,
        }

    def _get_json(self, url: str, ttl_seconds: int) -> dict[str, Any]:
        text = self._get_text(url, ttl_seconds=ttl_seconds)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            host = urlparse(url).netloc
            raise EastmoneyError(f"Unexpected JSON response from {host}") from exc

    def _get_validated_json(
        self,
        url: str,
        ttl_seconds: int,
        is_success: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        max_attempts = self.retries + 1
        for attempt in range(max_attempts):
            payload = self._get_json(url, ttl_seconds=ttl_seconds)
            if is_success(payload):
                return payload
            self.cache.delete(url)
            if attempt < max_attempts - 1:
                time.sleep(0.4 * (2**attempt))
        return payload

    def _get_text(self, url: str, ttl_seconds: int) -> str:
        cached = self.cache.get(url, ttl_seconds=ttl_seconds)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        max_attempts = self.retries + 1
        for attempt in range(max_attempts):
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    headers=self._headers_for_url(url),
                    follow_redirects=True,
                    http2=False,
                    trust_env=False,
                ) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    text = response.content.decode("utf-8", errors="replace")
                    self.cache.set(url, text)
                    return text
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
                httpx.HTTPStatusError,
            ) as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    time.sleep(0.4 * (2**attempt))
        host = urlparse(url).netloc
        raise EastmoneyError(
            f"Failed to fetch Eastmoney data from {host} after {max_attempts} attempts: "
            f"{last_error}"
        ) from last_error

    def _headers_for_url(self, url: str) -> dict[str, str]:
        headers = dict(self.headers)
        host = urlparse(url).netloc
        if host == "fundf10.eastmoney.com":
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers["Referer"] = "https://fundf10.eastmoney.com/"
        elif host == "api.fund.eastmoney.com":
            headers["Accept"] = "application/json,text/plain,*/*"
            headers["Referer"] = "https://fundf10.eastmoney.com/"
        elif host == "fund.eastmoney.com":
            headers["Accept"] = "application/javascript,text/javascript,*/*;q=0.8"
            headers["Referer"] = "https://fund.eastmoney.com/"
        elif host == "fundmobapi.eastmoney.com":
            headers["Accept"] = "application/json,text/plain,*/*"
            headers["Referer"] = "https://fund.eastmoney.com/"
            headers["User-Agent"] = "Mozilla/5.0"
        elif host == "www.csindex.com.cn":
            headers["Accept"] = "application/json,text/plain,*/*"
            headers["Referer"] = "https://www.csindex.com.cn/"
        elif host == "datacenter-web.eastmoney.com":
            headers["Referer"] = "https://data.eastmoney.com/"
        elif host == "emweb.securities.eastmoney.com":
            headers["Referer"] = "https://emweb.securities.eastmoney.com/"
        elif host == "searchapi.eastmoney.com":
            headers["Referer"] = "https://quote.eastmoney.com/"
        elif host == "quotes.sina.cn":
            headers["Accept"] = "application/json,text/javascript,*/*;q=0.8"
            headers["Referer"] = "https://finance.sina.com.cn/"
        return headers


def stock_bars_from_valuations(rows: list[StockValuationPoint]) -> list[StockBar]:
    bars: list[StockBar] = []
    previous_close: float | None = None
    for row in sorted(rows, key=lambda item: item.date):
        if row.close is None:
            continue
        change_amount = row.close - previous_close if previous_close is not None else None
        change_pct = (
            change_amount / previous_close * 100
            if previous_close not in (None, 0) and change_amount is not None
            else None
        )
        bars.append(
            StockBar(
                date=row.date,
                open=row.close,
                close=row.close,
                high=row.close,
                low=row.close,
                volume=0,
                amount=0,
                amplitude_pct=None,
                change_pct=change_pct,
                change_amount=change_amount,
                turnover_pct=None,
            )
        )
        previous_close = row.close
    return bars


def normalize_symbol(symbol: str) -> str:
    digits = re.sub(r"\D", "", symbol)
    if len(digits) != 6:
        raise ValueError(f"Expected a 6-digit stock symbol, got {symbol!r}")
    return digits


def normalize_fund_code(code: str) -> str:
    digits = re.sub(r"\D", "", code)
    if len(digits) != 6:
        raise ValueError(f"Expected a 6-digit fund code, got {code!r}")
    return digits


def fund_mobile_params(code: str) -> dict[str, str]:
    return {
        "FCODE": normalize_fund_code(code),
        "deviceid": "1234567890",
        "plat": "Android",
        "product": "EFund",
        "version": "6.6.8",
    }


def infer_secid(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if not is_a_share_symbol(code):
        raise ValueError(
            f"{code} does not look like an A-share stock code. "
            "If this is a fund code, choose asset_type='fund'."
        )
    if code.startswith(("5", "6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def f10_stock_code(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("5", "6", "9")):
        return f"SH{code}"
    return f"SZ{code}"


def infer_exchange_fund_secid(code: str) -> str | None:
    normalized_code = normalize_fund_code(code)
    if normalized_code.startswith("5"):
        return f"1.{normalized_code}"
    if normalized_code.startswith("1"):
        return f"0.{normalized_code}"
    return None


def parse_reit_profile(
    payload: dict[str, Any],
    expected_code: str,
) -> ReitProfile:
    data = payload.get("Datas")
    if payload.get("ErrCode") != 0 or not isinstance(data, dict):
        raise EastmoneyError(f"Unexpected Eastmoney REIT profile for {expected_code}")

    fund_code = str(data.get("FCODE") or "").strip()
    fund_type = str(data.get("FTYPE") or "").strip()
    fund_name = repair_mojibake(str(data.get("SHORTNAME") or "").strip()) or ""
    full_name = repair_mojibake(str(data.get("FULLNAME") or "").strip()) or ""
    if fund_code != expected_code:
        raise EastmoneyError(
            f"Eastmoney REIT profile route mismatch: expected {expected_code}, got {fund_code}"
        )
    if fund_type.casefold() != "reits":
        raise EastmoneyError(f"Fund {expected_code} is not an Eastmoney REIT")
    if not fund_name or not full_name:
        raise EastmoneyError(f"Eastmoney REIT profile has no valid name for {expected_code}")

    quote_id = infer_exchange_fund_secid(fund_code)
    if quote_id is None:
        raise EastmoneyError(f"REIT {expected_code} has no supported exchange route")
    exchange = "SSE" if quote_id.startswith("1.") else "SZSE"
    period_end_net_assets = reit_optional_float(
        data.get("ENDNAV"),
        "profile period-end net assets",
    )
    if period_end_net_assets is not None and period_end_net_assets <= 0:
        raise EastmoneyError("Eastmoney REIT profile has invalid period-end net assets")

    return ReitProfile(
        fund_code=fund_code,
        fund_name=fund_name,
        full_name=full_name,
        fund_type=fund_type,
        establishment_date=parse_optional_date(data.get("ESTABDATE")),
        term_text=optional_source_text(data.get("CYCLE")),
        scale_report_date=parse_optional_date(data.get("FEGMRQ")),
        period_end_net_assets_cny=period_end_net_assets,
        exchange=exchange,
        quote_id=quote_id,
        source="eastmoney_fund_mobile",
        raw=data,
    )


def is_a_share_symbol(symbol: str) -> bool:
    code = normalize_symbol(symbol)
    return code.startswith(
        (
            "000",
            "001",
            "002",
            "003",
            "300",
            "301",
            "600",
            "601",
            "603",
            "605",
            "688",
            "689",
        )
    )


def compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def iso_date(value: date) -> str:
    return value.isoformat()


def parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def parse_optional_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return parse_date(str(value))
    except ValueError:
        return None


def to_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def repair_mojibake(value: str | None) -> str | None:
    if value is None:
        return None
    candidates = [value]
    for encoding in ("latin1", "gbk"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except UnicodeError:
            pass
    return min(candidates, key=mojibake_score)


def mojibake_score(value: str) -> int:
    markers = ("�", "å", "æ", "è", "ç", "ä", "ã", "鎷", "氓", "猫", "鈥", "鍙")
    marker_score = sum(value.count(marker) for marker in markers)
    unsafe_score = sum(1 for char in value if ord(char) < 32 or 0xE000 <= ord(char) <= 0xF8FF)
    return marker_score + unsafe_score * 10


def parse_stock_kline(item: str) -> StockBar:
    fields = item.split(",")
    if len(fields) < 11:
        raise EastmoneyError(f"Unexpected stock kline row: {item}")
    return StockBar(
        date=parse_date(fields[0]),
        open=float(fields[1]),
        close=float(fields[2]),
        high=float(fields[3]),
        low=float(fields[4]),
        volume=float(fields[5]),
        amount=float(fields[6]),
        amplitude_pct=to_float(fields[7]),
        change_pct=to_float(fields[8]),
        change_amount=to_float(fields[9]),
        turnover_pct=to_float(fields[10]),
    )


def commodity_main_contract_spec(
    commodity: CommodityMainContractKey,
) -> CommodityMainContractSpec:
    try:
        return COMMODITY_MAIN_CONTRACTS[commodity]
    except KeyError as exc:
        raise ValueError(f"Unsupported commodity main contract: {commodity!r}") from exc


def parse_commodity_kline(
    item: str,
    spec: CommodityMainContractSpec,
    period: CommodityHistoryPeriod = "daily",
) -> CommodityFuturesBar:
    fields = item.split(",")
    if len(fields) < 11:
        raise EastmoneyError(f"Unexpected commodity kline row: {item}")
    try:
        bar_date = parse_date(fields[0])
    except ValueError as exc:
        raise EastmoneyError(f"Unexpected commodity kline date: {fields[0]!r}") from exc

    open_price = required_finite_float(fields[1], "open", item)
    close_price = required_finite_float(fields[2], "close", item)
    high_price = required_finite_float(fields[3], "high", item)
    low_price = required_finite_float(fields[4], "low", item)
    volume_lots = required_finite_float(fields[5], "volume", item)
    if min(open_price, close_price, high_price, low_price) <= 0 or volume_lots < 0:
        raise EastmoneyError(f"Invalid commodity kline price or volume: {item}")

    return CommodityFuturesBar(
        key=spec.key,
        quote_id=spec.quote_id,
        period=period,
        date=bar_date,
        open=open_price,
        close=close_price,
        high=high_price,
        low=low_price,
        volume_lots=volume_lots,
        amount_cny=optional_finite_float(fields[6], "amount", item),
        amplitude_pct=optional_finite_float(fields[7], "amplitude", item),
        change_pct=optional_finite_float(fields[8], "change_pct", item),
        change_amount=optional_finite_float(fields[9], "change_amount", item),
        is_complete=None,
        source=spec.source,
    )


def validated_commodity_kline_rows(
    payload: dict[str, Any],
    spec: CommodityMainContractSpec,
    period: CommodityHistoryPeriod = "daily",
) -> list[CommodityFuturesBar]:
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, dict):
        raise EastmoneyError("Unexpected Eastmoney commodity kline response")

    source_code = str(data.get("code") or "").strip().lower()
    source_name = repair_mojibake(str(data.get("name") or "").strip())
    try:
        source_market = int(data.get("market"))
    except (TypeError, ValueError) as exc:
        raise EastmoneyError("Eastmoney commodity response has no valid market") from exc
    if (
        source_code != spec.source_code
        or source_market != spec.source_market
        or source_name != spec.name_zh
    ):
        raise EastmoneyError(
            "Eastmoney commodity route mismatch: "
            f"expected {spec.quote_id} {spec.name_zh}, got "
            f"{source_market}.{source_code} {source_name or '<missing>'}"
        )

    klines = data.get("klines")
    if not isinstance(klines, list) or any(not isinstance(row, str) for row in klines):
        raise EastmoneyError("Unexpected Eastmoney commodity kline rows")
    rows = [parse_commodity_kline(item, spec, period=period) for item in klines]
    dates = [item.date for item in rows]
    if len(dates) != len(set(dates)):
        raise EastmoneyError("Eastmoney commodity kline response has duplicate dates")
    return sorted(rows, key=lambda item: item.date)


def required_finite_float(value: Any, field: str, row: str) -> float:
    parsed = to_float(value)
    if parsed is None or not isfinite(parsed):
        raise EastmoneyError(f"Invalid commodity kline {field}: {row}")
    return parsed


def optional_finite_float(value: Any, field: str, row: str) -> float | None:
    parsed = to_float(value)
    if parsed is not None and not isfinite(parsed):
        raise EastmoneyError(f"Invalid commodity kline {field}: {row}")
    return parsed


def optional_source_text(value: Any) -> str | None:
    text = repair_mojibake(str(value or "").strip()) or ""
    return None if text in {"", "-", "--", "---"} else text


def reit_optional_float(value: Any, context: str) -> float | None:
    parsed = to_float(value)
    if parsed is not None and not isfinite(parsed):
        raise EastmoneyError(f"Invalid Eastmoney REIT {context}: {value!r}")
    return parsed


def parse_reit_kline(
    item: str,
    profile: ReitProfile,
    period: ReitHistoryPeriod = "daily",
) -> ReitPriceBar:
    fields = item.split(",")
    if len(fields) < 11:
        raise EastmoneyError(f"Unexpected REIT kline row: {item}")
    try:
        bar_date = parse_date(fields[0])
    except ValueError as exc:
        raise EastmoneyError(f"Unexpected REIT kline date: {fields[0]!r}") from exc

    prices = [
        reit_optional_float(fields[index], f"kline {field}")
        for index, field in zip(range(1, 5), ("open", "close", "high", "low"), strict=True)
    ]
    if any(value is None or value <= 0 for value in prices):
        raise EastmoneyError(f"Invalid REIT kline price: {item}")
    open_price, close_price, high_price, low_price = prices
    volume = reit_optional_float(fields[5], "kline volume")
    if volume is None or volume < 0:
        raise EastmoneyError(f"Invalid REIT kline volume: {item}")

    return ReitPriceBar(
        fund_code=profile.fund_code,
        fund_name=profile.fund_name,
        exchange=profile.exchange,
        quote_id=profile.quote_id,
        period=period,
        date=bar_date,
        open=open_price,
        close=close_price,
        high=high_price,
        low=low_price,
        volume=volume,
        amount_cny=reit_optional_float(fields[6], "kline amount"),
        amplitude_pct=reit_optional_float(fields[7], "kline amplitude"),
        change_pct=reit_optional_float(fields[8], "kline change percentage"),
        change_amount=reit_optional_float(fields[9], "kline change amount"),
        turnover_pct=reit_optional_float(fields[10], "kline turnover"),
        is_complete=None,
        source="eastmoney_push2his",
    )


def validated_reit_kline_rows(
    payload: dict[str, Any],
    profile: ReitProfile,
    period: ReitHistoryPeriod = "daily",
) -> list[ReitPriceBar]:
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, dict):
        raise EastmoneyError("Unexpected Eastmoney REIT kline response")

    source_code = str(data.get("code") or "").strip()
    source_name = repair_mojibake(str(data.get("name") or "").strip()) or ""
    try:
        source_market = int(data.get("market"))
        expected_market = int(profile.quote_id.partition(".")[0])
    except (TypeError, ValueError) as exc:
        raise EastmoneyError("Eastmoney REIT response has no valid market") from exc
    if (
        source_code != profile.fund_code
        or source_market != expected_market
        or source_name != profile.fund_name
    ):
        raise EastmoneyError(
            "Eastmoney REIT price route mismatch: "
            f"expected {profile.quote_id} {profile.fund_name}, got "
            f"{source_market}.{source_code} {source_name or '<missing>'}"
        )

    klines = data.get("klines")
    if not isinstance(klines, list) or any(not isinstance(row, str) for row in klines):
        raise EastmoneyError("Unexpected Eastmoney REIT kline rows")
    rows = [parse_reit_kline(item, profile, period=period) for item in klines]
    dates = [item.date for item in rows]
    if len(dates) != len(set(dates)):
        raise EastmoneyError("Eastmoney REIT kline response has duplicate dates")
    return sorted(rows, key=lambda item: item.date)


def validated_reit_financial_payload(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], tuple[int, ...], int]:
    outer = payload.get("Data")
    if payload.get("ErrCode") != 0 or not isinstance(outer, dict):
        raise EastmoneyError("Unexpected Eastmoney REIT financial response")
    data = outer.get("data")
    raw_years = outer.get("years")
    if not isinstance(data, dict) or not isinstance(raw_years, list):
        raise EastmoneyError("Unexpected Eastmoney REIT financial data")

    arrays: dict[str, list[Any]] = {}
    for field_name in REIT_FINANCIAL_FIELDS:
        values = data.get(field_name)
        if not isinstance(values, list):
            raise EastmoneyError(
                f"Eastmoney REIT financial field {field_name} is not an array"
            )
        arrays[field_name] = values
    row_count = len(arrays["FSRQ"])
    if any(len(values) != row_count for values in arrays.values()):
        raise EastmoneyError("Eastmoney REIT financial arrays have unequal lengths")

    try:
        years = tuple(int(value) for value in raw_years)
        response_year = int(outer.get("year"))
    except (TypeError, ValueError) as exc:
        raise EastmoneyError("Eastmoney REIT financial response has invalid years") from exc
    if len(years) != len(set(years)) or tuple(sorted(years, reverse=True)) != years:
        raise EastmoneyError("Eastmoney REIT financial years are not unique descending years")
    if response_year not in years:
        raise EastmoneyError("Eastmoney REIT financial response year is not advertised")

    rows: list[dict[str, Any]] = []
    for index in range(row_count):
        row = {field_name: values[index] for field_name, values in arrays.items()}
        if parse_optional_date(row["FSRQ"]) is None:
            raise EastmoneyError("Eastmoney REIT financial row has no valid report date")
        rows.append(row)
    return rows, years, response_year


def normalized_reit_financial_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        parse_date(str(row["FSRQ"])),
        *(
            reit_optional_float(row.get(field), f"financial {field}")
            for field in REIT_FINANCIAL_FIELDS[1:]
        ),
    )


def deduplicate_reit_financial_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        report_date = parse_optional_date(row.get("FSRQ"))
        if report_date is None:
            raise EastmoneyError("Eastmoney REIT financial row has no valid report date")
        existing = by_date.get(report_date)
        if existing is not None:
            if normalized_reit_financial_values(existing) != normalized_reit_financial_values(
                row
            ):
                raise EastmoneyError(
                    f"Conflicting Eastmoney REIT financial values for {report_date}"
                )
            continue
        by_date[report_date] = row
    return [by_date[report_date] for report_date in sorted(by_date)]


def parse_reit_periodic_report_notice(
    row: dict[str, Any],
) -> ReitPeriodicReportNotice:
    fund_code = str(row.get("FUNDCODE") or "").strip()
    title = repair_mojibake(str(row.get("TITLE") or "").strip()) or ""
    category = str(row.get("NEWCATEGORY") or "").strip()
    publish_date = parse_optional_date(row.get("PUBLISHDATE"))
    announcement_id = str(row.get("ID") or "").strip()
    if not fund_code or not title or publish_date is None or not announcement_id:
        raise EastmoneyError("Unexpected Eastmoney REIT periodic report notice")
    report_date, report_kind = infer_reit_report_period(title)
    return ReitPeriodicReportNotice(
        fund_code=fund_code,
        title=title,
        category=category,
        publish_date=publish_date,
        attachment_type=optional_source_text(row.get("ATTACHTYPE")),
        announcement_id=announcement_id,
        attachment_url=f"https://pdf.dfcfw.com/pdf/H2_{announcement_id}_1.pdf",
        report_date=report_date,
        report_kind=report_kind,
        is_canonical=report_date is not None and report_kind is not None,
        source="eastmoney_fund_announcement",
        raw=row,
    )


def infer_reit_report_period(
    title: str,
) -> tuple[date | None, ReitReportKind | None]:
    match = REIT_PERIODIC_REPORT_PATTERN.search(title.strip())
    if match is None:
        return None, None
    year = int(match.group(1))
    label = match.group(2)
    if label == "年度":
        return date(year, 12, 31), "annual"
    if label in {"中期", "半年度"}:
        return date(year, 6, 30), "semiannual"
    quarter_text = match.group(3)
    quarter = REIT_QUARTER_NUMBERS.get(quarter_text, None)
    if quarter is None and quarter_text and quarter_text.isdigit():
        quarter = int(quarter_text)
    quarter_values: dict[int, tuple[date, ReitReportKind]] = {
        1: (date(year, 3, 31), "q1"),
        2: (date(year, 6, 30), "q2"),
        3: (date(year, 9, 30), "q3"),
        4: (date(year, 12, 31), "q4"),
    }
    return quarter_values.get(quarter, (None, None))


def select_reit_financial_notice(
    report_date: date,
    notices: list[ReitPeriodicReportNotice],
) -> ReitPeriodicReportNotice | None:
    preferred_kinds: dict[tuple[int, int], tuple[ReitReportKind, ...]] = {
        (12, 31): ("annual", "q4"),
        (6, 30): ("semiannual", "q2"),
        (3, 31): ("q1",),
        (9, 30): ("q3",),
    }
    kinds = preferred_kinds.get((report_date.month, report_date.day), ())
    candidates = [
        notice
        for notice in notices
        if notice.is_canonical and notice.report_date == report_date
    ]
    for kind in kinds:
        matching = [notice for notice in candidates if notice.report_kind == kind]
        if matching:
            return max(matching, key=lambda item: (item.publish_date, item.announcement_id))
    return None


def parse_reit_financial_snapshot(
    fund_code: str,
    row: dict[str, Any],
    notice: ReitPeriodicReportNotice | None,
) -> ReitFinancialSnapshot:
    report_date = parse_date(str(row["FSRQ"]))
    return ReitFinancialSnapshot(
        fund_code=fund_code,
        report_date=report_date,
        report_kind=notice.report_kind if notice else None,
        notice_date=notice.publish_date if notice else None,
        realized_income_cny=reit_optional_float(row.get("COMPROFIT"), "realized income"),
        net_profit_cny=reit_optional_float(row.get("NETPROFIT"), "net profit"),
        unit_profit_cny=reit_optional_float(row.get("UNITPROFIT"), "unit profit"),
        net_asset_growth_pct=reit_optional_float(row.get("NGROWTH"), "net asset growth"),
        fund_net_asset_growth_pct=reit_optional_float(
            row.get("FNGROWTH"),
            "fund net asset growth",
        ),
        distributable_profit_cny=reit_optional_float(
            row.get("DISPROFIT"),
            "distributable profit",
        ),
        distributable_profit_per_unit_cny=reit_optional_float(
            row.get("DIFUNTIPROFIT"),
            "distributable profit per unit",
        ),
        period_end_net_assets_cny=reit_optional_float(
            row.get("ENDNAV"),
            "period-end net assets",
        ),
        period_end_unit_nav_cny=reit_optional_float(
            row.get("ENDUNITNAV"),
            "period-end unit NAV",
        ),
        fund_share_nav_growth_pct=reit_optional_float(
            row.get("FCNGROWTH"),
            "fund share NAV growth",
        ),
        point_in_time_eligible=notice is not None,
        source="eastmoney_fund_financial",
        raw=row,
    )


def validated_reit_announcement_page(
    payload: dict[str, Any],
    expected_code: str,
    expected_category: str,
    expected_page: int,
) -> tuple[list[dict[str, Any]], int, int]:
    rows = payload.get("Data")
    if payload.get("ErrCode") != 0 or not isinstance(rows, list):
        raise EastmoneyError("Unexpected Eastmoney REIT announcement response")
    if any(not isinstance(row, dict) for row in rows):
        raise EastmoneyError("Unexpected Eastmoney REIT announcement rows")
    try:
        total_count = int(payload.get("TotalCount"))
        page_size = int(payload.get("PageSize"))
        page_index = int(payload.get("PageIndex"))
    except (TypeError, ValueError) as exc:
        raise EastmoneyError("Invalid Eastmoney REIT announcement pagination") from exc
    if total_count < len(rows) or page_size <= 0 or page_index != expected_page:
        raise EastmoneyError("Invalid Eastmoney REIT announcement pagination")
    for row in rows:
        if (
            str(row.get("FUNDCODE") or "").strip() != expected_code
            or str(row.get("NEWCATEGORY") or "").strip() != expected_category
        ):
            raise EastmoneyError("Eastmoney REIT announcement route mismatch")
    return rows, total_count, page_size


def deduplicate_reit_announcement_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        announcement_id = str(row.get("ID") or "").strip()
        if not announcement_id:
            raise EastmoneyError("Eastmoney REIT announcement has no ID")
        existing = by_id.get(announcement_id)
        if existing is not None and existing != row:
            raise EastmoneyError(
                f"Conflicting Eastmoney REIT announcement {announcement_id}"
            )
        by_id[announcement_id] = row
    return list(by_id.values())


def parse_stock_valuation_row(row: dict[str, Any]) -> StockValuationPoint:
    return StockValuationPoint(
        date=parse_date(str(row["TRADE_DATE"])),
        code=str(row.get("SECURITY_CODE") or ""),
        name=repair_mojibake(row.get("SECURITY_NAME_ABBR")),
        close=to_float(row.get("CLOSE_PRICE")),
        market_cap=to_float(row.get("TOTAL_MARKET_CAP")),
        pe_ttm=to_float(row.get("PE_TTM")),
        pe_static=to_float(row.get("PE_LAR")),
        pb=to_float(row.get("PB_MRQ")),
        ps_ttm=to_float(row.get("PS_TTM")),
        pcf_ocf_ttm=to_float(row.get("PCF_OCF_TTM")),
        peg=to_float(row.get("PEG_CAR")),
        raw=row,
        board_code=str(row.get("BOARD_CODE") or "").strip() or None,
        board_name=repair_mojibake(row.get("BOARD_NAME")),
        original_board_code=str(row.get("ORIG_BOARD_CODE") or "").strip() or None,
    )


def parse_stock_profile(row: dict[str, Any]) -> StockProfile:
    return StockProfile(
        code=str(row.get("SECURITY_CODE") or ""),
        name=repair_mojibake(row.get("SECURITY_NAME_ABBR")),
        em_industry=repair_mojibake(row.get("EM2016")),
        csrc_industry=repair_mojibake(row.get("INDUSTRYCSRC1")),
        security_type=repair_mojibake(row.get("SECURITY_TYPE")),
        raw=row,
    )


def parse_stock_financial_indicator(row: dict[str, Any]) -> StockFinancialIndicator:
    return StockFinancialIndicator(
        date=parse_date(str(row["REPORT_DATE"])),
        report_type=repair_mojibake(row.get("REPORT_TYPE")),
        notice_date=parse_optional_date(row.get("NOTICE_DATE")),
        source_updated_at=parse_optional_date(row.get("UPDATE_DATE")),
        org_type=repair_mojibake(row.get("ORG_TYPE")),
        roe_weighted=to_float(row.get("ROEJQ")),
        roe_deducted_weighted=to_float(row.get("ROEKCJQ")),
        parent_netprofit_growth_pct=to_float(row.get("PARENTNETPROFITTZ")),
        revenue_growth_pct=to_float(row.get("TOTALOPERATEREVETZ")),
        gross_margin_pct=to_float(row.get("XSMLL")),
        net_margin_pct=to_float(row.get("XSJLL")),
        roic_pct=to_float(row.get("ROIC")),
        fcff_backward_cny=to_float(row.get("FCFF_BACK")),
        fcff_forward_cny=to_float(row.get("FCFF_FORWARD")),
        net_interest_margin_pct=to_float(row.get("NET_INTEREST_MARGIN")),
        net_interest_spread_pct=to_float(row.get("NET_INTEREST_SPREAD")),
        non_performing_loan_ratio_pct=to_float(row.get("NONPERLOAN")),
        provision_coverage_ratio_pct=to_float(row.get("BLDKBBL")),
        capital_adequacy_ratio_pct=to_float(row.get("NEWCAPITALADER")),
        tier1_capital_adequacy_ratio_pct=to_float(row.get("FIRST_ADEQUACY_RATIO")),
        core_tier1_capital_adequacy_ratio_pct=to_float(row.get("HXYJBCZL")),
        solvency_adequacy_ratio_pct=to_float(row.get("SOLVENCY_AR")),
        new_business_value_cny=to_float(row.get("NBV_LIFE")),
        new_business_value_margin_pct=to_float(row.get("NBV_RATE")),
        surrender_rate_pct=to_float(row.get("SURRENDER_RATE_LIFE")),
        risk_coverage_ratio_pct=to_float(row.get("RISK_COVERAGE")),
        liquidity_coverage_ratio_pct=to_float(row.get("LIQUIDITY_COVERAGE_RATIO")),
        net_stable_funding_ratio_pct=to_float(row.get("NET_FUNDING_RATIO")),
        net_capital_to_net_assets_pct=to_float(row.get("JZBJZC")),
        net_capital_cny=to_float(row.get("JZB")),
        net_assets_cny=to_float(row.get("JZC")),
        raw=row,
    )


def financial_statement_common_fields(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row.get("SECURITY_CODE") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise EastmoneyError("Eastmoney F10 financial statement row has no valid code")
    return {
        "code": code,
        "report_date": parse_date(str(row["REPORT_DATE"])),
        "report_type": repair_mojibake(row.get("REPORT_TYPE")),
        "report_name": repair_mojibake(row.get("REPORT_DATE_NAME")),
        "notice_date": parse_optional_date(row.get("NOTICE_DATE")),
        "source_updated_at": parse_optional_date(row.get("UPDATE_DATE")),
        "currency": repair_mojibake(row.get("CURRENCY")),
        "org_type": repair_mojibake(row.get("ORG_TYPE")),
        "raw": row,
    }


def parse_stock_balance_sheet(row: dict[str, Any]) -> StockBalanceSheet:
    return StockBalanceSheet(
        **financial_statement_common_fields(row),
        total_assets_cny=to_float(row.get("TOTAL_ASSETS")),
        total_current_assets_cny=to_float(row.get("TOTAL_CURRENT_ASSETS")),
        monetary_funds_cny=to_float(row.get("MONETARYFUNDS")),
        accounts_receivable_cny=to_float(row.get("ACCOUNTS_RECE")),
        inventory_cny=to_float(row.get("INVENTORY")),
        contract_asset_cny=to_float(row.get("CONTRACT_ASSET")),
        total_liabilities_cny=to_float(row.get("TOTAL_LIABILITIES")),
        total_current_liabilities_cny=to_float(row.get("TOTAL_CURRENT_LIAB")),
        accounts_payable_cny=to_float(row.get("ACCOUNTS_PAYABLE")),
        contract_liability_cny=to_float(row.get("CONTRACT_LIAB")),
        short_term_borrowings_cny=to_float(row.get("SHORT_LOAN")),
        current_portion_noncurrent_liabilities_cny=to_float(
            row.get("NONCURRENT_LIAB_1YEAR")
        ),
        long_term_borrowings_cny=to_float(row.get("LONG_LOAN")),
        bonds_payable_cny=to_float(row.get("BOND_PAYABLE")),
        total_equity_cny=to_float(row.get("TOTAL_EQUITY")),
    )


def parse_stock_income_statement(row: dict[str, Any]) -> StockIncomeStatement:
    return StockIncomeStatement(
        **financial_statement_common_fields(row),
        total_operating_revenue_cny=to_float(row.get("TOTAL_OPERATE_INCOME")),
        operating_cost_cny=to_float(row.get("OPERATE_COST")),
        sales_expense_cny=to_float(row.get("SALE_EXPENSE")),
        management_expense_cny=to_float(row.get("MANAGE_EXPENSE")),
        finance_expense_cny=to_float(row.get("FINANCE_EXPENSE")),
        research_expense_cny=to_float(row.get("RESEARCH_EXPENSE")),
        development_expense_cny=to_float(row.get("DEVELOP_EXPENSE")),
        operating_profit_cny=to_float(row.get("OPERATE_PROFIT")),
        parent_net_profit_cny=to_float(row.get("PARENT_NETPROFIT")),
        deducted_parent_net_profit_cny=to_float(row.get("DEDUCT_PARENT_NETPROFIT")),
        income_tax_cny=to_float(row.get("INCOME_TAX")),
        total_operating_revenue_yoy_pct=to_float(row.get("TOTAL_OPERATE_INCOME_YOY")),
        research_expense_yoy_pct=to_float(row.get("RESEARCH_EXPENSE_YOY")),
        parent_net_profit_yoy_pct=to_float(row.get("PARENT_NETPROFIT_YOY")),
    )


def parse_stock_cash_flow_statement(row: dict[str, Any]) -> StockCashFlowStatement:
    return StockCashFlowStatement(
        **financial_statement_common_fields(row),
        sales_services_cash_cny=to_float(row.get("SALES_SERVICES")),
        cash_paid_to_staff_cny=to_float(row.get("PAY_STAFF_CASH")),
        net_operating_cash_flow_cny=to_float(row.get("NETCASH_OPERATE")),
        capital_expenditure_cash_cny=to_float(row.get("CONSTRUCT_LONG_ASSET")),
        investment_cash_paid_cny=to_float(row.get("INVEST_PAY_CASH")),
        net_investing_cash_flow_cny=to_float(row.get("NETCASH_INVEST")),
        borrowings_received_cash_cny=to_float(row.get("RECEIVE_LOAN_CASH")),
        debt_repaid_cash_cny=to_float(row.get("PAY_DEBT_CASH")),
        dividends_interest_paid_cash_cny=to_float(row.get("ASSIGN_DIVIDEND_PORFIT")),
        net_financing_cash_flow_cny=to_float(row.get("NETCASH_FINANCE")),
        cash_equivalents_increase_cny=to_float(row.get("CCE_ADD")),
        ending_cash_cny=to_float(row.get("END_CASH")),
    )


def validated_f10_financial_rows(
    payload: dict[str, Any],
    context: str,
) -> list[dict[str, Any]]:
    rows = payload.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise EastmoneyError(f"Unexpected Eastmoney F10 {context} response")
    return rows


def unique_report_dates(rows: list[dict[str, Any]]) -> list[date]:
    dates: set[date] = set()
    for row in rows:
        parsed = parse_optional_date(row.get("REPORT_DATE"))
        if parsed is None:
            raise EastmoneyError("Eastmoney F10 report date list contains no report date")
        dates.add(parsed)
    return sorted(dates, reverse=True)


def validate_f10_financial_statement_route(
    rows: list[dict[str, Any]],
    *,
    expected_security_code: str,
    requested_dates: set[date],
    context: str,
) -> None:
    for row in rows:
        source_code = str(row.get("SECURITY_CODE") or "").strip()
        report_date = parse_optional_date(row.get("REPORT_DATE"))
        if source_code != expected_security_code or report_date not in requested_dates:
            raise EastmoneyError(
                f"Eastmoney F10 {context} route mismatch: expected "
                f"{expected_security_code} and requested report dates"
            )


def deduplicate_financial_statement_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        report_date = parse_optional_date(row.get("REPORT_DATE"))
        if report_date is None:
            raise EastmoneyError("Eastmoney F10 financial statement row has no report date")
        existing = by_date.get(report_date)
        if existing is None:
            by_date[report_date] = row
            continue
        existing_notice = parse_optional_date(existing.get("NOTICE_DATE")) or date.min
        candidate_notice = parse_optional_date(row.get("NOTICE_DATE")) or date.min
        if candidate_notice >= existing_notice:
            by_date[report_date] = row
    return [by_date[report_date] for report_date in sorted(by_date)]


def parse_stock_peer_comparison(row: dict[str, Any]) -> StockPeerComparison | None:
    code = str(row.get("CORRE_SECURITY_CODE") or row.get("SECURITY_CODE") or "")
    name = repair_mojibake(row.get("CORRE_SECURITY_NAME")) or code
    if name in {"行业平均", "行业中值"} or code in {"行业平均", "行业中值"}:
        return None
    return StockPeerComparison(
        code=code,
        name=name,
        rank=int(row["PAIMING"]) if str(row.get("PAIMING") or "").isdigit() else None,
        pe_ttm=to_float(row.get("PE_TTM")),
        pb_mrq=to_float(row.get("PB_MRQ")),
        peg=to_float(row.get("PEG")),
        roe_avg=to_float(row.get("ROE_AVG")),
        net_profit_growth_ttm=to_float(row.get("JLRTTM")),
        revenue_growth_ttm=to_float(row.get("YYSRTTM")),
        raw=row,
    )


def parse_stock_dividend_plan(row: dict[str, Any]) -> StockDividendPlan:
    plan = repair_mojibake(row.get("IMPL_PLAN_PROFILE"))
    return StockDividendPlan(
        notice_date=parse_optional_date(row.get("NOTICE_DATE")),
        plan=plan,
        progress=repair_mojibake(row.get("ASSIGN_PROGRESS")),
        ex_dividend_date=parse_optional_date(row.get("EX_DIVIDEND_DATE")),
        cash_per_share=parse_cash_per_share(plan),
        raw=row,
    )


def parse_stock_dividend_summary(row: dict[str, Any]) -> StockDividendSummary:
    return StockDividendSummary(
        year=str(row.get("STATISTICS_YEAR") or ""),
        total_dividend=to_float(row.get("TOTAL_DIVIDEND")),
        raw=row,
    )


def parse_cash_per_share(plan: str | None) -> float | None:
    if not plan:
        return None
    match = re.search(r"10\s*派\s*([0-9.]+)\s*元", plan)
    if not match:
        return None
    return float(match.group(1)) / 10


def parse_asset_search_row(row: dict[str, Any]) -> AssetSearchResult | None:
    code = str(row.get("UnifiedCode") or row.get("Code") or "").strip()

    classify = str(row.get("Classify") or "").strip()
    security_type_name = repair_mojibake(str(row.get("SecurityTypeName") or "").strip()) or None
    asset_type: str | None = None
    if classify == "AStock":
        if not re.fullmatch(r"\d{6}", code):
            return None
        try:
            if is_a_share_symbol(code):
                asset_type = "stock"
        except ValueError:
            return None
    elif classify in {"Fund", "OTCFUND"} or security_type_name == "基金":
        if not re.fullmatch(r"\d{6}", code):
            return None
        asset_type = "fund"
    elif classify in {"Index", "24"} or security_type_name == "指数":
        if not re.fullmatch(r"[A-Z0-9]{5,8}", code.upper()):
            return None
        code = code.upper()
        asset_type = "index"

    if asset_type is None:
        return None

    name = repair_mojibake(str(row.get("Name") or "").strip())
    if not name:
        return None
    return AssetSearchResult(
        asset_type=asset_type,
        code=code,
        name=name,
        market=security_type_name,
        quote_id=str(row.get("QuoteID") or "").strip() or None,
        source_type=classify or None,
        raw=row,
    )


def normalize_search_text(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def looks_like_index_fund(name: str | None) -> bool:
    normalized = normalize_search_text(name or "")
    return "ETF" in normalized or "指数" in normalized


def looks_like_feeder_fund(name: str | None) -> bool:
    return "联接" in normalize_search_text(name or "")


def build_search_keywords(keyword: str) -> list[str]:
    normalized = normalize_search_text(keyword)
    if not normalized:
        return []

    keywords = [normalized]
    for manager in KNOWN_FUND_MANAGERS:
        if manager not in normalized:
            continue
        stripped = normalized.replace(manager, "")
        if len(stripped) >= 2:
            keywords.append(stripped)
        keywords.append(manager)
    return list(dict.fromkeys(keywords))


def build_index_search_keywords(fund_name: str) -> list[str]:
    normalized = normalize_search_text(fund_name)
    if not normalized:
        return []

    candidates = [normalized]
    stripped = normalized
    for manager in KNOWN_FUND_MANAGERS:
        stripped = stripped.replace(manager, "")
    stripped = re.sub(r"\([^)]*\)", "", stripped)
    stripped = re.sub(r"(ETF|LOF|QDII|联接|基金|指数|增强|发起式|场内|A|C)$", "", stripped)
    stripped = re.sub(r"(ETF|LOF|QDII|联接|基金|指数|增强|发起式|场内)", "", stripped)
    stripped = stripped.strip()
    if len(stripped) >= 2:
        candidates.append(stripped)
    without_tail_number = re.sub(r"\d+$", "", stripped)
    if len(without_tail_number) >= 2:
        candidates.append(without_tail_number)
    return list(dict.fromkeys(candidates))


def search_terms(keyword: str) -> list[str]:
    normalized = normalize_search_text(keyword)
    terms = [normalized] if normalized else []
    for manager in KNOWN_FUND_MANAGERS:
        if manager in normalized:
            terms.append(manager)
            stripped = normalized.replace(manager, "")
            if len(stripped) >= 2:
                terms.append(stripped)
    return list(dict.fromkeys(terms))


def rank_search_results(
    keyword: str,
    results: list[AssetSearchResult],
) -> list[AssetSearchResult]:
    return sorted(
        results,
        key=lambda item: search_result_score(keyword, item),
        reverse=True,
    )


def search_result_score(keyword: str, result: AssetSearchResult) -> int:
    normalized_keyword = normalize_search_text(keyword)
    normalized_name = normalize_search_text(result.name)
    score = 0
    if result.code == normalized_keyword:
        score += 10000
    if normalized_name == normalized_keyword:
        score += 9000
    if normalized_keyword and normalized_keyword in normalized_name:
        score += 3000 + len(normalized_keyword)

    for term in search_terms(normalized_keyword):
        if term and term in normalized_name:
            score += 200 + len(term) * 10

    if result.asset_type == "fund" and any(word in normalized_name for word in ("ETF", "LOF")):
        score += 30
    return score


def parse_pingzhongdata_fund_name(text: str) -> str | None:
    match = re.search(r'var\s+fS_name\s*=\s*"([^"]+)"', text)
    if not match:
        return None
    return repair_mojibake(match.group(1))


def parse_fund_tracking_info(payload: dict[str, Any]) -> FundTrackingInfo:
    if payload.get("ErrCode") != 0 or not isinstance(payload.get("Datas"), dict):
        message = payload.get("ErrMsg") or "unexpected response"
        raise EastmoneyError(f"Failed to resolve fund tracking information: {message}")
    data = payload["Datas"]
    fund_code = re.sub(r"\D", "", str(data.get("FCODE") or ""))
    if len(fund_code) != 6:
        raise EastmoneyError("Failed to resolve fund tracking information: invalid fund code")
    index_code = str(data.get("INDEXCODE") or "").strip()
    if index_code in {"", "-", "--"}:
        index_code = None
    index_name = repair_mojibake(data.get("INDEXNAME"))
    if index_name in {"", "-", "--"}:
        index_name = None
    return FundTrackingInfo(
        fund_code=fund_code,
        fund_name=repair_mojibake(data.get("SHORTNAME")),
        fund_type=repair_mojibake(data.get("FTYPE")),
        index_code=index_code,
        index_name=index_name,
        target_etf_code=None,
        target_etf_name=None,
    )


def parse_fund_product_info(payload: dict[str, Any]) -> FundProductInfo:
    if payload.get("ErrCode") != 0 or not isinstance(payload.get("Datas"), dict):
        message = payload.get("ErrMsg") or "unexpected response"
        raise EastmoneyError(f"Failed to resolve fund product information: {message}")
    data = payload["Datas"]
    fund_code = re.sub(r"\D", "", str(data.get("FCODE") or ""))
    if len(fund_code) != 6:
        raise EastmoneyError("Failed to resolve fund product information: invalid fund code")
    return FundProductInfo(
        fund_code=fund_code,
        fund_name=repair_mojibake(data.get("SHORTNAME")),
        fund_type=repair_mojibake(data.get("FTYPE")),
        establishment_date=parse_optional_date(data.get("ESTABDATE")),
        scale_report_date=parse_optional_date(data.get("FEGMRQ")),
        period_end_net_assets_cny=to_float(data.get("ENDNAV")),
        management_fee_pct=parse_percent_value(data.get("MGREXP")),
        custody_fee_pct=parse_percent_value(data.get("TRUSTEXP")),
        sales_service_fee_pct=parse_percent_value(data.get("SALESEXP")),
        benchmark=repair_mojibake(data.get("PERFCMP") or data.get("BENCH")),
        raw=data,
    )


def parse_percent_value(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    return to_float(text.removesuffix("%"))


def parse_fund_position_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("ErrCode") != 0 or not isinstance(payload.get("Datas"), dict):
        message = payload.get("ErrMsg") or "unexpected response"
        raise EastmoneyError(f"Failed to resolve fund position information: {message}")
    data = payload["Datas"]
    report_date = parse_optional_date(payload.get("Expansion"))
    holdings: list[FundHolding] = []
    for index, row in enumerate(data.get("fundStocks") or [], start=1):
        if not isinstance(row, dict):
            continue
        code = re.sub(r"\D", "", str(row.get("GPDM") or ""))
        name = repair_mojibake(row.get("GPJC"))
        if len(code) != 6 or not name:
            continue
        holdings.append(
            FundHolding(
                rank=index,
                code=code,
                name=name,
                weight_pct=to_float(row.get("JZBL")),
                shares_10k=None,
                market_value_10k=None,
                report_date=report_date,
            )
        )
    target_etf_code = re.sub(r"\D", "", str(data.get("ETFCODE") or "")) or None
    if target_etf_code and len(target_etf_code) != 6:
        target_etf_code = None
    return {
        "holdings": holdings,
        "report_date": report_date,
        "target_etf_code": target_etf_code,
        "target_etf_name": repair_mojibake(data.get("ETFSHORTNAME")),
    }


def parse_csi_index_top_holdings(payload: dict[str, Any]) -> list[FundHolding]:
    if str(payload.get("code")) != "200" or not isinstance(payload.get("data"), dict):
        message = payload.get("msg") or "unexpected response"
        raise EastmoneyError(f"Failed to resolve CSI index holdings: {message}")
    data = payload["data"]
    report_date = parse_optional_date(data.get("updateDate"))
    holdings: list[FundHolding] = []
    for index, row in enumerate(data.get("weightList") or [], start=1):
        if not isinstance(row, dict):
            continue
        code = re.sub(r"\D", "", str(row.get("securityCode") or ""))
        name = repair_mojibake(row.get("securityName"))
        if len(code) != 6 or not name:
            continue
        holdings.append(
            FundHolding(
                rank=int(row["rowNum"]) if str(row.get("rowNum") or "").isdigit() else index,
                code=code,
                name=name,
                weight_pct=to_float(row.get("preciseWeight") or row.get("weight")),
                shares_10k=None,
                market_value_10k=None,
                report_date=report_date,
            )
        )
    return sorted(holdings, key=lambda item: item.rank)


def build_fund_holdings_route(
    holdings: list[FundHolding],
    source: str,
    scope: str,
    tracking: FundTrackingInfo | None,
    fallback_reasons: list[str],
) -> FundHoldingsRoute:
    report_date = next((item.report_date for item in holdings if item.report_date), None)
    coverage = min(sum(item.weight_pct or 0.0 for item in holdings) / 100.0, 1.0)
    return FundHoldingsRoute(
        holdings=holdings,
        source=source,
        scope=scope,
        as_of=report_date,
        coverage=round(coverage, 4),
        tracking=tracking,
        fallback_reasons=tuple(fallback_reasons),
    )


def parse_fund_archives_content(text: str) -> str:
    match = re.search(
        r"var\s+apidata\s*=\s*\{\s*content:\"(.*)\",arryear:",
        text,
        re.S,
    )
    if not match:
        raise EastmoneyError("Unexpected Tiantian Fund holdings response")
    content = match.group(1)
    try:
        content = json.loads(f'"{content}"')
    except json.JSONDecodeError:
        content = content.replace(r'\"', '"').replace(r"\/", "/")
    return unescape(content)


def parse_fund_holdings_table(content: str) -> list[FundHolding]:
    parser = FundHoldingsHTMLParser.parse(content)
    report_date_match = re.search(r"截止至：\s*(\d{4}-\d{2}-\d{2})", parser.text)
    report_date = (
        parse_optional_date(report_date_match.group(1)) if report_date_match else None
    )
    holdings: list[FundHolding] = []
    for cells in parser.rows:
        if len(cells) < 9 or not cells[0].isdigit():
            continue
        code = re.sub(r"\D", "", cells[1])
        if not code:
            continue
        holdings.append(
            FundHolding(
                rank=int(cells[0]),
                code=code,
                name=repair_mojibake(cells[2]) or cells[2],
                weight_pct=to_float(cells[6]),
                shares_10k=to_float(cells[7]),
                market_value_10k=to_float(cells[8]),
                report_date=report_date,
            )
        )
    return sorted(holdings, key=lambda item: item.rank)


def parse_fund_nav_table(content: str) -> list[FundNavPoint]:
    table_rows = FundNavHTMLParser.parse(content)
    rows: list[FundNavPoint] = []
    for cells in table_rows:
        if len(cells) < 4:
            continue
        rows.append(
            FundNavPoint(
                date=parse_date(cells[0]),
                unit_nav=to_float(cells[1]),
                cumulative_nav=to_float(cells[2]),
                daily_growth_pct=to_float(cells[3]),
                subscribe_status=repair_mojibake(cells[4]) if len(cells) > 4 else None,
                redeem_status=repair_mojibake(cells[5]) if len(cells) > 5 else None,
            )
        )
    return rows


def parse_sina_index_history(text: str) -> list[StockBar]:
    match = re.search(r"var\s+_data=\((null|\[.*\])\);", text, re.S)
    if not match or match.group(1) == "null":
        return []
    try:
        raw_rows = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise EastmoneyError("Unexpected Sina index history response") from exc
    rows: list[StockBar] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        rows.append(
            StockBar(
                date=parse_date(str(row["day"])),
                open=to_float(row.get("open")),
                close=to_float(row.get("close")),
                high=to_float(row.get("high")),
                low=to_float(row.get("low")),
                volume=to_float(row.get("volume")),
                amount=None,
                amplitude_pct=None,
                change_pct=None,
                change_amount=None,
                turnover_pct=None,
            )
        )
    return sorted(rows, key=lambda item: item.date)


def parse_fund_nav_row(row: Any) -> FundNavPoint | None:
    if not isinstance(row, dict):
        return None
    nav_date = parse_optional_date(row.get("FSRQ"))
    if nav_date is None:
        return None
    return FundNavPoint(
        date=nav_date,
        unit_nav=to_float(row.get("DWJZ")),
        cumulative_nav=to_float(row.get("LJJZ")),
        daily_growth_pct=to_float(row.get("JZZZL")),
        subscribe_status=repair_mojibake(row.get("SGZT")),
        redeem_status=repair_mojibake(row.get("SHZT")),
    )


def parse_reit_distribution_table(
    fund_code: str,
    html: str,
) -> list[ReitDistribution]:
    parser = ReitDistributionHTMLParser.parse(html)
    if not parser.found_target_table:
        raise EastmoneyError("Eastmoney REIT distribution table was not found")

    distributions: list[ReitDistribution] = []
    for cells in parser.rows:
        if len(cells) != 5:
            raise EastmoneyError(f"Unexpected REIT distribution row: {cells!r}")
        year_match = re.fullmatch(r"(20\d{2})年", cells[0])
        cash_match = re.fullmatch(r"每份派现金([0-9]+(?:\.[0-9]+)?)元", cells[3])
        if year_match is None or cash_match is None:
            raise EastmoneyError(f"Unexpected REIT distribution row: {cells!r}")
        cash_per_unit = reit_optional_float(cash_match.group(1), "cash per unit")
        if cash_per_unit is None or cash_per_unit <= 0:
            raise EastmoneyError(f"Invalid REIT distribution cash amount: {cells!r}")

        record_date = parse_reit_table_date(cells[1], "record date")
        ex_dividend_date = parse_reit_table_date(cells[2], "ex-dividend date")
        payment_date = parse_reit_table_date(cells[4], "payment date")
        distributions.append(
            ReitDistribution(
                fund_code=fund_code,
                year=int(year_match.group(1)),
                record_date=record_date,
                ex_dividend_date=ex_dividend_date,
                cash_per_unit_cny=cash_per_unit,
                payment_date=payment_date,
                announcement_date=None,
                available_date=None,
                point_in_time_eligible=False,
                source="eastmoney_fund_distribution",
                raw_row=tuple(cells),
            )
        )
    dates = [item.ex_dividend_date for item in distributions if item.ex_dividend_date]
    if len(dates) != len(set(dates)):
        raise EastmoneyError("Eastmoney REIT distribution table has duplicate ex-dates")
    return sorted(
        distributions,
        key=lambda item: item.ex_dividend_date or date.min,
    )


def parse_reit_table_date(value: str, context: str) -> date | None:
    normalized = value.strip()
    if normalized in {"", "-", "--", "---"}:
        return None
    parsed = parse_optional_date(normalized)
    if parsed is None or parsed.isoformat() != normalized:
        raise EastmoneyError(f"Invalid REIT distribution {context}: {value!r}")
    return parsed


def match_reit_distribution_announcements(
    distributions: list[ReitDistribution],
    announcement_rows: list[dict[str, Any]],
) -> list[ReitDistribution]:
    candidates: list[tuple[str, date]] = []
    for row in announcement_rows:
        title = repair_mojibake(str(row.get("TITLE") or "")) or ""
        publish_date = parse_optional_date(row.get("PUBLISHDATE"))
        announcement_id = str(row.get("ID") or "").strip()
        if "收益分配" in title and publish_date is not None and announcement_id:
            candidates.append((announcement_id, publish_date))

    used_ids: set[str] = set()
    matched: list[ReitDistribution] = []
    for distribution in sorted(
        distributions,
        key=lambda item: item.ex_dividend_date or date.min,
    ):
        ex_date = distribution.ex_dividend_date
        if ex_date is None:
            matched.append(distribution)
            continue
        eligible = [
            candidate
            for candidate in candidates
            if candidate[0] not in used_ids
            and candidate[1] <= ex_date
            and (ex_date - candidate[1]).days <= 30
        ]
        if not eligible:
            matched.append(distribution)
            continue
        latest_date = max(candidate[1] for candidate in eligible)
        nearest = [candidate for candidate in eligible if candidate[1] == latest_date]
        if len(nearest) != 1:
            matched.append(distribution)
            continue
        announcement_id, announcement_date = nearest[0]
        used_ids.add(announcement_id)
        matched.append(
            replace(
                distribution,
                announcement_date=announcement_date,
                available_date=max(announcement_date, ex_date),
                point_in_time_eligible=True,
            )
        )
    return matched


class FundNavHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_td = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    @classmethod
    def parse(cls, html: str) -> list[list[str]]:
        parser = cls()
        parser.feed(html)
        return parser.rows

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() == "td":
            self._in_td = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td" and self._in_td:
            self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = []
            self._in_td = False
        elif tag == "tr" and self._current_row:
            self.rows.append(self._current_row)


class ReitDistributionHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._target_table_depth = 0
        self._in_td = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []
        self.found_target_table = False

    @classmethod
    def parse(cls, html: str) -> ReitDistributionHTMLParser:
        parser = cls()
        parser.feed(html)
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "table":
            if self._target_table_depth:
                self._target_table_depth += 1
            elif "cfxq" in classes:
                self._target_table_depth = 1
                self.found_target_table = True
            return
        if not self._target_table_depth:
            return
        if tag == "tr":
            self._current_row = []
        elif tag == "td":
            self._in_td = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._target_table_depth and self._in_td:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._target_table_depth:
            return
        if tag == "td" and self._in_td:
            self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = []
            self._in_td = False
        elif tag == "tr" and self._current_row:
            self.rows.append(self._current_row)
            self._current_row = []
        elif tag == "table":
            self._target_table_depth -= 1


class FundHoldingsHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_td = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []
        self._text: list[str] = []

    @classmethod
    def parse(cls, html: str) -> FundHoldingsHTMLParser:
        parser = cls()
        parser.feed(html)
        return parser

    @property
    def text(self) -> str:
        return "".join(self._text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag == "td":
            self._in_td = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        self._text.append(data)
        if self._in_td:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td" and self._in_td:
            self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = []
            self._in_td = False
        elif tag == "tr" and self._current_row:
            self.rows.append(self._current_row)
