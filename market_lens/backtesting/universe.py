from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from market_lens.backtesting.models import (
    BacktestDataError,
    parse_optional_date,
    parse_required_date,
)


@dataclass(frozen=True)
class MembershipPeriod:
    start: date
    end: date | None
    source: str | None = None
    source_as_of: date | None = None
    retrieved_at: datetime | None = None
    payload_sha256: str | None = None

    def contains(self, value: date) -> bool:
        return self.start <= value and (self.end is None or value <= self.end)


@dataclass(frozen=True)
class IndustryPeriod:
    start: date
    end: date | None
    em_industry: str | None
    csrc_industry: str | None
    source: str
    board_code: str | None = None
    source_as_of: date | None = None
    retrieved_at: datetime | None = None
    payload_sha256: str | None = None

    def contains(self, value: date) -> bool:
        return self.start <= value and (self.end is None or value <= self.end)


@dataclass(frozen=True)
class StockUniverseEntry:
    code: str
    name: str | None
    memberships: tuple[MembershipPeriod, ...]
    industries: tuple[IndustryPeriod, ...]
    list_date: date | None = None
    delist_date: date | None = None

    def is_member(self, value: date) -> bool:
        return any(item.contains(value) for item in self.memberships)

    def membership_at(self, value: date) -> MembershipPeriod | None:
        matches = [item for item in self.memberships if item.contains(value)]
        if len(matches) > 1:
            raise BacktestDataError(f"overlapping membership periods for {self.code} at {value}")
        return matches[0] if matches else None

    def industry_at(self, value: date) -> IndustryPeriod | None:
        matches = [item for item in self.industries if item.contains(value)]
        if len(matches) > 1:
            raise BacktestDataError(f"overlapping industry periods for {self.code} at {value}")
        return matches[0] if matches else None


@dataclass(frozen=True)
class StockUniverseManifest:
    schema_version: str
    name: str
    source: str
    point_in_time_verified: bool
    includes_delisted: bool
    historical_industry_verified: bool
    entries: tuple[StockUniverseEntry, ...]
    source_revision: str | None = None
    generated_at: datetime | None = None
    selection_method: str | None = None

    def validate_for_collection(self, dates: list[date]) -> None:
        if self.schema_version not in {"stock-universe-1", "stock-universe-2"}:
            raise BacktestDataError(
                "stock universe schema_version must be stock-universe-1 or stock-universe-2"
            )
        if not self.source.strip():
            raise BacktestDataError("stock universe source is required")
        if not self.point_in_time_verified:
            raise BacktestDataError("stock universe must be point-in-time verified")
        if not self.includes_delisted:
            raise BacktestDataError("stock universe must include delisted members")
        if not self.historical_industry_verified:
            raise BacktestDataError("historical industry classifications must be verified")
        if not self.entries:
            raise BacktestDataError("stock universe cannot be empty")
        if self.schema_version == "stock-universe-2":
            if not self.source_revision:
                raise BacktestDataError("stock-universe-2 source_revision is required")
            if self.generated_at is None:
                raise BacktestDataError("stock-universe-2 generated_at is required")
            if not self.selection_method:
                raise BacktestDataError("stock-universe-2 selection_method is required")
        codes = [item.code for item in self.entries]
        if len(codes) != len(set(codes)):
            raise BacktestDataError("stock universe contains duplicate stock codes")
        for entry in self.entries:
            validate_periods(entry.memberships, f"membership periods for {entry.code}")
            validate_periods(entry.industries, f"industry periods for {entry.code}")
            if (
                entry.list_date is not None
                and entry.delist_date is not None
                and entry.delist_date < entry.list_date
            ):
                raise BacktestDataError(f"invalid listing dates for {entry.code}")
            if self.schema_version == "stock-universe-2":
                for membership in entry.memberships:
                    validate_period_evidence(membership, entry.code, "membership")
                for industry in entry.industries:
                    validate_period_evidence(industry, entry.code, "industry")
                    if not industry.board_code:
                        raise BacktestDataError(
                            f"historical industry board_code is required for {entry.code}"
                        )
            for value in dates:
                if entry.is_member(value) and entry.industry_at(value) is None:
                    raise BacktestDataError(
                        f"historical industry is missing for {entry.code} at {value}"
                    )


def load_stock_universe_manifest(path: Path) -> StockUniverseManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BacktestDataError(f"failed to read stock universe manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise BacktestDataError("stock universe manifest root must be an object")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise BacktestDataError("stock universe entries must be an array")
    return StockUniverseManifest(
        schema_version=str(payload.get("schema_version") or ""),
        name=str(payload.get("name") or "").strip(),
        source=str(payload.get("source") or "").strip(),
        point_in_time_verified=payload.get("point_in_time_verified") is True,
        includes_delisted=payload.get("includes_delisted") is True,
        historical_industry_verified=payload.get("historical_industry_verified") is True,
        entries=tuple(parse_universe_entry(item) for item in raw_entries),
        source_revision=optional_text(payload.get("source_revision")),
        generated_at=parse_optional_datetime(payload.get("generated_at")),
        selection_method=optional_text(payload.get("selection_method")),
    )


def parse_universe_entry(value: Any) -> StockUniverseEntry:
    if not isinstance(value, dict):
        raise BacktestDataError("stock universe entry must be an object")
    code = str(value.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise BacktestDataError(f"invalid stock universe code: {code!r}")
    raw_memberships = value.get("memberships")
    raw_industries = value.get("industries")
    if not isinstance(raw_memberships, list) or not raw_memberships:
        raise BacktestDataError(f"membership periods are required for {code}")
    if not isinstance(raw_industries, list) or not raw_industries:
        raise BacktestDataError(f"industry periods are required for {code}")
    return StockUniverseEntry(
        code=code,
        name=str(value.get("name")) if value.get("name") else None,
        memberships=tuple(parse_membership(item, code) for item in raw_memberships),
        industries=tuple(parse_industry(item, code) for item in raw_industries),
        list_date=parse_optional_date(value.get("list_date")),
        delist_date=parse_optional_date(value.get("delist_date")),
    )


def parse_membership(value: Any, code: str) -> MembershipPeriod:
    if not isinstance(value, dict):
        raise BacktestDataError(f"membership period for {code} must be an object")
    return MembershipPeriod(
        start=parse_required_date(value.get("start"), f"membership start for {code}"),
        end=parse_optional_date(value.get("end")),
        source=optional_text(value.get("source")),
        source_as_of=parse_optional_date(value.get("source_as_of")),
        retrieved_at=parse_optional_datetime(value.get("retrieved_at")),
        payload_sha256=optional_text(value.get("payload_sha256")),
    )


def parse_industry(value: Any, code: str) -> IndustryPeriod:
    if not isinstance(value, dict):
        raise BacktestDataError(f"industry period for {code} must be an object")
    source = str(value.get("source") or "").strip()
    if not source:
        raise BacktestDataError(f"industry source is required for {code}")
    em_industry = str(value.get("em_industry")) if value.get("em_industry") else None
    csrc_industry = str(value.get("csrc_industry")) if value.get("csrc_industry") else None
    if not em_industry and not csrc_industry:
        raise BacktestDataError(f"industry classification is required for {code}")
    return IndustryPeriod(
        start=parse_required_date(value.get("start"), f"industry start for {code}"),
        end=parse_optional_date(value.get("end")),
        em_industry=em_industry,
        csrc_industry=csrc_industry,
        source=source,
        board_code=optional_text(value.get("board_code")),
        source_as_of=parse_optional_date(value.get("source_as_of")),
        retrieved_at=parse_optional_datetime(value.get("retrieved_at")),
        payload_sha256=optional_text(value.get("payload_sha256")),
    )


def validate_periods(periods: tuple[Any, ...], label: str) -> None:
    ordered = sorted(periods, key=lambda item: item.start)
    for item in ordered:
        if item.end is not None and item.end < item.start:
            raise BacktestDataError(f"invalid {label}: end precedes start")
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.end is None or current.start <= previous.end:
            raise BacktestDataError(f"overlapping {label}")


def validate_period_evidence(value: Any, code: str, label: str) -> None:
    source = optional_text(getattr(value, "source", None))
    source_as_of = getattr(value, "source_as_of", None)
    retrieved_at = getattr(value, "retrieved_at", None)
    payload_sha256 = optional_text(getattr(value, "payload_sha256", None))
    if not source or source_as_of is None or retrieved_at is None:
        raise BacktestDataError(f"{label} evidence is incomplete for {code}")
    if source_as_of > value.start:
        raise BacktestDataError(f"{label} evidence is from the future for {code}")
    if not payload_sha256 or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        raise BacktestDataError(f"{label} payload_sha256 is invalid for {code}")


def parse_optional_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise BacktestDataError(f"expected ISO datetime, got {value!r}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BacktestDataError(f"invalid ISO datetime: {value}") from exc


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
