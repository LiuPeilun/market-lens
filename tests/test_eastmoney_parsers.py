from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import pytest

from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    build_fund_holdings_snapshot,
    build_index_search_keywords,
    build_search_keywords,
    build_top10_from_snapshot,
    f10_stock_code,
    infer_exchange_fund_secid,
    infer_secid,
    is_a_share_symbol,
    merge_csi_index_valuation_points,
    parse_asset_search_row,
    parse_cash_per_share,
    parse_cnindex_index_identity,
    parse_cnindex_index_top_holdings,
    parse_cnindex_related_index_products,
    parse_csi_index_indicator_rows,
    parse_csi_index_pe_ttm_history,
    parse_csi_index_top_holdings,
    parse_csi_index_weight_rows,
    parse_fund_archives_content,
    parse_fund_asset_allocation_page,
    parse_fund_holdings_sections,
    parse_fund_holdings_table,
    parse_fund_nav_row,
    parse_fund_nav_table,
    parse_fund_position_payload,
    parse_fund_product_info,
    parse_fund_tracking_info,
    parse_pingzhongdata_fund_name,
    parse_pingzhongdata_fund_nav,
    parse_sina_index_history,
    parse_stock_dividend_plan,
    parse_stock_financial_indicator,
    parse_stock_kline,
    parse_stock_peer_comparison,
    parse_stock_profile,
    parse_stock_valuation_row,
    parse_tencent_qfq_history,
    rank_search_results,
    repair_mojibake,
    stock_history_year_chunks,
    supports_csi_official_index_code,
    tencent_stock_symbol,
)
from market_lens.data.index_providers import (
    official_index_constituent_provider,
    official_index_provider_capabilities,
    official_index_valuation_provider,
)
from market_lens.types import (
    CsiIndexConstituentWeight,
    FundAssetAllocation,
    FundHolding,
    FundTrackingInfo,
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


def test_csi_provider_rejects_known_szse_index_codes_before_request() -> None:
    assert supports_csi_official_index_code("000300") is True
    assert supports_csi_official_index_code("931994") is True
    assert supports_csi_official_index_code("399006") is False

    client = EastmoneyClient.__new__(EastmoneyClient)
    with pytest.raises(ValueError, match="does not cover index 399006"):
        client.get_csi_index_valuation_history("399006")
    with pytest.raises(ValueError, match="does not cover index 399006"):
        client.get_csi_index_full_weights("399006")


def test_official_index_provider_capabilities_are_explicit() -> None:
    assert official_index_constituent_provider("399006") == "cnindex"
    assert official_index_valuation_provider("399006") is None
    assert official_index_constituent_provider("000300") == "csindex"
    assert official_index_valuation_provider("000300") == "csindex"

    cnindex = official_index_provider_capabilities("399006")
    assert cnindex.official_identity is True
    assert cnindex.top_constituent_weights is True
    assert cnindex.full_constituent_weights is False
    assert cnindex.valuation_history is False


def test_parse_cnindex_identity_and_top_holdings() -> None:
    identity = parse_cnindex_index_identity(
        {
            "code": 200,
            "data": {
                "indexcode": "399006",
                "indexname": "创业板指",
                "indexfullcname": "创业板指数",
            },
        },
        expected_code="399006",
        expected_name="创业板指数(价格)",
    )
    assert identity == {
        "index_code": "399006",
        "index_name": "创业板指数",
        "source": "cnindex_official",
    }

    rows = [
        {
            "dateStr": "2026-07-29",
            "seccode": f"300{index:03d}",
            "secname": f"样本{index}",
            "weight": str(11 - index),
        }
        for index in range(1, 11)
    ]
    rows.append(
        {
            "dateStr": "2026-07-29",
            "seccode": "300999",
            "secname": "未披露权重",
            "weight": "--",
        }
    )
    holdings = parse_cnindex_index_top_holdings(
        {"code": 200, "data": {"total": 100, "rows": rows}},
        expected_code="399006",
        expected_name=identity["index_name"],
    )

    assert len(holdings) == 10
    assert holdings[0].code == "300001"
    assert holdings[0].weight_pct == 10.0
    assert holdings[-1].weight_pct == 1.0
    assert {item.report_date for item in holdings} == {date(2026, 7, 29)}


def test_cnindex_parser_rejects_identity_mismatch_and_incomplete_weights() -> None:
    with pytest.raises(EastmoneyError, match="name mismatch"):
        parse_cnindex_index_identity(
            {
                "code": 200,
                "data": {
                    "indexcode": "399006",
                    "indexfullcname": "深证成份指数",
                },
            },
            expected_code="399006",
            expected_name="创业板指数",
        )

    with pytest.raises(EastmoneyError, match="expected 10, got 1"):
        parse_cnindex_index_top_holdings(
            {
                "code": 200,
                "data": {
                    "rows": [
                        {
                            "dateStr": "2026-07-29",
                            "seccode": "300750",
                            "secname": "宁德时代",
                            "weight": "17.6",
                        },
                        {
                            "dateStr": "2026-07-29",
                            "seccode": "300308",
                            "secname": "中际旭创",
                            "weight": "--",
                        },
                    ]
                },
            },
            expected_code="399006",
            expected_name="创业板指数",
        )


def test_parse_cnindex_related_products_validates_index_mapping() -> None:
    products = parse_cnindex_related_index_products(
        {
            "code": 200,
            "data": {
                "rows": [
                    {
                        "fundCode": "110026",
                        "fundName": "易方达创业板ETF联接A",
                        "fundType": "ETF联接",
                        "fundIndexCode": "399006.SZ",
                        "dateStr": "2026-06-30 00:00:00",
                    },
                    {
                        "fundCode": "159915",
                        "fundName": "易方达创业板ETF",
                        "fundType": "ETF",
                        "fundIndexCode": "399006.SZ",
                        "dateStr": "2026-06-30 00:00:00",
                    },
                    {
                        "fundCode": "CNXT",
                        "fundName": "VanEck ChiNext ETF",
                        "fundType": "境外基金",
                        "fundIndexCode": "399006.SZ",
                        "dateStr": "2026-06-30 00:00:00",
                    },
                ]
            },
        },
        expected_index_code="399006",
    )

    assert products["110026"]["index_code"] == "399006"
    assert products["159915"]["fund_type"] == "ETF"
    assert products["159915"]["source_as_of"] == "2026-06-30"

    with pytest.raises(EastmoneyError, match="identity mismatch"):
        parse_cnindex_related_index_products(
            {
                "code": 200,
                "data": {
                    "rows": [
                        {
                            "fundCode": "159915",
                            "fundName": "错误映射",
                            "fundType": "ETF",
                            "fundIndexCode": "399001.SZ",
                        }
                    ]
                },
            },
            expected_index_code="399006",
        )


def test_parse_stock_kline() -> None:
    row = parse_stock_kline("2026-07-02,10.1,10.2,10.5,10.0,1000,2000,5.0,1.2,0.12,3.4")
    assert row.date.isoformat() == "2026-07-02"
    assert row.close == 10.2
    assert row.turnover_pct == 3.4


def test_parse_sina_index_history() -> None:
    rows = parse_sina_index_history(
        'var _data=([{"day":"2026-07-20","open":"4500.0",'
        '"high":"4600.0","low":"4490.0","close":"4598.3",'
        '"volume":"33311605800"}]);'
    )

    assert len(rows) == 1
    assert rows[0].date == date(2026, 7, 20)
    assert rows[0].close == 4598.3
    assert parse_sina_index_history("var _data=(null);") == []


def test_parse_tencent_qfq_history() -> None:
    rows = parse_tencent_qfq_history(
        {
            "code": 0,
            "data": {
                "sz000069": {
                    "qfqday": [
                        [
                            "2020-07-22",
                            "7.260",
                            "7.080",
                            "7.350",
                            "7.010",
                            "1061437.000",
                            {"nd": "2020-07-22"},
                        ]
                    ]
                }
            },
        },
        "sz000069",
    )

    assert rows[0].date == date(2020, 7, 22)
    assert rows[0].open == 7.26
    assert rows[0].close == 7.08
    assert rows[0].volume == 1061437.0


def test_tencent_stock_history_uses_year_chunks_and_deduplicates(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    requested_urls: list[str] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict[str, object]:
        del ttl_seconds
        decoded = unquote(url)
        requested_urls.append(decoded)
        row_date = "2020-12-31" if "2020-12-31" in decoded else "2021-01-04"
        return {
            "code": 0,
            "data": {"sz000069": {"qfqday": [[row_date, "7.0", "7.1", "7.2", "6.9", "1000"]]}},
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_tencent_stock_history("000069", date(2020, 12, 1), date(2021, 1, 31))

    assert tencent_stock_symbol("000069") == "sz000069"
    assert stock_history_year_chunks(date(2020, 12, 1), date(2021, 1, 31)) == [
        (date(2020, 12, 1), date(2020, 12, 31)),
        (date(2021, 1, 1), date(2021, 1, 31)),
    ]
    assert [row.date for row in rows] == [date(2020, 12, 31), date(2021, 1, 4)]
    assert rows[1].change_amount == 0.0
    assert len(requested_urls) == 2
    assert "sz000069,day,2020-12-01,2020-12-31,400,qfq" in requested_urls[0]
    assert "sz000069,day,2021-01-01,2021-01-31,400,qfq" in requested_urls[1]


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


def test_parse_stock_valuation_row_includes_industry_board() -> None:
    row = parse_stock_valuation_row(
        {
            "TRADE_DATE": "2026-07-20 00:00:00",
            "SECURITY_CODE": "600519",
            "SECURITY_NAME_ABBR": "贵州茅台",
            "PE_TTM": 20.06,
            "PB_MRQ": 6.13,
            "BOARD_CODE": "016165",
            "BOARD_NAME": "白酒Ⅱ",
            "ORIG_BOARD_CODE": "1277",
        }
    )

    assert row.board_code == "016165"
    assert row.board_name == "白酒Ⅱ"
    assert row.original_board_code == "1277"


def test_get_stock_industry_valuation_snapshot_paginates(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    requested_urls: list[str] = []

    def raw_row(code: str, pe: float, pb: float) -> dict[str, object]:
        return {
            "TRADE_DATE": "2026-07-20 00:00:00",
            "SECURITY_CODE": code,
            "SECURITY_NAME_ABBR": code,
            "PE_TTM": pe,
            "PB_MRQ": pb,
            "BOARD_CODE": "016165",
            "BOARD_NAME": "白酒Ⅱ",
            "ORIG_BOARD_CODE": "1277",
        }

    def fake_get_json(url: str, ttl_seconds: int) -> dict[str, object]:
        requested_urls.append(unquote(url))
        page = 2 if "pageNumber=2" in url else 1
        return {
            "success": True,
            "result": {
                "pages": 2,
                "data": [raw_row("600519" if page == 1 else "000858", 20 + page, 6 + page)],
            },
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    snapshot = client.get_stock_industry_valuation_snapshot(
        "016165",
        date(2026, 7, 20),
        board_name="白酒Ⅱ",
        page_size=1,
    )

    assert [row.code for row in snapshot.rows] == ["000858", "600519"]
    assert snapshot.original_board_code == "1277"
    assert len(requested_urls) == 2
    assert all('(BOARD_CODE="016165")' in url for url in requested_urls)
    assert all("(TRADE_DATE='2026-07-20')" in url for url in requested_urls)


def test_get_stock_industry_valuation_snapshot_rejects_mismatched_rows(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url, ttl_seconds: {
            "success": True,
            "result": {
                "pages": 1,
                "data": [
                    {
                        "TRADE_DATE": "2026-07-20 00:00:00",
                        "SECURITY_CODE": "600519",
                        "BOARD_CODE": "wrong-board",
                    }
                ],
            },
        },
    )

    try:
        client.get_stock_industry_valuation_snapshot("016165", date(2026, 7, 20))
    except EastmoneyError as exc:
        assert "did not match" in str(exc)
    else:
        raise AssertionError("mismatched industry rows must fail closed")


def test_parse_stock_financial_indicator() -> None:
    row = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "REPORT_TYPE": "annual",
            "NOTICE_DATE": "2026-04-17 00:00:00",
            "UPDATE_DATE": "2026-04-18 00:00:00",
            "ORG_TYPE": "通用",
            "ROEJQ": "32.53",
            "ROEKCJQ": "32.52",
            "PARENTNETPROFITTZ": "-4.53",
            "TOTALOPERATEREVETZ": "-1.21",
            "XSMLL": "91.18",
            "XSJLL": "50.53",
            "ROIC": "31.42",
            "FCFF_BACK": "76139290546.69",
            "FCFF_FORWARD": "68581654498.07",
        }
    )

    assert row.date.isoformat() == "2025-12-31"
    assert row.roe_weighted == 32.53
    assert row.parent_netprofit_growth_pct == -4.53
    assert row.org_type == "通用"
    assert row.notice_date == date(2026, 4, 17)
    assert row.roic_pct == 31.42
    assert row.fcff_backward_cny == 76139290546.69


def test_parse_stock_financial_industry_fields() -> None:
    bank = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31",
            "ORG_TYPE": "银行",
            "NET_INTEREST_MARGIN": "1.87",
            "NONPERLOAN": "0.94",
            "BLDKBBL": "391.79",
            "NEWCAPITALADER": "18.24",
        }
    )
    insurance = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31",
            "ORG_TYPE": "保险",
            "SOLVENCY_AR": "193.3",
            "NBV_LIFE": "36897000000",
            "NBV_RATE": "28.5",
        }
    )
    securities = parse_stock_financial_indicator(
        {
            "REPORT_DATE": "2025-12-31",
            "ORG_TYPE": "证券",
            "RISK_COVERAGE": "210.46",
            "LIQUIDITY_COVERAGE_RATIO": "137.8",
            "NET_FUNDING_RATIO": "125.27",
            "JZBJZC": "61.26",
        }
    )

    assert bank.net_interest_margin_pct == 1.87
    assert bank.provision_coverage_ratio_pct == 391.79
    assert insurance.solvency_adequacy_ratio_pct == 193.3
    assert insurance.new_business_value_cny == 36897000000.0
    assert securities.risk_coverage_ratio_pct == 210.46
    assert securities.net_capital_to_net_assets_pct == 61.26


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
                "fundStocks": [{"GPDM": "600089", "GPJC": "特变电工", "JZBL": "0.25"}],
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


def test_parse_fund_tracking_treats_placeholder_index_as_missing() -> None:
    tracking = parse_fund_tracking_info(
        {
            "Datas": {
                "FCODE": "000001",
                "SHORTNAME": "华夏成长混合",
                "FTYPE": "混合型-灵活",
                "INDEXCODE": "--",
                "INDEXNAME": "--",
            },
            "ErrCode": 0,
        }
    )

    assert tracking.index_code is None
    assert tracking.index_name is None


def test_parse_fund_product_info() -> None:
    product = parse_fund_product_info(
        {
            "Datas": {
                "FCODE": "510300",
                "SHORTNAME": "沪深300ETF华泰柏瑞",
                "FTYPE": "指数型-股票",
                "ESTABDATE": "2012-05-04",
                "FEGMRQ": "2026-06-30",
                "ENDNAV": "94872183996.4",
                "MGREXP": "0.15%",
                "TRUSTEXP": "0.05%",
                "SALESEXP": "--",
                "PERFCMP": "沪深300指数",
            },
            "ErrCode": 0,
        }
    )

    assert product.fund_code == "510300"
    assert product.scale_report_date == date(2026, 6, 30)
    assert product.period_end_net_assets_cny == 94872183996.4
    assert product.management_fee_pct == 0.15
    assert product.custody_fee_pct == 0.05
    assert product.sales_service_fee_pct is None


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


def test_parse_and_merge_csi_index_valuation_points() -> None:
    pe_points = parse_csi_index_pe_ttm_history(
        {
            "code": "200",
            "data": [
                {
                    "tradeDate": "20260723",
                    "indexName": "CSI 300",
                    "indexNameEn": "CSI 300",
                    "peg": 14.59,
                },
                {
                    "tradeDate": "20260724",
                    "indexName": "CSI 300",
                    "indexNameEn": "CSI 300",
                    "peg": 14.39,
                },
            ],
        },
        expected_code="000300",
        expected_names={"CSI 300"},
    )
    indicator_points = parse_csi_index_indicator_rows(
        [
            [
                "Date",
                "Index Code",
                "Chinese Name",
                "Index Chinese Name",
                "English Name",
                "Index English Name",
                "P/E1",
                "P/E2",
                "D/P1",
                "D/P2",
            ],
            [
                20260723.0,
                "000300",
                "CSI 300",
                "CSI 300",
                "CSI 300 Index",
                "CSI 300",
                14.92,
                17.53,
                2.63,
                2.22,
            ],
            [
                20260724.0,
                "000300",
                "CSI 300",
                "CSI 300",
                "CSI 300 Index",
                "CSI 300",
                14.72,
                17.24,
                2.67,
                2.26,
            ],
        ],
        expected_code="000300",
        expected_names={"CSI 300"},
    )

    points = merge_csi_index_valuation_points(pe_points, indicator_points)

    assert len(points) == 2
    assert points[-1].date == date(2026, 7, 24)
    assert points[-1].pe_ttm == 14.39
    assert points[-1].pe_static_total_capital == 14.72
    assert points[-1].dividend_yield_total_capital_pct == 2.67
    assert points[-1].pb is None
    assert points[-1].scoring_eligible is False


def test_parse_csi_index_pe_history_rejects_name_mismatch() -> None:
    with pytest.raises(EastmoneyError, match="name mismatch"):
        parse_csi_index_pe_ttm_history(
            {
                "code": "200",
                "data": [
                    {
                        "tradeDate": "20260724",
                        "indexName": "CSI 500",
                        "peg": 26.82,
                    }
                ],
            },
            expected_code="000300",
            expected_names={"CSI 300"},
        )


def test_parse_csi_index_weights_requires_complete_weight_sum() -> None:
    header = [
        "Date",
        "Index Code",
        "Index Name",
        "Index Name(Eng)",
        "Constituent Code",
        "Constituent Name",
        "Constituent Name(Eng)",
        "Exchange",
        "Exchange(Eng)",
        "weight",
    ]
    rows = parse_csi_index_weight_rows(
        [
            header,
            [
                20260630.0,
                "931151",
                "Photovoltaic Industry",
                "Photovoltaic Industry",
                "000591",
                "Solar Energy",
                "Solar Energy",
                "SZSE",
                "SZSE",
                40.0,
            ],
            [
                20260630.0,
                "931151",
                "Photovoltaic Industry",
                "Photovoltaic Industry",
                "688599",
                "Trina Solar",
                "Trina Solar",
                "SSE",
                "SSE",
                60.0,
            ],
        ],
        expected_code="931151",
        expected_names={"Photovoltaic Industry"},
    )

    assert len(rows) == 2
    assert rows[0].report_date == date(2026, 6, 30)
    assert sum(item.weight_pct for item in rows) == 100.0
    assert rows[0].scoring_eligible is False

    with pytest.raises(EastmoneyError, match="sum to"):
        parse_csi_index_weight_rows(
            [
                header,
                [
                    20260630.0,
                    "931151",
                    "Photovoltaic Industry",
                    "",
                    "000591",
                    "Solar Energy",
                    "",
                    "SZSE",
                    "",
                    10.0,
                ],
            ],
            expected_code="931151",
            expected_names={"Photovoltaic Industry"},
        )


def test_csi_index_material_url_is_identity_checked(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    monkeypatch.setattr(
        client,
        "_get_validated_json",
        lambda url, ttl_seconds, is_success: {
            "code": "200",
            "data": {
                "\u6307\u6570\u4f30\u503c": [
                    {
                        "fileName": "000300indicator",
                        "filePath": (
                            "https://oss-ch.csindex.com.cn/static/html/csindex/public/"
                            "uploads/file/autofile/indicator/000300indicator.xls"
                        ),
                        "fileType": "xls",
                    }
                ]
            },
        },
    )

    url = client._get_csi_index_material_url(
        "000300",
        category="\u6307\u6570\u4f30\u503c",
        suffix="indicator",
    )

    assert url.endswith("/000300indicator.xls")


def test_csi_index_material_url_rejects_untrusted_host(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    monkeypatch.setattr(
        client,
        "_get_validated_json",
        lambda url, ttl_seconds, is_success: {
            "code": "200",
            "data": {
                "\u6307\u6570\u4f30\u503c": [
                    {
                        "fileName": "000300indicator",
                        "filePath": "https://example.test/000300indicator.xls",
                        "fileType": "xls",
                    }
                ]
            },
        },
    )

    with pytest.raises(EastmoneyError, match="Unexpected CSI index material URL"):
        client._get_csi_index_material_url(
            "000300",
            category="\u6307\u6570\u4f30\u503c",
            suffix="indicator",
        )


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
        CsiIndexConstituentWeight(
            rank=1,
            report_date=date(2026, 6, 30),
            index_code="931994",
            index_name=tracking.index_name or "",
            security_code="600487",
            security_name="亨通光电",
            exchange="SSE",
            weight_pct=60.0,
        ),
        CsiIndexConstituentWeight(
            rank=2,
            report_date=date(2026, 6, 30),
            index_code="931994",
            index_name=tracking.index_name or "",
            security_code="000400",
            security_name="许继电气",
            exchange="SZSE",
            weight_pct=40.0,
        ),
    ]
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(client, "get_csi_index_full_weights", lambda code: official)

    def fail_if_top10_is_used(code: str, top_n: int = 10):
        raise AssertionError(f"top ten must not be used for {code}, top_n={top_n}")

    monkeypatch.setattr(client, "get_csi_index_top_holdings", fail_if_top10_is_used)
    route = client.get_fund_holdings_route(
        "025856",
        analysis_end=date(2026, 7, 24),
    )

    assert route.scope == "tracked_index_full_weights"
    assert route.source == "csindex_official"
    assert route.coverage == 1.0
    assert len(route.holdings) == 2
    assert route.full_disclosure is not None
    assert route.latest_top10 is not None


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
    target_top10 = build_fund_holdings_snapshot(
        [FundHolding(1, "600487", "亨通光电", 15.01, None, None, date(2026, 6, 30))],
        source="eastmoney_fund_disclosure",
        scope="target_etf_top10",
        equity_allocation_pct=99.9,
    )
    target_full = build_fund_holdings_snapshot(
        [
            FundHolding(1, "600487", "亨通光电", 55.0, None, None, date(2025, 12, 31)),
            FundHolding(2, "000400", "许继电气", 44.8, None, None, date(2025, 12, 31)),
        ],
        source="eastmoney_fund_disclosure",
        scope="target_etf_full_disclosure",
        equity_allocation_pct=99.9,
    )
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(
        client,
        "get_csi_index_full_weights",
        lambda code: (_ for _ in ()).throw(EastmoneyError(f"full unavailable: {code}")),
    )

    def fail_official_index(code: str, top_n: int = 10):
        raise EastmoneyError(f"official index unavailable: {code}, top_n={top_n}")

    monkeypatch.setattr(client, "get_csi_index_top_holdings", fail_official_index)
    monkeypatch.setattr(
        client,
        "get_fund_top10_snapshot",
        lambda code, source, scope: target_top10,
    )
    monkeypatch.setattr(
        client,
        "get_fund_full_holdings_snapshot",
        lambda code, as_of, scope: target_full,
    )
    route = client.get_fund_holdings_route(
        "025856",
        analysis_end=date(2026, 7, 24),
    )

    assert route.scope == "target_etf_full_disclosure"
    assert route.coverage == pytest.approx(0.999)
    assert route.holdings == target_full.holdings
    assert route.latest_top10 == target_top10
    assert route.fallback_reasons[0].startswith("official_index_full_weights_unavailable")


def test_fund_holdings_route_skips_csi_for_szse_index(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    tracking = FundTrackingInfo(
        fund_code="110026",
        fund_name="易方达创业板ETF联接A",
        fund_type="指数型-股票",
        index_code="399006",
        index_name="创业板指数(价格)",
        target_etf_code="159915",
        target_etf_name="创业板ETF易方达",
    )
    target_full = build_fund_holdings_snapshot(
        [
            FundHolding(1, "300750", "宁德时代", 60.0, None, None, date(2025, 12, 31)),
            FundHolding(2, "300308", "中际旭创", 39.8, None, None, date(2025, 12, 31)),
        ],
        source="eastmoney_fund_disclosure",
        scope="target_etf_full_disclosure",
        equity_allocation_pct=99.9,
    )
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(
        client,
        "get_csi_index_full_weights",
        lambda code: (_ for _ in ()).throw(
            AssertionError(f"CSI provider must not receive {code}")
        ),
    )
    monkeypatch.setattr(
        client,
        "get_csi_index_top_holdings",
        lambda code, top_n=10: (_ for _ in ()).throw(
            AssertionError(f"CSI provider must not receive {code}, top_n={top_n}")
        ),
    )
    official_top10 = [
        FundHolding(
            1,
            "300750",
            "宁德时代",
            17.6,
            None,
            None,
            date(2026, 7, 29),
        )
    ]
    monkeypatch.setattr(
        client,
        "get_cnindex_index_top_holdings",
        lambda code, expected_name, top_n, as_of: official_top10,
    )
    monkeypatch.setattr(
        client,
        "get_cnindex_related_index_products",
        lambda code: {
            "110026": {"fund_type": "ETF联接"},
            "159915": {"fund_type": "ETF"},
        },
    )
    monkeypatch.setattr(
        client,
        "get_fund_top10_snapshot",
        lambda code, source, scope: None,
    )
    monkeypatch.setattr(
        client,
        "get_fund_full_holdings_snapshot",
        lambda code, as_of, scope: target_full,
    )

    route = client.get_fund_holdings_route(
        "110026",
        analysis_end=date(2026, 7, 29),
    )

    assert route.scope == "target_etf_full_disclosure"
    assert route.holdings == target_full.holdings
    assert route.latest_top10 is not None
    assert route.latest_top10.source == "cnindex_official"
    assert route.validation == {
        "official_index_provider": "cnindex",
        "fund_mapping": "confirmed",
        "target_etf_mapping": "confirmed",
        "product_mapping_source_as_of": None,
        "index_identity": "confirmed",
    }
    assert route.fallback_reasons == ("official_index_full_weights_not_published",)


def test_fund_holdings_route_uses_cnindex_top10_after_target_etf_failure(
    monkeypatch,
) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    tracking = FundTrackingInfo(
        fund_code="110026",
        fund_name="易方达创业板ETF联接A",
        fund_type="指数型-股票",
        index_code="399006",
        index_name="创业板指数(价格)",
        target_etf_code="159915",
        target_etf_name="创业板ETF易方达",
    )
    official_top10 = [
        FundHolding(
            rank=index,
            code=f"300{index:03d}",
            name=f"样本{index}",
            weight_pct=float(11 - index),
            shares_10k=None,
            market_value_10k=None,
            report_date=date(2026, 7, 29),
        )
        for index in range(1, 11)
    ]
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(
        client,
        "get_cnindex_index_top_holdings",
        lambda code, expected_name, top_n, as_of: official_top10,
    )
    monkeypatch.setattr(
        client,
        "get_cnindex_related_index_products",
        lambda code: {
            "110026": {"fund_type": "ETF联接"},
            "159915": {"fund_type": "ETF"},
        },
    )
    monkeypatch.setattr(
        client,
        "get_fund_top10_snapshot",
        lambda code, source, scope: None,
    )
    monkeypatch.setattr(
        client,
        "get_fund_full_holdings_snapshot",
        lambda code, as_of, scope: None,
    )

    route = client.get_fund_holdings_route(
        "110026",
        analysis_end=date(2026, 7, 30),
    )

    assert route.scope == "tracked_index_top10"
    assert route.source == "cnindex_official"
    assert route.coverage == pytest.approx(0.55)
    assert route.holdings == official_top10
    assert route.fallback_reasons == ("official_index_full_weights_not_published",)


def test_fund_holdings_route_rejects_officially_mismatched_target_etf(
    monkeypatch,
) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    tracking = FundTrackingInfo(
        fund_code="110026",
        fund_name="易方达创业板ETF联接A",
        fund_type="指数型-股票",
        index_code="399006",
        index_name="创业板指数(价格)",
        target_etf_code="159999",
        target_etf_name="错误目标ETF",
    )
    official_top10 = [
        FundHolding(
            rank=index,
            code=f"300{index:03d}",
            name=f"样本{index}",
            weight_pct=float(11 - index),
            shares_10k=None,
            market_value_10k=None,
            report_date=date(2026, 7, 29),
        )
        for index in range(1, 11)
    ]
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(
        client,
        "get_cnindex_related_index_products",
        lambda code: {
            "110026": {"fund_type": "ETF联接"},
            "159915": {"fund_type": "ETF"},
        },
    )
    monkeypatch.setattr(
        client,
        "get_cnindex_index_top_holdings",
        lambda code, expected_name, top_n, as_of: official_top10,
    )

    def reject_target_lookup(*args, **kwargs):
        raise AssertionError(f"mismatched target ETF must not be queried: {args}, {kwargs}")

    monkeypatch.setattr(client, "get_fund_top10_snapshot", reject_target_lookup)
    monkeypatch.setattr(client, "get_fund_full_holdings_snapshot", reject_target_lookup)

    route = client.get_fund_holdings_route(
        "110026",
        analysis_end=date(2026, 7, 30),
    )

    assert route.scope == "tracked_index_top10"
    assert route.tracking is not None
    assert route.tracking.target_etf_code is None
    assert route.validation["fund_mapping"] == "confirmed"
    assert route.validation["target_etf_mapping"] == "mismatch"
    assert route.validation["index_identity"] == "confirmed"
    assert route.fallback_reasons == (
        "official_index_full_weights_not_published",
        "target_etf_relationship_official_mismatch",
    )


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
    direct_top10 = build_fund_holdings_snapshot(
        [FundHolding(1, "600519", "贵州茅台", 8.5, None, None, date(2026, 6, 30))],
        source="eastmoney_fund_disclosure",
        scope="fund_direct_top10",
        equity_allocation_pct=80.0,
    )
    direct_full = build_fund_holdings_snapshot(
        [
            FundHolding(1, "600519", "贵州茅台", 40.0, None, None, date(2025, 12, 31)),
            FundHolding(2, "000858", "五粮液", 39.5, None, None, date(2025, 12, 31)),
        ],
        source="eastmoney_fund_disclosure",
        scope="fund_full_disclosure",
        equity_allocation_pct=80.0,
    )
    monkeypatch.setattr(client, "get_fund_tracking_info", lambda code: tracking)
    monkeypatch.setattr(client, "get_fund_top10_snapshot", lambda code: direct_top10)
    monkeypatch.setattr(
        client,
        "get_fund_full_holdings_snapshot",
        lambda code, as_of: direct_full,
    )

    route = client.get_fund_holdings_route(
        "000001",
        fund_name=tracking.fund_name,
        analysis_end=date(2026, 7, 24),
    )

    assert route.scope == "fund_full_disclosure"
    assert route.source == "eastmoney_fund_disclosure"
    assert route.coverage == pytest.approx(0.9938)
    assert route.equity_allocation_pct == 80.0
    assert route.nav_equity_exposure == 0.8
    assert route.holdings == direct_full.holdings


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


def test_cnindex_api_uses_official_site_headers() -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    client.headers = {"User-Agent": "default"}

    headers = client._headers_for_url("https://www.cnindex.com.cn/index/example")

    assert headers["Accept"] == "application/json,text/plain,*/*"
    assert headers["Referer"] == "https://www.cnindex.com.cn/"
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

    def unavailable_overview(url: str, ttl_seconds: int) -> str:
        del url, ttl_seconds
        raise EastmoneyError("overview unavailable")

    monkeypatch.setattr(client, "_get_text", unavailable_overview)
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


def test_get_fund_nav_prefers_validated_single_request_overview(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    china_time = timezone(timedelta(hours=8))
    first_timestamp = int(datetime(2026, 7, 19, tzinfo=china_time).timestamp() * 1000)
    second_timestamp = int(datetime(2026, 7, 20, tzinfo=china_time).timestamp() * 1000)
    overview = (
        'var fS_name = "测试基金";\n'
        'var fS_code = "025856";\n'
        "var Data_netWorthTrend = "
        f'[{{"x":{first_timestamp},"y":1.01,"equityReturn":0.1}},'
        f'{{"x":{second_timestamp},"y":1.02,"equityReturn":0.435}}];\n'
        f"var Data_ACWorthTrend = [[{first_timestamp},1.11],[{second_timestamp},1.12]];"
    )

    monkeypatch.setattr(client, "_get_text", lambda url, ttl_seconds: overview)
    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url, ttl_seconds: pytest.fail("paginated NAV fallback should not be called"),
    )

    rows = client.get_fund_nav(
        "025856",
        start=date(2026, 7, 20),
        end=date(2026, 7, 21),
    )

    assert len(rows) == 1
    assert rows[0].date == date(2026, 7, 20)
    assert rows[0].unit_nav == 1.02
    assert rows[0].cumulative_nav == 1.12
    assert rows[0].daily_growth_pct == 0.44


def test_parse_pingzhongdata_fund_nav_rejects_route_mismatch() -> None:
    text = 'var fS_code = "000001";var Data_netWorthTrend = [];var Data_ACWorthTrend = [];'

    with pytest.raises(EastmoneyError, match="code mismatch"):
        parse_pingzhongdata_fund_nav(
            text,
            expected_code="025856",
            start=date(2026, 1, 1),
            end=date(2026, 7, 21),
        )


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


def test_parse_fund_holdings_sections_keeps_report_periods_separate() -> None:
    content = """
    <div class='box'><h4><a href='http://fund.eastmoney.com/515450.html'>ETF</a>
    截止至：2025-12-31</h4><table><tbody>
    <tr><td>1</td><td>600001</td><td>股票一</td><td>资讯</td>
    <td>60.00%</td><td>100</td><td>600</td></tr>
    <tr><td>2</td><td>000002</td><td>股票二</td><td>资讯</td>
    <td>39.98%</td><td>200</td><td>400</td></tr>
    </tbody></table></div>
    <div class='box'><h4><a href='http://fund.eastmoney.com/515450.html'>ETF</a>
    截止至：2025-09-30</h4><table><tbody>
    <tr><td>1</td><td>600001</td><td>股票一</td><td>资讯</td>
    <td>10.00%</td><td>100</td><td>100</td></tr>
    </tbody></table></div>
    """

    sections = parse_fund_holdings_sections(content, expected_code="515450")

    assert len(sections) == 2
    assert sections[0][0].report_date == date(2025, 12, 31)
    assert len(sections[0]) == 2
    assert sum(item.weight_pct or 0 for item in sections[0]) == pytest.approx(99.98)
    assert sections[1][0].report_date == date(2025, 9, 30)
    assert len(sections[1]) == 1


def test_parse_fund_asset_allocation_and_equity_coverage() -> None:
    page = """
    <title>红利低波50ETF南方(515450)基金资产配置</title>
    <script>
    var chartData ={"Dates":["2025-12-31"],"GP":[99.99],"ZQ":[0.0],
    "XJ":[0.06],"CTPZ":[0.0],"JZC":[150.39]};
    </script>
    """
    allocation = parse_fund_asset_allocation_page(
        page,
        expected_code="515450",
    )[0]
    holdings = [
        FundHolding(1, "600001", "股票一", 60.0, None, None, date(2025, 12, 31)),
        FundHolding(2, "000002", "股票二", 39.98, None, None, date(2025, 12, 31)),
    ]

    snapshot = build_fund_holdings_snapshot(
        holdings,
        source="eastmoney_fund_disclosure",
        scope="target_etf_full_disclosure",
        allocation=allocation,
    )

    assert allocation.stock_pct == 99.99
    assert snapshot.total_nav_weight_pct == 99.98
    assert snapshot.equity_coverage == pytest.approx(0.9999, abs=0.0001)
    assert snapshot.unexplained_equity_weight_pct == pytest.approx(0.01)


def test_active_fund_full_holdings_are_measured_against_stock_allocation() -> None:
    allocation = FundAssetAllocation(
        report_date=date(2025, 12, 31),
        stock_pct=79.42,
        bond_pct=20.58,
        cash_pct=1.24,
        other_pct=0.0,
        net_assets_100m_cny=29.37,
    )
    holdings = [
        FundHolding(1, "600001", "股票一", 40.0, None, None, date(2025, 12, 31)),
        FundHolding(2, "000002", "股票二", 39.41, None, None, date(2025, 12, 31)),
    ]

    snapshot = build_fund_holdings_snapshot(
        holdings,
        source="eastmoney_fund_disclosure",
        scope="fund_full_disclosure",
        allocation=allocation,
    )

    assert snapshot.total_nav_weight_pct == 79.41
    assert snapshot.equity_coverage == pytest.approx(0.9999, abs=0.0001)
    assert snapshot.unexplained_equity_weight_pct == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("full_scope", "top10_scope"),
    [
        ("tracked_index_full_weights", "tracked_index_top10"),
        ("target_etf_full_disclosure", "target_etf_top10"),
        ("fund_full_disclosure", "fund_direct_top10"),
    ],
)
def test_complete_holdings_snapshot_uses_explicit_top10_scope(
    full_scope: str,
    top10_scope: str,
) -> None:
    holdings = [
        FundHolding(
            rank=index,
            code=f"{index:06d}",
            name=f"Stock {index}",
            weight_pct=float(13 - index),
            shares_10k=None,
            market_value_10k=None,
            report_date=date(2025, 12, 31),
        )
        for index in range(1, 13)
    ]
    snapshot = build_fund_holdings_snapshot(
        holdings,
        source="test",
        scope=full_scope,
        equity_allocation_pct=100.0,
    )

    top10 = build_top10_from_snapshot(snapshot)

    assert top10.scope == top10_scope
    assert len(top10.holdings) == 10
    assert [item.rank for item in top10.holdings] == list(range(1, 11))


def test_top10_snapshot_rejects_unknown_complete_scope() -> None:
    snapshot = build_fund_holdings_snapshot(
        [
            FundHolding(
                rank=1,
                code="600519",
                name="贵州茅台",
                weight_pct=10.0,
                shares_10k=None,
                market_value_10k=None,
                report_date=date(2025, 12, 31),
            )
        ],
        source="test",
        scope="unknown_complete_scope",
        equity_allocation_pct=100.0,
    )

    with pytest.raises(EastmoneyError, match="Cannot derive a top-ten scope"):
        build_top10_from_snapshot(snapshot)


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
