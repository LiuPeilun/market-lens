from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest

from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    deduplicate_financial_statement_rows,
    parse_stock_balance_sheet,
    parse_stock_cash_flow_statement,
    parse_stock_income_statement,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eastmoney"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_parse_stock_balance_sheet_fixture() -> None:
    row = parse_stock_balance_sheet(load_fixture("f10_balance_sheet.json")["data"][0])

    assert row.code == "000002"
    assert row.report_date == date(2025, 12, 31)
    assert row.notice_date == date(2026, 4, 1)
    assert row.contract_liability_cny == 93057376612.88
    assert row.inventory_cny == 373738098060.15
    assert row.current_portion_noncurrent_liabilities_cny == 136650283100.6
    assert row.total_equity_cny == 235860058036.69


def test_parse_stock_income_statement_fixture() -> None:
    row = parse_stock_income_statement(load_fixture("f10_income_statement.json")["data"][0])

    assert row.code == "300750"
    assert row.notice_date == date(2026, 3, 10)
    assert row.total_operating_revenue_cny == 423701834000.0
    assert row.research_expense_cny == 22146581000.0
    assert row.research_expense_yoy_pct == 19.0244070487
    assert row.development_expense_cny is None


def test_parse_stock_cash_flow_statement_fixture() -> None:
    row = parse_stock_cash_flow_statement(
        load_fixture("f10_cash_flow_statement.json")["data"][0]
    )

    assert row.net_operating_cash_flow_cny == 133219982000.0
    assert row.capital_expenditure_cash_cny == 42344558000.0
    assert row.net_investing_cash_flow_cny == -94475790000.0
    assert row.ending_cash_cny == 299929741000.0


def test_income_statement_client_fetches_dates_in_five_report_chunks(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    requested_urls: list[str] = []
    report_dates = [date(year, 12, 31) for year in range(2026, 2019, -1)]

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        decoded = unquote(url)
        requested_urls.append(decoded)
        if "lrbDateAjaxNew" in decoded:
            return {
                "data": [
                    {"REPORT_DATE": f"{report_date.isoformat()} 00:00:00"}
                    for report_date in report_dates
                ]
            }

        dates_parameter = decoded.split("dates=", 1)[1].split("&", 1)[0]
        return {
            "data": [
                {
                    "SECURITY_CODE": "300750",
                    "REPORT_DATE": f"{report_date} 00:00:00",
                    "NOTICE_DATE": f"{int(report_date[:4]) + 1}-03-31 00:00:00",
                    "TOTAL_OPERATE_INCOME": "100",
                    "RESEARCH_EXPENSE": "10",
                }
                for report_date in dates_parameter.split(",")
            ]
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_stock_income_statements("300750", max_reports=None)

    assert [row.report_date.year for row in rows] == list(range(2020, 2027))
    assert len(requested_urls) == 3
    assert "companyType=4" in requested_urls[0]
    assert "reportDateType=1" in requested_urls[0]
    data_requests = [url for url in requested_urls if "lrbAjaxNew" in url]
    request_chunk_sizes = [
        len(url.split("dates=", 1)[1].split("&", 1)[0].split(","))
        for url in data_requests
    ]
    assert request_chunk_sizes == [5, 2]


def test_statement_client_applies_scope_company_type_and_limit(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    requested_urls: list[str] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        decoded = unquote(url)
        requested_urls.append(decoded)
        if "DateAjaxNew" in decoded:
            return {
                "data": [
                    {"REPORT_DATE": "2026-03-31"},
                    {"REPORT_DATE": "2025-12-31"},
                    {"REPORT_DATE": "2025-09-30"},
                ]
            }
        dates_parameter = decoded.split("dates=", 1)[1].split("&", 1)[0]
        return {
            "data": [
                {"SECURITY_CODE": "600036", "REPORT_DATE": report_date}
                for report_date in dates_parameter.split(",")
            ]
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_stock_balance_sheets(
        "600036",
        report_scope="all",
        company_type="bank",
        max_reports=2,
    )

    assert [row.report_date for row in rows] == [date(2025, 12, 31), date(2026, 3, 31)]
    assert all("companyType=3" in url for url in requested_urls)
    assert all("reportDateType=0" in url for url in requested_urls)


def test_statement_client_rejects_invalid_payload_and_arguments(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: {"unexpected": []})

    with pytest.raises(EastmoneyError, match="balance_sheet dates"):
        client.get_stock_balance_sheets("000002")
    with pytest.raises(ValueError, match="report scope"):
        client.get_stock_balance_sheets("000002", report_scope="quarterly")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="company type"):
        client.get_stock_balance_sheets("000002", company_type="unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_reports"):
        client.get_stock_balance_sheets("000002", max_reports=0)


def test_statement_client_accepts_empty_date_list(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: {"data": []})

    assert client.get_stock_cash_flow_statements("300750") == []


def test_statement_deduplication_keeps_latest_notice_date() -> None:
    rows = deduplicate_financial_statement_rows(
        [
            {"REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-03-10", "version": 1},
            {"REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-03-20", "version": 2},
        ]
    )

    assert len(rows) == 1
    assert rows[0]["version"] == 2
