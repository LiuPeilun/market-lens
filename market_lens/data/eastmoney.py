from __future__ import annotations

import json
import re
import time
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
    FundNavPoint,
    StockBar,
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

    def get_fund_name(self, code: str) -> str | None:
        normalized_code = normalize_fund_code(code)
        url = f"https://fund.eastmoney.com/pingzhongdata/{normalized_code}.js"
        text = self._get_text(url, ttl_seconds=24 * 60 * 60)
        return parse_pingzhongdata_fund_name(text)

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
        params = {
            "type": "lsjz",
            "code": code,
            "page": str(page),
            "per": str(page_size),
            "sdate": iso_date(start),
            "edate": iso_date(end),
        }
        url = "https://fundf10.eastmoney.com/F10DataApi.aspx?" + urlencode(params)
        text = self._get_text(url, ttl_seconds=24 * 60 * 60)
        match = re.search(r"var\s+apidata\s*=\s*(\{.*\});?", text, re.S)
        if not match:
            raise EastmoneyError("Unexpected Tiantian Fund NAV response")
        raw = match.group(1)
        raw = raw.replace("content:", '"content":')
        raw = raw.replace("records:", '"records":')
        raw = raw.replace("pages:", '"pages":')
        raw = raw.replace("curpage:", '"curpage":')
        payload = json.loads(raw)
        content = unescape(payload.get("content") or "")
        return {
            "records": payload.get("records"),
            "pages": payload.get("pages"),
            "curpage": payload.get("curpage"),
            "rows": parse_fund_nav_table(content),
        }

    def _get_json(self, url: str, ttl_seconds: int) -> dict[str, Any]:
        text = self._get_text(url, ttl_seconds=ttl_seconds)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            host = urlparse(url).netloc
            raise EastmoneyError(f"Unexpected JSON response from {host}") from exc

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
        elif host == "fund.eastmoney.com":
            headers["Accept"] = "application/javascript,text/javascript,*/*;q=0.8"
            headers["Referer"] = "https://fund.eastmoney.com/"
        elif host == "datacenter-web.eastmoney.com":
            headers["Referer"] = "https://data.eastmoney.com/"
        elif host == "searchapi.eastmoney.com":
            headers["Referer"] = "https://quote.eastmoney.com/"
        return headers


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
    )


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
