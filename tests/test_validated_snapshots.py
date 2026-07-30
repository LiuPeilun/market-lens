from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from contextvars import copy_context
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from market_lens.agent.market_agent import apply_last_known_good_diagnostics
from market_lens.data.eastmoney import EastmoneyClient, EastmoneyError
from market_lens.data.snapshot_validation import (
    CNINDEX_OFFICIAL_SOURCE,
    EASTMONEY_F10_NAV_SOURCE,
    EASTMONEY_PUSH2HIS_SOURCE,
    EXCHANGE_FUND_PRICE_DATASET,
    FUND_NAV_DATASET,
    FUND_NAV_SNAPSHOT_VERSION,
    INDEX_TOP_HOLDINGS_DATASET,
    INDEX_TOP_HOLDINGS_SNAPSHOT_VERSION,
    STOCK_HISTORY_DATASET,
    STOCK_HISTORY_SNAPSHOT_VERSION,
    STOCK_VALUATION_DATASET,
    serialize_fund_nav_point,
    validate_fund_nav_snapshot,
    validate_index_top_holdings_snapshot,
    validate_stock_bar_snapshot,
)
from market_lens.storage.snapshots import ValidatedSnapshot, ValidatedSnapshotStore
from market_lens.storage.sqlite_cache import SQLiteCache
from market_lens.types import FundHolding, FundNavPoint, StockBar


@pytest.fixture
def snapshot_temp_root() -> Iterator[Path]:
    root = Path(".tmp") / "snapshot-tests" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_snapshot_store_round_trip_requires_exact_identity_and_source(
    snapshot_temp_root: Path,
) -> None:
    now = [1_000]
    store = ValidatedSnapshotStore(
        snapshot_temp_root / "snapshots.sqlite3",
        clock=lambda: now[0],
    )
    identity = {"symbol": "600519", "start": "2026-07-01", "end": "2026-07-20"}
    payload = [{"date": "2026-07-20", "close": 10.0}]
    store.put(
        dataset="stock_history",
        identity=identity,
        source="eastmoney_push2his",
        validator_version="stock_history_v1",
        payload=payload,
        source_as_of=date(2026, 7, 20),
        row_count=1,
    )

    snapshot = store.get(
        dataset="stock_history",
        identity=identity,
        allowed_sources={"eastmoney_push2his"},
        validator_version="stock_history_v1",
        max_age_seconds=60,
        validator=lambda value, source_as_of, count: (
            value == payload and source_as_of == date(2026, 7, 20) and count == 1
        ),
    )

    assert snapshot is not None
    assert snapshot.payload == payload
    assert snapshot.age_seconds == 0
    assert (
        store.get(
            dataset="stock_history",
            identity={**identity, "end": "2026-07-21"},
            allowed_sources={"eastmoney_push2his"},
            validator_version="stock_history_v1",
            max_age_seconds=60,
            validator=lambda value, source_as_of, count: True,
        )
        is None
    )
    assert (
        store.get(
            dataset="stock_history",
            identity=identity,
            allowed_sources={"untrusted_source"},
            validator_version="stock_history_v1",
            max_age_seconds=60,
            validator=lambda value, source_as_of, count: True,
        )
        is None
    )


def test_lkg_events_follow_stage_context_and_ignore_late_rotated_context(
    snapshot_temp_root: Path,
) -> None:
    client = EastmoneyClient(
        cache=SQLiteCache(snapshot_temp_root / "cache.sqlite3"),
        snapshot_store=ValidatedSnapshotStore(
            snapshot_temp_root / "snapshots.sqlite3"
        ),
    )
    snapshot = ValidatedSnapshot(
        dataset=STOCK_HISTORY_DATASET,
        identity={"symbol": "600519"},
        source=EASTMONEY_PUSH2HIS_SOURCE,
        validator_version=STOCK_HISTORY_SNAPSHOT_VERSION,
        payload=[{"date": "2026-07-20", "close": 10.0}],
        source_as_of=date(2026, 7, 20),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        age_seconds=0,
        row_count=1,
        payload_sha256="a" * 64,
    )

    client.consume_lkg_events()
    successful_stage_context = copy_context()
    successful_stage_context.run(client._record_lkg_event, snapshot)

    assert client.consume_lkg_events()[0]["dataset"] == STOCK_HISTORY_DATASET

    late_stage_context = copy_context()
    client.consume_lkg_events()
    late_stage_context.run(client._record_lkg_event, snapshot)

    assert client.consume_lkg_events() == []


def test_snapshot_store_rejects_stale_snapshot(snapshot_temp_root: Path) -> None:
    now = [1_000]
    store = ValidatedSnapshotStore(
        snapshot_temp_root / "snapshots.sqlite3",
        clock=lambda: now[0],
    )
    identity = {"code": "025856"}
    store.put(
        dataset="fund_nav",
        identity=identity,
        source="eastmoney_f10_nav",
        validator_version="fund_nav_v1",
        payload=[{"date": "2026-07-20"}],
        source_as_of=date(2026, 7, 20),
        row_count=1,
    )
    now[0] = 1_061

    snapshot = store.get(
        dataset="fund_nav",
        identity=identity,
        allowed_sources={"eastmoney_f10_nav"},
        validator_version="fund_nav_v1",
        max_age_seconds=60,
        validator=lambda value, source_as_of, count: True,
    )

    assert snapshot is None


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("payload_sha256", "corrupt"),
        ("row_count", 2),
        ("validator_version", "wrong_version"),
        ("schema_version", 999),
        ("schema_version", "corrupt"),
    ],
)
def test_snapshot_store_rejects_corrupt_metadata(
    snapshot_temp_root: Path,
    column: str,
    value: str | int,
) -> None:
    db_path = snapshot_temp_root / f"{column}.sqlite3"
    store = ValidatedSnapshotStore(db_path, clock=lambda: 1_000)
    identity = {"symbol": "600519"}
    store.put(
        dataset="stock_valuation",
        identity=identity,
        source="eastmoney_datacenter",
        validator_version="stock_valuation_v1",
        payload=[{"date": "2026-07-20"}],
        source_as_of=date(2026, 7, 20),
        row_count=1,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE validated_snapshots SET {column} = ? WHERE snapshot_key = ?",
            (value, store.key_for("stock_valuation", identity)),
        )

    snapshot = store.get(
        dataset="stock_valuation",
        identity=identity,
        allowed_sources={"eastmoney_datacenter"},
        validator_version="stock_valuation_v1",
        max_age_seconds=60,
        validator=lambda payload, source_as_of, count: (
            isinstance(payload, list) and len(payload) == count
        ),
    )

    assert snapshot is None


def test_dataset_validators_reject_bad_dates_and_incomplete_values() -> None:
    valid_bar = stock_bar(date(2026, 7, 20))
    duplicate_bars = [valid_bar, valid_bar]
    future_bar = stock_bar(date(2099, 1, 1))
    invalid_ohlc = StockBar(
        **{
            **valid_bar.__dict__,
            "high": 9.0,
        }
    )
    invalid_nav = FundNavPoint(
        date=date(2026, 7, 20),
        unit_nav=None,
        cumulative_nav=1.2,
        daily_growth_pct=0.1,
        subscribe_status=None,
        redeem_status=None,
    )

    assert not validate_stock_bar_snapshot(
        duplicate_bars,
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )
    assert not validate_stock_bar_snapshot(
        [future_bar],
        start=date(2026, 7, 1),
        end=date(2099, 1, 1),
    )
    assert not validate_stock_bar_snapshot(
        [invalid_ohlc],
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )
    assert not validate_fund_nav_snapshot(
        [invalid_nav],
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )


def test_index_top_holdings_validator_rejects_incomplete_or_duplicate_rows() -> None:
    rows = [
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

    assert validate_index_top_holdings_snapshot(
        rows,
        expected_count=10,
        today=date(2026, 7, 30),
    )
    assert not validate_index_top_holdings_snapshot(
        rows[:9],
        expected_count=10,
        today=date(2026, 7, 30),
    )
    duplicate = [*rows[:-1], FundHolding(**{**rows[-1].__dict__, "code": rows[0].code})]
    assert not validate_index_top_holdings_snapshot(
        duplicate,
        expected_count=10,
        today=date(2026, 7, 30),
    )


def test_cnindex_top_holdings_uses_exact_validated_lkg_after_live_failure(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "cnindex.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )
    identity_payload = {
        "code": 200,
        "data": {
            "indexcode": "399006",
            "indexname": "创业板指",
            "indexfullcname": "创业板指数",
        },
    }
    sample_payload = {
        "code": 200,
        "data": {
            "rows": [
                {
                    "dateStr": "2026-07-29",
                    "seccode": f"300{index:03d}",
                    "secname": f"样本{index}",
                    "weight": str(11 - index),
                }
                for index in range(1, 11)
            ]
        },
    }

    def live_response(url: str, ttl_seconds: int, is_success) -> dict:
        del ttl_seconds, is_success
        return identity_payload if "selectIndexByCode" in url else sample_payload

    monkeypatch.setattr(client, "_get_validated_json", live_response)
    expected = client.get_cnindex_index_top_holdings(
        "399006",
        expected_name="创业板指数(价格)",
        top_n=10,
        as_of=date(2026, 7, 30),
    )
    assert client.consume_lkg_events() == []

    def fail_live(url: str, ttl_seconds: int, is_success) -> dict:
        del url, ttl_seconds, is_success
        raise EastmoneyError("CNIndex offline")

    monkeypatch.setattr(client, "_get_validated_json", fail_live)
    restored = client.get_cnindex_index_top_holdings(
        "399006",
        expected_name="创业板指数(价格)",
        top_n=10,
        as_of=date(2026, 7, 30),
    )

    assert restored == expected
    event = client.consume_lkg_events()[0]
    assert event["dataset"] == INDEX_TOP_HOLDINGS_DATASET
    assert event["source"] == CNINDEX_OFFICIAL_SOURCE
    assert event["validator_version"] == INDEX_TOP_HOLDINGS_SNAPSHOT_VERSION
    assert event["identity"] == {"index_code": "399006", "top_n": 10}

    with pytest.raises(EastmoneyError, match="CNIndex offline"):
        client.get_cnindex_index_top_holdings(
            "399006",
            expected_name="创业板指数(价格)",
            top_n=5,
            as_of=date(2026, 7, 30),
        )


def test_stock_history_uses_valid_lkg_after_live_failure(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "client.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )
    live_payload = {
        "data": {
            "code": "600519",
            "klines": [
                "2026-07-18,10.0,10.2,10.3,9.9,100,1000,4.0,2.0,0.2,1.0",
                "2026-07-20,10.2,10.4,10.5,10.1,120,1200,3.9,1.96,0.2,1.1",
            ]
        }
    }
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: live_payload)
    expected = client.get_stock_history(
        "600519",
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )
    assert client.consume_lkg_events() == []

    def fail_live(url: str, ttl_seconds: int) -> dict:
        raise EastmoneyError("upstream disconnected")

    monkeypatch.setattr(client, "_get_json", fail_live)
    actual = client.get_stock_history(
        "600519",
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )

    assert actual == expected
    events = client.consume_lkg_events()
    assert events[0]["dataset"] == STOCK_HISTORY_DATASET
    assert events[0]["source"] == EASTMONEY_PUSH2HIS_SOURCE
    assert events[0]["row_count"] == 2


def test_malformed_live_response_cannot_overwrite_valid_lkg(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "client.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )
    valid_payload = {
        "data": {
            "code": "600519",
            "klines": [
                "2026-07-20,10.0,10.2,10.3,9.9,100,1000,4.0,2.0,0.2,1.0"
            ],
        }
    }
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: valid_payload)
    expected = client.get_stock_history(
        "600519",
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )
    client.consume_lkg_events()
    malformed_payload = {
        "data": {
            "code": "600519",
            "klines": [
                "2026-07-20,10.0,99.0,10.3,9.9,100,1000,4.0,2.0,0.2,1.0"
            ],
        }
    }
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: malformed_payload)

    actual = client.get_stock_history(
        "600519",
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )

    assert actual == expected
    assert client.consume_lkg_events()[0]["dataset"] == STOCK_HISTORY_DATASET


def test_stock_valuation_uses_valid_lkg_after_live_failure(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "valuation.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )
    live_payload = {
        "result": {
            "data": [
                {
                    "TRADE_DATE": "2026-07-20",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "CLOSE_PRICE": 1500.0,
                    "TOTAL_MARKET_CAP": 1_800_000_000_000.0,
                    "PE_TTM": 25.0,
                    "PE_LAR": 26.0,
                    "PB_MRQ": 8.0,
                    "PS_TTM": 12.0,
                    "PCF_OCF_TTM": 30.0,
                    "PEG_CAR": 1.5,
                }
            ]
        }
    }
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: live_payload)
    expected = client.get_stock_valuation("600519")
    client.consume_lkg_events()

    def fail_live(url: str, ttl_seconds: int) -> dict:
        raise EastmoneyError("upstream disconnected")

    monkeypatch.setattr(client, "_get_json", fail_live)
    actual = client.get_stock_valuation("600519")

    assert actual == [
        type(expected[0])(
            **{
                **expected[0].__dict__,
                "raw": {},
            }
        )
    ]
    assert client.consume_lkg_events()[0]["dataset"] == STOCK_VALUATION_DATASET


def test_exchange_fund_price_uses_valid_lkg_after_live_failure(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "exchange-fund.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )
    live_payload = {
        "data": {
            "code": "515450",
            "klines": [
                "2026-07-20,1.0,1.02,1.03,0.99,100,1000,4.0,2.0,0.02,1.0"
            ],
        }
    }
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: live_payload)
    expected = client.get_exchange_fund_price_nav(
        "515450",
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )
    client.consume_lkg_events()

    def fail_live(url: str, ttl_seconds: int) -> dict:
        raise EastmoneyError("upstream disconnected")

    monkeypatch.setattr(client, "_get_json", fail_live)
    actual = client.get_exchange_fund_price_nav(
        "515450",
        start=date(2026, 7, 1),
        end=date(2026, 7, 20),
    )

    assert actual == expected
    assert client.consume_lkg_events()[0]["dataset"] == EXCHANGE_FUND_PRICE_DATASET


def test_live_failure_without_exact_valid_lkg_still_raises(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "client.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )

    def fail_live(url: str, ttl_seconds: int) -> dict:
        raise EastmoneyError("upstream disconnected")

    monkeypatch.setattr(client, "_get_json", fail_live)

    with pytest.raises(EastmoneyError, match="upstream disconnected"):
        client.get_stock_history(
            "600519",
            start=date(2026, 7, 1),
            end=date(2026, 7, 20),
        )


def test_live_stock_history_identity_mismatch_fails_closed(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "identity.sqlite3"
    client = EastmoneyClient(
        cache=SQLiteCache(db_path),
        snapshot_store=ValidatedSnapshotStore(db_path, clock=lambda: 1_000),
    )
    mismatched_payload = {
        "data": {
            "code": "000001",
            "klines": [
                "2026-07-20,10.0,10.2,10.3,9.9,100,1000,4.0,2.0,0.2,1.0"
            ],
        }
    }
    monkeypatch.setattr(client, "_get_json", lambda url, ttl_seconds: mismatched_payload)

    with pytest.raises(EastmoneyError, match="identity mismatch"):
        client.get_stock_history(
            "600519",
            start=date(2026, 7, 1),
            end=date(2026, 7, 20),
        )


def test_fund_nav_uses_only_exact_validated_lkg(
    snapshot_temp_root: Path,
    monkeypatch,
) -> None:
    db_path = snapshot_temp_root / "client.sqlite3"
    store = ValidatedSnapshotStore(db_path, clock=lambda: 1_000)
    client = EastmoneyClient(cache=SQLiteCache(db_path), snapshot_store=store)
    start = date(2026, 7, 1)
    end = date(2026, 7, 20)
    identity = {
        "code": "025856",
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    rows = [
        FundNavPoint(
            date=end,
            unit_nav=1.02,
            cumulative_nav=1.12,
            daily_growth_pct=0.1,
            subscribe_status="open",
            redeem_status="open",
        )
    ]
    store.put(
        dataset=FUND_NAV_DATASET,
        identity=identity,
        source=EASTMONEY_F10_NAV_SOURCE,
        validator_version=FUND_NAV_SNAPSHOT_VERSION,
        payload=[serialize_fund_nav_point(row) for row in rows],
        source_as_of=end,
        row_count=1,
    )

    def fail_live(*args, **kwargs):
        raise EastmoneyError("upstream disconnected")

    monkeypatch.setattr(client, "_get_text", fail_live)
    monkeypatch.setattr(client, "_get_fund_nav_page", fail_live)

    actual = client.get_fund_nav("025856", start=start, end=end)

    assert actual == rows
    assert client.consume_lkg_events()[0]["dataset"] == FUND_NAV_DATASET
    with pytest.raises(EastmoneyError, match="upstream disconnected"):
        client.get_fund_nav(
            "025856",
            start=start,
            end=date(2026, 7, 21),
        )


def test_lkg_diagnostics_degrade_assessment_without_changing_score_or_confidence() -> None:
    result = {
        "notes": [],
        "assessment": {
            "status": "complete",
            "method": "fundamental_valuation",
            "fallback_reasons": [],
            "overall_confidence": 0.72,
            "dimensions": {"valuation": {"score": 42.0}},
            "data_quality": {"sources": [], "warnings": []},
        },
    }
    events = [
        {
            "dataset": "stock_history",
            "identity": {"symbol": "600519"},
            "source": "eastmoney_push2his",
            "source_as_of": "2026-07-20",
            "snapshot_retrieved_at": "2026-07-20T08:00:00+00:00",
            "snapshot_age_seconds": 3600,
            "row_count": 100,
            "payload_sha256": "abc",
            "validator_version": STOCK_HISTORY_SNAPSHOT_VERSION,
            "fallback_reason": "upstream_unavailable",
        }
    ]

    apply_last_known_good_diagnostics(result, events)

    assessment = result["assessment"]
    assert assessment["status"] == "degraded"
    assert assessment["method"] == "last_known_good"
    assert assessment["fallback_reasons"] == ["last_known_good_snapshot"]
    assert assessment["dimensions"]["valuation"]["score"] == 42.0
    assert assessment["overall_confidence"] == 0.72
    assert assessment["data_quality"]["sources"][-1]["status"] == "last_known_good"
    assert result["last_known_good"]["used"] is True


def test_lkg_diagnostics_keep_unavailable_assessment_unavailable() -> None:
    result = {
        "notes": [],
        "assessment": {
            "status": "unavailable",
            "method": "unavailable",
            "fallback_reasons": [],
            "dimensions": {"valuation": {"score": None}},
            "data_quality": {"sources": [], "warnings": []},
        },
    }

    apply_last_known_good_diagnostics(
        result,
        [
            {
                "dataset": "fund_nav_history",
                "source": "eastmoney_f10_nav",
            }
        ],
    )

    assert result["assessment"]["status"] == "unavailable"
    assert result["assessment"]["method"] == "unavailable"


def test_optional_lkg_diagnostics_preserve_selected_valuation_method() -> None:
    result = {
        "notes": [],
        "assessment": {
            "status": "complete",
            "method": "index_fundamental_valuation",
            "dimensions": {"valuation": {"score": 42.0}},
            "fallback_reasons": [],
            "data_quality": {"sources": [], "warnings": []},
        },
    }
    event = {
        "dataset": INDEX_TOP_HOLDINGS_DATASET,
        "identity": {"index_code": "399006", "top_n": 10},
        "source": CNINDEX_OFFICIAL_SOURCE,
        "source_as_of": "2026-07-29",
        "snapshot_retrieved_at": "2026-07-29T08:00:00+00:00",
        "snapshot_age_seconds": 60,
        "row_count": 10,
        "payload_sha256": "a" * 64,
        "validator_version": INDEX_TOP_HOLDINGS_SNAPSHOT_VERSION,
        "fallback_reason": "upstream_unavailable",
    }

    apply_last_known_good_diagnostics(
        result,
        [event],
        affects_valuation_method=False,
    )

    assert result["assessment"]["status"] == "degraded"
    assert result["assessment"]["method"] == "index_fundamental_valuation"
    assert result["assessment"]["fallback_reasons"] == ["last_known_good_snapshot"]


def stock_bar(point_date: date) -> StockBar:
    return StockBar(
        date=point_date,
        open=10.0,
        close=10.2,
        high=10.3,
        low=9.9,
        volume=100.0,
        amount=1_000.0,
        amplitude_pct=4.0,
        change_pct=2.0,
        change_amount=0.2,
        turnover_pct=1.0,
    )
