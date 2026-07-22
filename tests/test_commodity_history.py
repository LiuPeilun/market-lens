from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest

from market_lens.data.eastmoney import (
    COMMODITY_MAIN_CONTRACTS,
    EastmoneyClient,
    EastmoneyError,
    commodity_main_contract_spec,
    parse_commodity_kline,
    validated_commodity_kline_rows,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eastmoney"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_commodity_main_contract_specs_are_complete_and_explicit() -> None:
    expected = {
        "copper": ("CU", "113.cum", "SHFE", "CNY/metric_ton", 5.0),
        "aluminum": ("AL", "113.alm", "SHFE", "CNY/metric_ton", 5.0),
        "gold": ("AU", "113.aum", "SHFE", "CNY/gram", 1000.0),
        "rebar": ("RB", "113.rbm", "SHFE", "CNY/metric_ton", 10.0),
        "hot_rolled_coil": ("HC", "113.hcm", "SHFE", "CNY/metric_ton", 10.0),
        "iron_ore": ("I", "114.im", "DCE", "CNY/metric_ton", 100.0),
        "coking_coal": ("JM", "114.jmm", "DCE", "CNY/metric_ton", 60.0),
        "coke": ("J", "114.jm", "DCE", "CNY/metric_ton", 100.0),
    }

    assert set(COMMODITY_MAIN_CONTRACTS) == set(expected)
    for key, values in expected.items():
        spec = commodity_main_contract_spec(key)  # type: ignore[arg-type]
        assert (
            spec.product_code,
            spec.quote_id,
            spec.exchange,
            spec.price_unit,
            spec.contract_multiplier,
        ) == values
        assert spec.currency == "CNY"
        assert spec.series_kind == "main_continuous"
        assert spec.roll_method == "eastmoney_provider_defined_main_contract"
        assert spec.price_adjustment == "none"


def test_parse_commodity_kline_fixture() -> None:
    payload = load_fixture("commodity_main_rebar.json")
    spec = commodity_main_contract_spec("rebar")
    row = parse_commodity_kline(payload["data"]["klines"][0], spec)

    assert row.key == "rebar"
    assert row.quote_id == "113.rbm"
    assert row.period == "daily"
    assert row.date == date(2026, 7, 22)
    assert row.close == 3079.0
    assert row.volume_lots == 10913220.0
    assert row.amount_cny == 336857872312.0
    assert row.is_complete is None
    assert row.source == "eastmoney_push2his"


def test_commodity_client_requests_unadjusted_main_series(monkeypatch) -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)
    requested_urls: list[str] = []

    def fake_get_json(url: str, ttl_seconds: int) -> dict:
        requested_urls.append(unquote(url))
        return load_fixture("commodity_main_rebar.json")

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_commodity_main_contract_history(
        "rebar",
        date(2009, 1, 1),
        date(2026, 7, 22),
        period="monthly",
    )

    assert [row.date for row in rows] == [date(2009, 3, 31), date(2026, 7, 22)]
    assert all(row.period == "monthly" for row in rows)
    assert all(row.is_complete is None for row in rows)
    assert len(requested_urls) == 1
    assert "secid=113.rbm" in requested_urls[0]
    assert "klt=103" in requested_urls[0]
    assert "fqt=0" in requested_urls[0]
    assert "beg=20090101" in requested_urls[0]
    assert "end=20260722" in requested_urls[0]


def test_commodity_client_rejects_invalid_arguments() -> None:
    client = EastmoneyClient.__new__(EastmoneyClient)

    with pytest.raises(ValueError, match="Unsupported commodity"):
        client.get_commodity_main_contract_history(  # type: ignore[arg-type]
            "thermal_coal", date(2026, 1, 1), date(2026, 2, 1)
        )
    with pytest.raises(ValueError, match="period"):
        client.get_commodity_main_contract_history(
            "copper",
            date(2026, 1, 1),
            date(2026, 2, 1),
            period="yearly",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="start"):
        client.get_commodity_main_contract_history(
            "copper", date(2026, 2, 1), date(2026, 1, 1)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("code", "alm"), ("market", 114), ("name", "沪铝主连")],
)
def test_commodity_response_rejects_route_mismatch(field: str, value: object) -> None:
    payload = load_fixture("commodity_main_rebar.json")
    payload["data"][field] = value

    with pytest.raises(EastmoneyError, match="route mismatch"):
        validated_commodity_kline_rows(payload, commodity_main_contract_spec("rebar"))


def test_commodity_response_handles_empty_and_malformed_data() -> None:
    spec = commodity_main_contract_spec("rebar")

    assert validated_commodity_kline_rows({"data": None}, spec) == []
    with pytest.raises(EastmoneyError, match="kline response"):
        validated_commodity_kline_rows({"data": []}, spec)
    with pytest.raises(EastmoneyError, match="kline rows"):
        validated_commodity_kline_rows(
            {"data": {"code": "rbm", "market": 113, "name": "螺纹钢主连"}},
            spec,
        )


def test_commodity_response_rejects_bad_values_and_duplicate_dates() -> None:
    spec = commodity_main_contract_spec("rebar")
    base_data = {"code": "rbm", "market": 113, "name": "螺纹钢主连"}

    with pytest.raises(EastmoneyError, match="open"):
        validated_commodity_kline_rows(
            {
                "data": base_data
                | {"klines": ["2026-07-22,--,3079,3128,3052,1,2,3,4,5,0"]}
            },
            spec,
        )
    duplicate = "2026-07-22,3088,3079,3128,3052,1,2,3,4,5,0"
    with pytest.raises(EastmoneyError, match="duplicate dates"):
        validated_commodity_kline_rows(
            {"data": base_data | {"klines": [duplicate, duplicate]}},
            spec,
        )
