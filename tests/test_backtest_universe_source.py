from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from market_lens.backtesting.models import BacktestDataError
from market_lens.backtesting.universe_source import (
    AuditableStockUniverseBuilder,
    DoltHubHistoricalIndexSource,
    HistoricalIndexMember,
    HistoricalIndexSnapshot,
    stable_member_sample,
)
from market_lens.types import StockValuationPoint


class FakeHistoricalSource:
    def snapshot_on_or_before(self, index_code, scheduled_date):
        del index_code
        first = scheduled_date.month == 3
        codes = range(1, 11) if first else range(2, 12)
        suffix = "a" if first else "b"
        return HistoricalIndexSnapshot(
            date=scheduled_date,
            members=tuple(make_member(code) for code in codes),
            source="fixture://historical-index",
            source_revision="a" * 32,
            retrieved_at=datetime(2026, 7, 22, tzinfo=UTC),
            query=f"snapshot:{scheduled_date}",
            payload_sha256=suffix * 64,
        )


class FakeValuationSource:
    def __init__(self, *, missing_date: bool = False) -> None:
        self.missing_date = missing_date

    def get_stock_valuation(self, symbol):
        days = [date(2020, 3, 31), date(2020, 6, 30)]
        if self.missing_date and symbol == "000001":
            days = days[1:]
        return [make_valuation(symbol, day) for day in days]


def test_builder_creates_audited_point_in_time_manifest() -> None:
    manifest = AuditableStockUniverseBuilder(
        FakeHistoricalSource(), FakeValuationSource()
    ).build(
        index_code="000300.SH",
        start=date(2020, 1, 1),
        end=date(2020, 6, 30),
        sample_size=10,
    )

    assert manifest["schema_version"] == "stock-universe-2"
    assert manifest["audit"]["selected_unique_stocks"] == 11
    assert manifest["audit"]["selected_index_exits"] == 1
    assert manifest["audit"]["stock_status_filter"] is None
    assert {item["code"] for item in manifest["entries"]} == {
        f"{code:06d}" for code in range(1, 12)
    }
    first = manifest["entries"][0]
    assert first["memberships"][0]["source_as_of"] == "2020-03-31"
    assert len(first["memberships"][0]["payload_sha256"]) == 64
    assert first["industries"][0]["board_code"] == "BK0001"


def test_builder_rejects_missing_exact_date_industry() -> None:
    builder = AuditableStockUniverseBuilder(
        FakeHistoricalSource(), FakeValuationSource(missing_date=True)
    )

    with pytest.raises(BacktestDataError, match="exact-date historical industry"):
        builder.build(
            index_code="000300.SH",
            start=date(2020, 1, 1),
            end=date(2020, 6, 30),
            sample_size=10,
        )


def test_dolthub_source_pins_queries_to_commit() -> None:
    commit = "a" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        sql = request.url.params["q"]
        assert f"AS OF '{commit}'" in sql
        if "MAX(trade_date)" in sql:
            rows = [{"trade_date": "2020-03-31"}]
        else:
            rows = [
                {
                    "stock_code": f"{code:06d}.SZ",
                    "weight": "1.0",
                    "list_date": "2010-01-01",
                    "delist_date": None,
                }
                for code in range(1, 11)
            ]
        return httpx.Response(
            200,
            json={"query_execution_status": "Success", "rows": rows},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    snapshot = DoltHubHistoricalIndexSource(commit=commit, client=client).snapshot_on_or_before(
        "000300.SH", date(2020, 3, 31)
    )

    assert snapshot.date == date(2020, 3, 31)
    assert snapshot.source_revision == commit
    assert len(snapshot.members) == 10
    assert len(snapshot.payload_sha256) == 64


def test_stable_sample_does_not_depend_on_input_order() -> None:
    members = tuple(make_member(code) for code in range(1, 21))

    forward = stable_member_sample(members, 10, "seed")
    reverse = stable_member_sample(tuple(reversed(members)), 10, "seed")

    assert [item.code for item in forward] == [item.code for item in reverse]


def make_member(number: int) -> HistoricalIndexMember:
    code = f"{number:06d}"
    return HistoricalIndexMember(
        code=code,
        source_code=f"{code}.SZ",
        weight=1.0,
        list_date=date(2010, 1, 1),
        delist_date=None,
    )


def make_valuation(code: str, day: date) -> StockValuationPoint:
    return StockValuationPoint(
        date=day,
        code=code,
        name=f"Stock {code}",
        close=10.0,
        market_cap=100.0,
        pe_ttm=10.0,
        pe_static=10.0,
        pb=1.0,
        ps_ttm=1.0,
        pcf_ocf_ttm=10.0,
        peg=1.0,
        raw={"SECURITY_CODE": code, "TRADE_DATE": day.isoformat()},
        board_code="BK0001",
        board_name="测试行业",
        original_board_code="OLD",
    )
