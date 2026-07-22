from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest

from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    deduplicate_reit_financial_rows,
    infer_reit_report_period,
    match_reit_distribution_announcements,
    parse_reit_distribution_table,
    parse_reit_profile,
    validated_reit_financial_payload,
    validated_reit_kline_rows,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eastmoney"


def load_json_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def load_text_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def client_with_profile(monkeypatch) -> EastmoneyClient:
    client = EastmoneyClient.__new__(EastmoneyClient)
    profile_payload = load_json_fixture("reit_profile_180101.json")
    monkeypatch.setattr(
        client,
        "_get_fund_detail_payload",
        lambda normalized_code: copy.deepcopy(profile_payload),
    )
    return client


def test_reit_profile_requires_exact_type_and_route() -> None:
    payload = load_json_fixture("reit_profile_180101.json")
    profile = parse_reit_profile(payload, expected_code="180101")

    assert profile.fund_code == "180101"
    assert profile.fund_name == "博时蛇口产园REIT"
    assert profile.fund_type == "Reits"
    assert profile.quote_id == "0.180101"
    assert profile.exchange == "SZSE"
    assert profile.period_end_net_assets_cny == 3017800392.7
    assert profile.scale_report_date == date(2025, 12, 31)
    assert profile.scoring_eligible is False

    ordinary_fund = copy.deepcopy(payload)
    ordinary_fund["Datas"]["FTYPE"] = "混合型-偏股"
    with pytest.raises(EastmoneyError, match="is not an Eastmoney REIT"):
        parse_reit_profile(ordinary_fund, expected_code="180101")

    wrong_route = copy.deepcopy(payload)
    wrong_route["Datas"]["FCODE"] = "508000"
    with pytest.raises(EastmoneyError, match="route mismatch"):
        parse_reit_profile(wrong_route, expected_code="180101")


def test_reit_price_client_uses_unadjusted_exchange_route(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)
    requested_urls: list[str] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        requested_urls.append(unquote(url))
        return load_json_fixture("reit_price_180101.json")

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_reit_price_history(
        "180101",
        date(2026, 7, 21),
        date(2026, 7, 22),
        period="daily",
    )

    assert [row.date for row in rows] == [date(2026, 7, 21), date(2026, 7, 22)]
    assert rows[-1].close == 1.551
    assert rows[-1].fund_name == "博时蛇口产园REIT"
    assert rows[-1].is_complete is None
    assert rows[-1].scoring_eligible is False
    assert len(requested_urls) == 1
    assert "secid=0.180101" in requested_urls[0]
    assert "fqt=0" in requested_urls[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [("code", "508000"), ("market", 1), ("name", "另一只REIT")],
)
def test_reit_price_rejects_route_drift(field: str, value: object) -> None:
    profile = parse_reit_profile(
        load_json_fixture("reit_profile_180101.json"),
        expected_code="180101",
    )
    payload = load_json_fixture("reit_price_180101.json")
    payload["data"][field] = value

    with pytest.raises(EastmoneyError, match="route mismatch"):
        validated_reit_kline_rows(payload, profile)


def test_reit_price_rejects_invalid_period_and_dates(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)
    with pytest.raises(ValueError, match="period"):
        client.get_reit_price_history(
            "180101",
            date(2026, 1, 1),
            date(2026, 2, 1),
            period="yearly",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="start"):
        client.get_reit_price_history(
            "180101",
            date(2026, 2, 1),
            date(2026, 1, 1),
        )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("某基金2025年年度报告", (date(2025, 12, 31), "annual")),
        ("某基金2025年半年度报告", (date(2025, 6, 30), "semiannual")),
        ("某基金2025年第3季度报告", (date(2025, 9, 30), "q3")),
        ("某基金2025年房地产评估报告", (None, None)),
        ("某基金2025年年度报告摘要", (None, None)),
    ],
)
def test_reit_periodic_report_title_is_strict(
    title: str,
    expected: tuple[date | None, str | None],
) -> None:
    assert infer_reit_report_period(title) == expected


def test_reit_notices_keep_noncanonical_rows_but_do_not_date_them(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)
    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url, ttl_seconds: load_json_fixture("reit_periodic_notices_180101.json"),
    )

    notices = client.get_reit_notices("180101")

    annual = next(item for item in notices if item.announcement_id == "ANNUAL2025")
    valuation = next(item for item in notices if item.announcement_id == "VALUATION2025")
    assert annual.report_date == date(2025, 12, 31)
    assert annual.report_kind == "annual"
    assert annual.attachment_url.endswith("H2_ANNUAL2025_1.pdf")
    assert valuation.is_canonical is False
    assert valuation.report_date is None
    assert all(item.scoring_eligible is False for item in notices)


def test_reit_financials_match_preferred_canonical_notices(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        decoded = unquote(url)
        if "GetArrayCwzb" in decoded:
            return load_json_fixture("reit_financial_180101.json")
        if "JJGG" in decoded:
            return load_json_fixture("reit_periodic_notices_180101.json")
        raise AssertionError(f"Unexpected URL: {decoded}")

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_reit_financials("180101")

    annual = next(item for item in rows if item.report_date == date(2025, 12, 31))
    semiannual = next(item for item in rows if item.report_date == date(2025, 6, 30))
    assert annual.report_kind == "annual"
    assert annual.notice_date == date(2026, 3, 28)
    assert annual.realized_income_cny == -15632572.9
    assert annual.net_profit_cny == 181322141.94
    assert annual.period_end_unit_nav_cny == 2.12
    assert annual.distributable_profit_cny is None
    assert annual.point_in_time_eligible is True
    assert annual.scoring_eligible is False
    assert semiannual.report_kind == "semiannual"
    assert semiannual.notice_date == date(2025, 8, 30)


def test_reit_financials_without_notice_are_point_in_time_ineligible(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)
    empty_notices = {
        "Data": [],
        "ErrCode": 0,
        "TotalCount": 0,
        "PageSize": 100,
        "PageIndex": 1,
    }

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        if "GetArrayCwzb" in url:
            return load_json_fixture("reit_financial_180101.json")
        return empty_notices

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_reit_financials("180101")

    assert rows
    assert all(item.notice_date is None for item in rows)
    assert all(item.point_in_time_eligible is False for item in rows)
    assert all(item.scoring_eligible is False for item in rows)


def test_reit_financials_fetch_advertised_years_and_deduplicate(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)
    first_payload = load_json_fixture("reit_financial_180101.json")
    first_payload["Data"]["years"] = [2025, 2024]
    second_payload = copy.deepcopy(first_payload)
    second_payload["Data"]["year"] = 2024
    arrays = second_payload["Data"]["data"]
    for values in arrays.values():
        values[:] = [values[1], values[0]]
    arrays["FSRQ"] = ["2025-06-30", "2024-12-31"]
    requested_urls: list[str] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        decoded = unquote(url)
        requested_urls.append(decoded)
        if "JJGG" in decoded:
            return load_json_fixture("reit_periodic_notices_180101.json")
        if "year=2024" in decoded:
            return second_payload
        return first_payload

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_reit_financials("180101")

    assert [item.report_date for item in rows] == [
        date(2024, 12, 31),
        date(2025, 6, 30),
        date(2025, 12, 31),
    ]
    assert sum("GetArrayCwzb" in url for url in requested_urls) == 2
    assert any("year=2024" in url for url in requested_urls)


def test_reit_financial_payload_rejects_shape_changes_and_conflicts() -> None:
    payload = load_json_fixture("reit_financial_180101.json")
    malformed = copy.deepcopy(payload)
    malformed["Data"]["data"]["ENDNAV"].pop()
    with pytest.raises(EastmoneyError, match="unequal lengths"):
        validated_reit_financial_payload(malformed)

    rows, _, _ = validated_reit_financial_payload(payload)
    conflicting = copy.deepcopy(rows[0])
    conflicting["NETPROFIT"] = "1.00"
    with pytest.raises(EastmoneyError, match="Conflicting"):
        deduplicate_reit_financial_rows([rows[0], conflicting])


def test_reit_announcement_client_paginates_and_deduplicates(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    all_rows = load_json_fixture("reit_periodic_notices_180101.json")["Data"]
    requested_pages: list[int] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        decoded = unquote(url)
        page = int(decoded.split("pageIndex=", 1)[1].split("&", 1)[0])
        requested_pages.append(page)
        start = (page - 1) * 2
        return {
            "Data": all_rows[start : start + 2],
            "ErrCode": 0,
            "TotalCount": len(all_rows),
            "PageSize": 2,
            "PageIndex": page,
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client._get_reit_announcement_rows("180101", category="3", page_size=2)

    assert requested_pages == [1, 2, 3]
    assert {row["ID"] for row in rows} == {row["ID"] for row in all_rows}


def test_reit_distribution_parser_and_announcement_matching() -> None:
    html = load_text_fixture("reit_distributions_180101.html")
    rows = parse_reit_distribution_table("180101", html)
    notices = load_json_fixture("reit_distribution_notices_180101.json")["Data"]
    matched = match_reit_distribution_announcements(rows, notices)

    assert len(matched) == 2
    assert matched[0].cash_per_unit_cny == 0.02
    assert matched[0].announcement_date == date(2025, 12, 4)
    assert matched[0].available_date == date(2025, 12, 8)
    assert matched[0].point_in_time_eligible is True
    assert matched[1].cash_per_unit_cny == 0.0231
    assert all(item.scoring_eligible is False for item in matched)

    unmatched = match_reit_distribution_announcements(rows, [])
    assert all(item.announcement_date is None for item in unmatched)
    assert all(item.point_in_time_eligible is False for item in unmatched)


def test_reit_distribution_parser_rejects_missing_or_malformed_table() -> None:
    with pytest.raises(EastmoneyError, match="was not found"):
        parse_reit_distribution_table("180101", "<html></html>")
    malformed = load_text_fixture("reit_distributions_180101.html").replace(
        "每份派现金0.0231元",
        "每十份派现金0.231元",
    )
    with pytest.raises(EastmoneyError, match="Unexpected REIT distribution row"):
        parse_reit_distribution_table("180101", malformed)


def test_reit_distribution_client_uses_profile_html_and_notices(monkeypatch) -> None:
    client = client_with_profile(monkeypatch)
    monkeypatch.setattr(
        client,
        "_get_text",
        lambda url, ttl_seconds: load_text_fixture("reit_distributions_180101.html"),
    )
    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url, ttl_seconds: load_json_fixture(
            "reit_distribution_notices_180101.json"
        ),
    )

    rows = client.get_reit_distributions("180101")

    assert len(rows) == 2
    assert all(item.point_in_time_eligible for item in rows)
