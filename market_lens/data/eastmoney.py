from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from market_lens.config import settings
from market_lens.storage.sqlite_cache import SQLiteCache
from market_lens.types import (
    AssetSearchResult,
    AssetType,
    FundHolding,
    FundHoldingsRoute,
    FundNavPoint,
    FundTrackingInfo,
    StockBar,
    StockDividendPlan,
    StockDividendSummary,
    StockFinancialIndicator,
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
        params = {
            "FCODE": normalized_code,
            "deviceid": "1234567890",
            "plat": "Android",
            "product": "EFund",
            "version": "6.6.8",
        }
        detail_url = (
            "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNDetailInformation?"
            + urlencode(params)
        )
        detail = parse_fund_tracking_info(
            self._get_validated_json(
                detail_url,
                ttl_seconds=24 * 60 * 60,
                is_success=lambda payload: payload.get("ErrCode") == 0,
            )
        )
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
        roe_weighted=to_float(row.get("ROEJQ")),
        roe_deducted_weighted=to_float(row.get("ROEKCJQ")),
        parent_netprofit_growth_pct=to_float(row.get("PARENTNETPROFITTZ")),
        revenue_growth_pct=to_float(row.get("TOTALOPERATEREVETZ")),
        gross_margin_pct=to_float(row.get("XSMLL")),
        net_margin_pct=to_float(row.get("XSJLL")),
        raw=row,
    )


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
    return FundTrackingInfo(
        fund_code=fund_code,
        fund_name=repair_mojibake(data.get("SHORTNAME")),
        fund_type=repair_mojibake(data.get("FTYPE")),
        index_code=str(data.get("INDEXCODE") or "").strip() or None,
        index_name=repair_mojibake(data.get("INDEXNAME")),
        target_etf_code=None,
        target_etf_name=None,
    )


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
