from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
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

    def contains(self, value: date) -> bool:
        return self.start <= value and (self.end is None or value <= self.end)


@dataclass(frozen=True)
class IndustryPeriod:
    start: date
    end: date | None
    em_industry: str | None
    csrc_industry: str | None
    source: str

    def contains(self, value: date) -> bool:
        return self.start <= value and (self.end is None or value <= self.end)


@dataclass(frozen=True)
class StockUniverseEntry:
    code: str
    name: str | None
    memberships: tuple[MembershipPeriod, ...]
    industries: tuple[IndustryPeriod, ...]

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

    def validate_for_collection(self, dates: list[date]) -> None:
        if self.schema_version != "stock-universe-1":
            raise BacktestDataError("stock universe schema_version must be stock-universe-1")
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
        codes = [item.code for item in self.entries]
        if len(codes) != len(set(codes)):
            raise BacktestDataError("stock universe contains duplicate stock codes")
        for entry in self.entries:
            validate_periods(entry.memberships, f"membership periods for {entry.code}")
            validate_periods(entry.industries, f"industry periods for {entry.code}")
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
    )


def parse_membership(value: Any, code: str) -> MembershipPeriod:
    if not isinstance(value, dict):
        raise BacktestDataError(f"membership period for {code} must be an object")
    return MembershipPeriod(
        start=parse_required_date(value.get("start"), f"membership start for {code}"),
        end=parse_optional_date(value.get("end")),
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
    )


def validate_periods(periods: tuple[Any, ...], label: str) -> None:
    ordered = sorted(periods, key=lambda item: item.start)
    for item in ordered:
        if item.end is not None and item.end < item.start:
            raise BacktestDataError(f"invalid {label}: end precedes start")
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.end is None or current.start <= previous.end:
            raise BacktestDataError(f"overlapping {label}")
