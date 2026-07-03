from __future__ import annotations

from market_lens.data.eastmoney import (
    build_index_search_keywords,
    build_search_keywords,
    infer_exchange_fund_secid,
    infer_secid,
    is_a_share_symbol,
    parse_asset_search_row,
    parse_fund_nav_table,
    parse_pingzhongdata_fund_name,
    parse_stock_kline,
    rank_search_results,
    repair_mojibake,
)


def test_infer_secid() -> None:
    assert infer_secid("600519") == "1.600519"
    assert infer_secid("000001") == "0.000001"
    assert not is_a_share_symbol("019670")


def test_infer_exchange_fund_secid() -> None:
    assert infer_exchange_fund_secid("515450") == "1.515450"
    assert infer_exchange_fund_secid("159525") == "0.159525"
    assert infer_exchange_fund_secid("019670") is None


def test_parse_stock_kline() -> None:
    row = parse_stock_kline("2026-07-02,10.1,10.2,10.5,10.0,1000,2000,5.0,1.2,0.12,3.4")
    assert row.date.isoformat() == "2026-07-02"
    assert row.close == 10.2
    assert row.turnover_pct == 3.4


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
