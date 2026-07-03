from __future__ import annotations

from market_lens.data.eastmoney import (
    infer_secid,
    is_a_share_symbol,
    parse_asset_search_row,
    parse_fund_nav_table,
    parse_pingzhongdata_fund_name,
    parse_stock_kline,
    repair_mojibake,
)


def test_infer_secid() -> None:
    assert infer_secid("600519") == "1.600519"
    assert infer_secid("000001") == "0.000001"
    assert not is_a_share_symbol("019670")


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
