from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx

from market_lens.backtesting.collector import generate_rebalance_dates
from market_lens.backtesting.models import BacktestDataError
from market_lens.data.eastmoney import EastmoneyClient, EastmoneyError
from market_lens.types import StockValuationPoint

DOLTHUB_API = "https://www.dolthub.com/api/v1alpha1/chenditc/investment_data/master"
PILOT_DOLT_COMMIT = "d8uuk1008bb6kps34g0389kjn3fgn00l"
UNIVERSE_BUILDER_VERSION = "dolthub-csi-snapshot-v1"


@dataclass(frozen=True)
class HistoricalIndexMember:
    code: str
    source_code: str
    weight: float
    list_date: date
    delist_date: date | None


@dataclass(frozen=True)
class HistoricalIndexSnapshot:
    date: date
    members: tuple[HistoricalIndexMember, ...]
    source: str
    source_revision: str
    retrieved_at: datetime
    query: str
    payload_sha256: str


class HistoricalIndexSource(Protocol):
    def snapshot_on_or_before(
        self, index_code: str, scheduled_date: date
    ) -> HistoricalIndexSnapshot: ...


class HistoricalValuationSource(Protocol):
    def get_stock_valuation(self, symbol: str) -> list[StockValuationPoint]: ...


class DoltHubHistoricalIndexSource:
    def __init__(
        self,
        *,
        commit: str = PILOT_DOLT_COMMIT,
        client: httpx.Client | None = None,
    ) -> None:
        if not re.fullmatch(r"[0-9a-v]{32}", commit):
            raise ValueError("Dolt commit must be a 32-character hash")
        self.commit = commit
        self.client = client or httpx.Client(
            timeout=45.0,
            follow_redirects=True,
            headers={"User-Agent": "market-lens/0.1 historical-universe"},
        )

    def snapshot_on_or_before(
        self, index_code: str, scheduled_date: date
    ) -> HistoricalIndexSnapshot:
        normalized_index = validate_index_code(index_code)
        earliest = scheduled_date - timedelta(days=7)
        date_sql = (
            "SELECT MAX(trade_date) AS trade_date "
            f"FROM ts_index_weight AS OF '{self.commit}' "
            f"WHERE index_code='{normalized_index}' "
            f"AND trade_date BETWEEN '{earliest.isoformat()}' "
            f"AND '{scheduled_date.isoformat()}'"
        )
        date_payload, _ = self._query(date_sql)
        date_rows = date_payload.get("rows") or []
        if len(date_rows) != 1 or not date_rows[0].get("trade_date"):
            raise BacktestDataError(
                f"no historical index snapshot within 7 days of {scheduled_date}"
            )
        snapshot_date = date.fromisoformat(str(date_rows[0]["trade_date"])[:10])
        snapshot_sql = (
            "SELECT w.stock_code,w.weight,s.list_date,s.delist_date "
            f"FROM ts_index_weight AS OF '{self.commit}' w "
            f"LEFT JOIN ts_a_stock_list AS OF '{self.commit}' s "
            "ON w.stock_code=s.ts_code "
            f"WHERE w.index_code='{normalized_index}' "
            f"AND w.trade_date='{snapshot_date.isoformat()}' "
            "ORDER BY w.stock_code"
        )
        payload, payload_bytes = self._query(snapshot_sql)
        rows = payload.get("rows") or []
        members = tuple(parse_index_member(row) for row in rows)
        if len(members) < 10:
            raise BacktestDataError(
                f"historical index snapshot is unexpectedly small: {len(members)}"
            )
        codes = [item.code for item in members]
        if len(codes) != len(set(codes)):
            raise BacktestDataError("historical index snapshot contains duplicate members")
        return HistoricalIndexSnapshot(
            date=snapshot_date,
            members=members,
            source="dolthub://chenditc/investment_data/ts_index_weight",
            source_revision=self.commit,
            retrieved_at=datetime.now(UTC),
            query=snapshot_sql,
            payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        )

    def _query(self, sql: str) -> tuple[dict[str, Any], bytes]:
        try:
            response = self.client.get(DOLTHUB_API, params={"q": sql})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise BacktestDataError(f"DoltHub query failed: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("query_execution_status") != "Success":
            message = payload.get("query_execution_message") if isinstance(payload, dict) else None
            raise BacktestDataError(f"DoltHub query failed: {message or 'invalid response'}")
        return payload, response.content


class AuditableStockUniverseBuilder:
    def __init__(
        self,
        historical_source: HistoricalIndexSource | None = None,
        valuation_source: HistoricalValuationSource | None = None,
    ) -> None:
        self.historical_source = historical_source or DoltHubHistoricalIndexSource()
        self.valuation_source = valuation_source or EastmoneyClient()

    def build(
        self,
        *,
        index_code: str,
        start: date,
        end: date,
        frequency: str = "quarterly",
        sample_size: int = 20,
        seed: str = "market-lens-pilot-v1",
    ) -> dict[str, Any]:
        if end < start:
            raise BacktestDataError("universe end date cannot precede start date")
        if frequency not in {"monthly", "quarterly"}:
            raise ValueError("frequency must be monthly or quarterly")
        if sample_size < 10:
            raise ValueError("sample_size must be at least 10")
        schedules = generate_rebalance_dates(start, end, frequency)  # type: ignore[arg-type]
        if len(schedules) < 2:
            raise BacktestDataError("at least two historical snapshots are required")

        snapshots: list[HistoricalIndexSnapshot] = []
        selected: dict[str, dict[date, HistoricalIndexMember]] = {}
        for scheduled_date in schedules:
            snapshot = self.historical_source.snapshot_on_or_before(
                index_code, scheduled_date
            )
            if sample_size > len(snapshot.members):
                raise BacktestDataError(
                    f"sample_size {sample_size} exceeds population {len(snapshot.members)}"
                )
            snapshots.append(snapshot)
            for member in stable_member_sample(snapshot.members, sample_size, seed):
                selected.setdefault(member.code, {})[snapshot.date] = member

        entries: list[dict[str, Any]] = []
        for code in sorted(selected):
            try:
                valuations = self.valuation_source.get_stock_valuation(code)
            except (EastmoneyError, KeyError, TypeError, ValueError) as exc:
                raise BacktestDataError(
                    f"failed to load historical industry evidence for {code}: {exc}"
                ) from exc
            valuations_by_date = {item.date: item for item in valuations}
            memberships: list[dict[str, Any]] = []
            industries: list[dict[str, Any]] = []
            entry_name: str | None = None
            source_rows = selected[code]
            for snapshot in snapshots:
                member = source_rows.get(snapshot.date)
                if member is None:
                    continue
                valuation = valuations_by_date.get(snapshot.date)
                if valuation is None or not valuation.board_code or not valuation.board_name:
                    raise BacktestDataError(
                        f"exact-date historical industry is unavailable for {code} "
                        f"at {snapshot.date}"
                    )
                entry_name = entry_name or valuation.name
                memberships.append(
                    {
                        "start": snapshot.date.isoformat(),
                        "end": snapshot.date.isoformat(),
                        "source": snapshot.source,
                        "source_as_of": snapshot.date.isoformat(),
                        "retrieved_at": snapshot.retrieved_at.isoformat(),
                        "payload_sha256": snapshot.payload_sha256,
                    }
                )
                industries.append(
                    {
                        "start": snapshot.date.isoformat(),
                        "end": snapshot.date.isoformat(),
                        "em_industry": valuation.board_name,
                        "csrc_industry": None,
                        "board_code": valuation.board_code,
                        "source": "eastmoney:RPT_VALUEANALYSIS_DET",
                        "source_as_of": snapshot.date.isoformat(),
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "payload_sha256": valuation_evidence_sha256(valuation),
                    }
                )
            first_member = source_rows[min(source_rows)]
            entries.append(
                {
                    "code": code,
                    "name": entry_name,
                    "list_date": first_member.list_date.isoformat(),
                    "delist_date": (
                        first_member.delist_date.isoformat()
                        if first_member.delist_date
                        else None
                    ),
                    "memberships": memberships,
                    "industries": industries,
                }
            )

        snapshot_codes = [
            {item.code for item in sample} for sample in _selected_by_date(selected)
        ]
        exits = set().union(
            *(left - right for left, right in zip(snapshot_codes, snapshot_codes[1:], strict=False))
        )
        revisions = {item.source_revision for item in snapshots}
        if len(revisions) != 1:
            raise BacktestDataError("historical snapshots use mixed source revisions")
        generated_at = datetime.now(UTC)
        return {
            "schema_version": "stock-universe-2",
            "name": f"{index_code} historical {frequency} stable-hash sample",
            "source": snapshots[0].source,
            "source_revision": revisions.pop(),
            "generated_at": generated_at.isoformat(),
            "selection_method": UNIVERSE_BUILDER_VERSION,
            "point_in_time_verified": True,
            "includes_delisted": True,
            "historical_industry_verified": True,
            "audit": {
                "index_code": validate_index_code(index_code),
                "scheduled_start": start.isoformat(),
                "scheduled_end": end.isoformat(),
                "frequency": frequency,
                "sample_size_per_snapshot": sample_size,
                "selection_seed": seed,
                "selection_rule": "lowest sha256(seed|stock_code) among each historical snapshot",
                "stock_status_filter": None,
                "future_price_filter": None,
                "snapshot_dates": [item.date.isoformat() for item in snapshots],
                "population_sizes": [len(item.members) for item in snapshots],
                "snapshot_payload_sha256": [item.payload_sha256 for item in snapshots],
                "selected_unique_stocks": len(entries),
                "selected_index_exits": len(exits),
                "selected_delisted_stocks": sum(
                    1 for item in entries if item["delist_date"] is not None
                ),
                "industry_rule": "exact-date Eastmoney valuation row board code and name",
            },
            "entries": entries,
        }


def stable_member_sample(
    members: tuple[HistoricalIndexMember, ...], sample_size: int, seed: str
) -> tuple[HistoricalIndexMember, ...]:
    ranked = sorted(
        members,
        key=lambda item: (
            hashlib.sha256(f"{seed}|{item.code}".encode()).hexdigest(),
            item.code,
        ),
    )
    return tuple(ranked[:sample_size])


def parse_index_member(value: Any) -> HistoricalIndexMember:
    if not isinstance(value, dict):
        raise BacktestDataError("historical index member must be an object")
    source_code = str(value.get("stock_code") or "").strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", source_code)
    if match is None:
        raise BacktestDataError(f"invalid historical stock code: {source_code!r}")
    if not value.get("list_date"):
        raise BacktestDataError(f"listing date is missing for {source_code}")
    try:
        weight = float(value.get("weight"))
    except (TypeError, ValueError) as exc:
        raise BacktestDataError(f"invalid historical weight for {source_code}") from exc
    if weight <= 0:
        raise BacktestDataError(f"historical weight must be positive for {source_code}")
    return HistoricalIndexMember(
        code=match.group(1),
        source_code=source_code,
        weight=weight,
        list_date=date.fromisoformat(str(value["list_date"])[:10]),
        delist_date=(
            date.fromisoformat(str(value["delist_date"])[:10])
            if value.get("delist_date")
            else None
        ),
    )


def validate_index_code(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"\d{6}\.(SH|SZ|CSI)", normalized):
        raise ValueError(f"invalid index code: {value!r}")
    return normalized


def valuation_evidence_sha256(value: StockValuationPoint) -> str:
    evidence = {
        "date": value.date.isoformat(),
        "code": value.code,
        "name": value.name,
        "board_code": value.board_code,
        "board_name": value.board_name,
        "original_board_code": value.original_board_code,
        "raw": value.raw,
    }
    encoded = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _selected_by_date(
    selected: dict[str, dict[date, HistoricalIndexMember]],
) -> list[tuple[HistoricalIndexMember, ...]]:
    dates = sorted({day for rows in selected.values() for day in rows})
    return [
        tuple(rows[day] for rows in selected.values() if day in rows)
        for day in dates
    ]
