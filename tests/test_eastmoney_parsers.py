from __future__ import annotations

from market_lens.data.eastmoney import (
    build_index_search_keywords,
    build_search_keywords,
    f10_stock_code,
    infer_exchange_fund_secid,
    infer_secid,
    is_a_share_symbol,
    parse_asset_search_row,
    parse_cash_per_share,
    parse_fund_archives_content,
    parse_fund_holdings_table,
    parse_fund_nav_table,
    parse_pingzhongdata_fund_name,
    parse_stock_dividend_plan,
    parse_stock_financial_indicator,
    parse_stock_kline,
    parse_stock_peer_comparison,
    parse_stock_profile,
    rank_search_results,
    repair_mojibake,
)


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
