from __future__ import annotations

from datetime import date

from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    build_index_search_keywords,
    build_search_keywords,
    f10_stock_code,
    infer_exchange_fund_secid,
    infer_secid,
    is_a_share_symbol,
    parse_asset_search_row,
    parse_cash_per_share,
    parse_csi_index_top_holdings,
    parse_fund_archives_content,
    parse_fund_holdings_table,
    parse_fund_nav_row,
    parse_fund_nav_table,
    parse_fund_position_payload,
    parse_fund_tracking_info,
    parse_pingzhongdata_fund_name,
    parse_stock_dividend_plan,
    parse_stock_financial_indicator,
    parse_stock_kline,
    parse_stock_peer_comparison,
    parse_stock_profile,
    rank_search_results,
    repair_mojibake,
)
from market_lens.types import FundHolding, FundTrackingInfo


def test_infer_secid() -> None:
    assert infer_secid("600519") == "1.600519"
    assert infer_secid("000001") == "0.000001"
    assert not is_a_share_symbol("019670")
    assert f10_stock_code("600519") == "SH600519"
    assert f10_stock_code("000001") == "SZ000001"


def test_infer_exchange_fund_secid() -> None:
    assert infer_exchange_fund_secid("515450") == "1.515450"
    assert infer_exchange_fund_secid("159525") == "0.159525"
    assert infer_exchange_fund_secid("019670") is None


def test_parse_stock_kline() -> None:
    row = parse_stock_kline("2026-07-02,10.1,10.2,10.5,10.0,1000,2000,5.0,1.2,0.12,3.4")
    assert row.date.isoformat() == "2026-07-02"
    assert row.close == 10.2
    assert row.turnover_pct == 3.4


def test_parse_stock_profile() -> None:
    row = parse_stock_profile(
        {
            "SECURITY_CODE": "600519",
            "SECURITY_NAME_ABBR": "Kweichow",
            "EM2016": "Food-Beverage",
            "INDUSTRYCSRC1": "Manufacturing",
            "SECURITY_TYPE": "A-share",
        }
    )

    assert row.code == "600519"
    assert row.em_industry == "Food-Beverage"
    assert row.csrc_industry == "Manufacturing"


def test_parse_stock_financial_indicator() -> None:
    row = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "REPORT_TYPE": "annual",
            "ROEJQ": "32.53",
            "ROEKCJQ": "32.52",
            "PARENTNETPROFITTZ": "-4.53",
            "TOTALOPERATEREVETZ": "-1.21",
            "XSMLL": "91.18",
            "XSJLL": "50.53",
        }
    )

    assert row.date.isoformat() == "2025-12-31"
    assert row.roe_weighted == 32.53
    assert row.parent_netprofit_growth_pct == -4.53


def test_parse_stock_peer_comparison() -> None:
    row = parse_stock_peer_comparison(
        {
            "CORRE_SECURITY_CODE": "600519",
            "CORRE_SECURITY_NAME": "Kweichow",
            "PAIMING": 2,
            "PE_TTM": 17.86,
            "PB_MRQ": 5.45,
            "PEG": 1.72,
            "ROE_AVG": 36.36,
            "JLRTTM": -7.09,
            "YYSRTTM": -2.04,
        }
    )

    assert row is not None
    assert row.code == "600519"
    assert row.pe_ttm == 17.86
    assert row.roe_avg == 36.36
    assert parse_stock_peer_comparison({"CORRE_SECURITY_NAME": "行业平均"}) is None


def test_parse_stock_dividend_plan() -> None:
    assert parse_cash_per_share("10派280.2423元") == 28.02423
    row = parse_stock_dividend_plan(
        {
            "NOTICE_DATE": "2026-06-22 00:00:00",
            "IMPL_PLAN_PROFILE": "10派280.2423元",
            "ASSIGN_PROGRESS": "implemented",
            "EX_DIVIDEND_DATE": "2026-06-26 00:00:00",
        }
    )

    assert row.cash_per_share == 28.02423
    assert row.ex_dividend_date is not None


def test_parse_fund_nav_table() -> None:
    html = """
    <table><tbody>
      <tr><td>2026-07-02</td><td>1.2345</td><td>2.3456</td><td>0.88%</td><td>开放申购</td><td>开放赎回</td></tr>
    </tbody></table>
    """
    rows = parse_fund_nav_table(html)
    assert len(rows) == 1
    assert rows[0].unit_nav == 1.2345
    assert rows[0].daily_growth_pct == 0.88


def test_parse_fund_nav_row() -> None:
    row = parse_fund_nav_row(
        {
            "FSRQ": "2026-07-20",
            "DWJZ": "1.0241",
            "LJJZ": "1.0241",
            "JZZZL": "0.42",
            "SGZT": "开放申购",
            "SHZT": "开放赎回",
        }
    )

    assert row is not None
    assert row.date.isoformat() == "2026-07-20"
    assert row.unit_nav == 1.0241
    assert row.daily_growth_pct == 0.42
    assert parse_fund_nav_row({"FSRQ": ""}) is None


def test_parse_fund_tracking_and_target_etf() -> None:
    tracking = parse_fund_tracking_info(
        {
            "Datas": {
                "FCODE": "025856",
                "SHORTNAME": "华夏中证电网设备主题ETF发起式联接A",
                "FTYPE": "指数型-股票",
                "INDEXCODE": "931994",
                "INDEXNAME": "中证电网设备主题指数",
            },
            "ErrCode": 0,
        }
    )
    position = parse_fund_position_payload(
        {
            "Datas": {
                "ETFCODE": "159326",
                "ETFSHORTNAME": "电网设备ETF华夏",
                "fundStocks": [
                    {"GPDM": "600089", "GPJC": "特变电工", "JZBL": "0.25"}
                ],
            },
            "ErrCode": 0,
            "Expansion": "2026-06-30",
        }
    )

    assert tracking.index_code == "931994"
    assert tracking.index_name == "中证电网设备主题指数"
    assert position["target_etf_code"] == "159326"
    assert position["holdings"][0].weight_pct == 0.25
    assert position["report_date"].isoformat() == "2026-06-30"


def test_parse_csi_index_top_holdings() -> None:
    rows = parse_csi_index_top_holdings(
        {
            "code": "200",
            "data": {
                "updateDate": "2026-07-20",
                "weightList": [
                    {
                        "rowNum": "1",
                        "securityCode": "600487",
                        "securityName": "亨通光电",
                        "weight": 10.47,
                        "preciseWeight": 10.472584,
                    }
                ],
            },
        }
    )

    assert len(rows) == 1
    assert rows[0].name == "亨通光电"
    assert rows[0].weight_pct == 10.472584
    assert rows[0].report_date == date(2026, 7, 20)


def test_fund_holdings_route_prefers_official_tracked_index(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    tracking = FundTrackingInfo(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF发起式联接A",
        fund_type="指数型-股票",
        index_code="931994",
        index_name="中证电网设备主题指数",
        target_etf_code="159326",
        target_etf_name="电网设备ETF华夏",
    )
    official = [
        FundHolding(1, "600487", "亨通光电", 10.47, None, None, date(2026, 7, 20))
    ]
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(client, "get_csi_index_top_holdings", lambda code, top_n: official)

    def fail_if_direct_holdings_are_used(code: str, top_n: int = 10):
        raise AssertionError(f"direct holdings must not be used for {code}, top_n={top_n}")

    monkeypatch.setattr(client, "get_fund_holdings", fail_if_direct_holdings_are_used)
    route = client.get_fund_holdings_route("025856")

    assert route.scope == "tracked_index_top10"
    assert route.source == "csindex_official"
    assert route.coverage == 0.1047
    assert route.holdings == official


def test_fund_holdings_route_falls_back_to_target_etf(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    tracking = FundTrackingInfo(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF发起式联接A",
        fund_type="指数型-股票",
        index_code="931994",
        index_name="中证电网设备主题指数",
        target_etf_code="159326",
        target_etf_name="电网设备ETF华夏",
    )
    target_etf = [
        FundHolding(1, "600487", "亨通光电", 15.01, None, None, date(2026, 6, 30))
    ]
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)

    def fail_official_index(code: str, top_n: int = 10):
        raise EastmoneyError(f"official index unavailable: {code}, top_n={top_n}")

    monkeypatch.setattr(client, "get_csi_index_top_holdings", fail_official_index)
    monkeypatch.setattr(
        client,
        "get_fund_holdings",
        lambda code, top_n=10: target_etf if code == "159326" else [],
    )
    route = client.get_fund_holdings_route("025856")

    assert route.scope == "target_etf_top10"
    assert route.coverage == 0.1501
    assert route.holdings == target_etf
    assert route.fallback_reasons[0].startswith("official_index_holdings_unavailable")


def test_fund_holdings_route_uses_direct_holdings_for_active_fund(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    tracking = FundTrackingInfo(
        fund_code="000001",
        fund_name="示例主动股票基金",
        fund_type="股票型",
        index_code=None,
        index_name=None,
        target_etf_code=None,
        target_etf_name=None,
    )
    direct = [
        FundHolding(1, "600519", "贵州茅台", 8.5, None, None, date(2026, 6, 30))
    ]
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(client, "get_fund_holdings", lambda code, top_n=10: direct)

    route = client.get_fund_holdings_route("000001", fund_name=tracking.fund_name)

    assert route.scope == "fund_direct_top10"
    assert route.source == "eastmoney_fund_disclosure"
    assert route.coverage == 0.085
    assert route.holdings == direct


def test_fund_holdings_route_fails_closed_for_unresolved_index_fund(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)

    def fail_tracking(code: str):
        raise EastmoneyError(f"tracking unavailable: {code}")

    def fail_if_direct_holdings_are_used(code: str, top_n: int = 10):
        raise AssertionError(f"direct holdings must not be used for {code}, top_n={top_n}")

    monkeypatch.setattr(client, "get_fund_tracking_info", fail_tracking)
    monkeypatch.setattr(client, "get_fund_holdings", fail_if_direct_holdings_are_used)
    route = client.get_fund_holdings_route(
        "025856",
        fund_name="华夏中证电网设备主题ETF发起式联接A",
    )

    assert route.scope == "unresolved_index_fund"
    assert route.source == "unavailable"
    assert route.coverage == 0.0
    assert route.holdings == []


def test_validated_json_evicts_business_errors_before_retry(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    client.retries = 1
    responses = iter(
        [
            {"ErrCode": 61136, "ErrMsg": "busy"},
            {"ErrCode": 0, "Datas": {"FCODE": "025856"}},
        ]
    )

    class FakeCache:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete(self, url: str) -> None:
            self.deleted.append(url)

    client.cache = FakeCache()
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: next(responses))
    monkeypatch.setattr("market_lens.data.eastmoney.time.sleep", lambda seconds: None)

    payload = client._get_validated_json(
        "https://example.test/data",
        ttl_seconds=60,
        is_success=lambda value: value.get("ErrCode") == 0,
    )

    assert payload["ErrCode"] == 0
    assert client.cache.deleted == ["https://example.test/data"]


def test_mobile_fund_api_uses_compatible_user_agent() -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    client.headers = {"User-Agent": "full browser user agent"}

    headers = client._headers_for_url("https://fundmobapi.eastmoney.com/example")

    assert headers["User-Agent"] == "Mozilla/5.0"


def test_get_fund_nav_uses_json_pagination(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    requested_urls: list[str] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict[str, object]:
        requested_urls.append(url)
        page = 2 if "pageIndex=2" in url else 1
        return {
            "Data": {
                "LSJZList": [
                    {
                        "FSRQ": f"2026-07-{21 - page:02d}",
                        "DWJZ": str(1 + page / 100),
                        "LJJZ": str(1 + page / 100),
                        "JZZZL": "0.1",
                        "SGZT": "开放申购",
                        "SHZT": "开放赎回",
                    }
                ]
            },
            "ErrCode": 0,
            "TotalCount": 2,
            "PageSize": 1,
            "PageIndex": page,
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_fund_nav(
        "025856",
        start=date(2026, 7, 1),
        end=date(2026, 7, 21),
        page_size=1,
    )

    assert [row.date.isoformat() for row in rows] == ["2026-07-19", "2026-07-20"]
    assert len(requested_urls) == 2
    assert all("api.fund.eastmoney.com/f10/lsjz" in url for url in requested_urls)
    assert all("fundCode=025856" in url for url in requested_urls)


def test_parse_fund_holdings_table() -> None:
    response = """var apidata={ content:"<div><h4>测试基金 截止至：<font>2026-03-31</font></h4>
    <table><tbody><tr><td>1</td><td><a>000651</a></td><td><a>格力电器</a></td>
    <td></td><td></td><td>资讯</td><td>3.48%</td><td>4,321.50</td><td>56,789.10</td>
    </tr></tbody></table></div>",arryear:[2026],curyear:2026};"""

    rows = parse_fund_holdings_table(parse_fund_archives_content(response))

    assert len(rows) == 1
    assert rows[0].code == "000651"
    assert rows[0].name == "格力电器"
    assert rows[0].weight_pct == 3.48
    assert rows[0].shares_10k == 4321.5
    assert rows[0].market_value_10k == 56789.1
    assert rows[0].report_date is not None
    assert rows[0].report_date.isoformat() == "2026-03-31"


def test_parse_pingzhongdata_fund_name() -> None:
    text = 'var fS_name = "招商中证白酒指数(LOF)A"; var fS_code = "161725";'
    assert parse_pingzhongdata_fund_name(text) == "招商中证白酒指数(LOF)A"


def test_repair_mojibake() -> None:
    assert repair_mojibake("è´µå·\u009eè\u008c\u0085å\u008f°") == "贵州茅台"
    assert repair_mojibake("贵州茅台") == "贵州茅台"


def test_parse_stock_search_row() -> None:
    row = {
        "Code": "600519",
        "Name": "贵州茅台",
        "Classify": "AStock",
        "SecurityTypeName": "沪A",
        "QuoteID": "1.600519",
        "UnifiedCode": "600519",
    }

    result = parse_asset_search_row(row)

    assert result is not None
    assert result.asset_type == "stock"
    assert result.code == "600519"
    assert result.name == "贵州茅台"


def test_parse_fund_search_row() -> None:
    row = {
        "Code": "019670",
        "Name": "广发港股创新药ETF联接(QDII)A",
        "Classify": "OTCFUND",
        "SecurityTypeName": "基金",
        "QuoteID": "150.019670",
        "UnifiedCode": "019670",
    }

    result = parse_asset_search_row(row)

    assert result is not None
    assert result.asset_type == "fund"
    assert result.code == "019670"
    assert result.name == "广发港股创新药ETF联接(QDII)A"


def test_parse_search_row_ignores_unsupported_assets() -> None:
    row = {
        "Code": "BK0896",
        "Name": "白酒",
        "Classify": "BK",
        "SecurityTypeName": "板块",
        "UnifiedCode": "BK0896",
    }

    assert parse_asset_search_row(row) is None


def test_parse_index_search_row() -> None:
    row = {
        "Code": "H30269",
        "Name": "红利低波",
        "Classify": "24",
        "SecurityTypeName": "指数",
        "QuoteID": "2.H30269",
        "UnifiedCode": "H30269",
    }

    result = parse_asset_search_row(row)

    assert result is not None
    assert result.asset_type == "index"
    assert result.code == "H30269"
    assert result.quote_id == "2.H30269"


def test_build_search_keywords_relaxes_fund_manager_prefix() -> None:
    assert build_search_keywords("南方红利低波") == ["南方红利低波", "红利低波", "南方"]


def test_build_index_search_keywords_from_etf_name() -> None:
    assert build_index_search_keywords("红利低波50ETF南方") == [
        "红利低波50ETF南方",
        "红利低波50",
        "红利低波",
    ]


def test_rank_search_results_prefers_manager_and_theme_match() -> None:
    rows = [
        {
            "Code": "159525",
            "Name": "红利低波ETF富国",
            "Classify": "Fund",
            "SecurityTypeName": "基金",
            "UnifiedCode": "159525",
        },
        {
            "Code": "515450",
            "Name": "红利低波50ETF南方",
            "Classify": "Fund",
            "SecurityTypeName": "基金",
            "UnifiedCode": "515450",
        },
    ]
    results = [result for row in rows if (result := parse_asset_search_row(row)) is not None]

    ranked = rank_search_results("南方红利低波", results)

    assert ranked[0].code == "515450"
