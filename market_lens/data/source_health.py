from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Literal

CircuitState = Literal["closed", "open", "half_open"]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SourceHealthRecord:
    source: str
    circuit_state: CircuitState = "closed"
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0
    consecutive_failures: int = 0
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    opened_at: datetime | None = None
    next_probe_at: datetime | None = None
    last_error_code: str | None = None
    probe_in_flight: bool = False


class SourceHealthRegistry:
    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_seconds < 0:
            raise ValueError("recovery_seconds must not be negative")
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._records: dict[str, SourceHealthRecord] = {}
        self._lock = RLock()

    def acquire_request(self, source: str) -> bool:
        normalized_source = normalize_source(source)
        with self._lock:
            record = self._record(normalized_source)
            now = self._clock()
            if record.circuit_state == "closed":
                return True
            if (
                record.circuit_state == "open"
                and record.next_probe_at is not None
                and now >= record.next_probe_at
            ):
                record.circuit_state = "half_open"
                record.probe_in_flight = True
                return True
            record.rejected_requests += 1
            return False

    def record_success(self, source: str) -> None:
        normalized_source = normalize_source(source)
        with self._lock:
            record = self._record(normalized_source)
            now = self._clock()
            record.total_requests += 1
            record.successful_requests += 1
            record.consecutive_failures = 0
            record.last_attempt_at = now
            record.last_success_at = now
            record.circuit_state = "closed"
            record.opened_at = None
            record.next_probe_at = None
            record.last_error_code = None
            record.probe_in_flight = False

    def record_failure(self, source: str, *, error_code: str) -> None:
        normalized_source = normalize_source(source)
        with self._lock:
            record = self._record(normalized_source)
            now = self._clock()
            record.total_requests += 1
            record.failed_requests += 1
            record.consecutive_failures += 1
            record.last_attempt_at = now
            record.last_failure_at = now
            record.last_error_code = error_code
            should_open = (
                record.circuit_state == "half_open"
                or record.consecutive_failures >= self.failure_threshold
            )
            if should_open:
                record.circuit_state = "open"
                record.opened_at = now
                record.next_probe_at = now + timedelta(seconds=self.recovery_seconds)
            else:
                record.circuit_state = "closed"
                record.opened_at = None
                record.next_probe_at = None
            record.probe_in_flight = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            sources = [self._serialize(record) for record in self._records.values()]
        sources.sort(key=lambda item: item["source"])
        states = {item["circuit_state"] for item in sources}
        if not sources:
            status = "unknown"
        elif states == {"open"}:
            status = "unavailable"
        elif states.intersection({"open", "half_open"}) or any(
            item["consecutive_failures"] > 0 for item in sources
        ):
            status = "degraded"
        else:
            status = "healthy"
        return {
            "status": status,
            "failure_threshold": self.failure_threshold,
            "recovery_seconds": self.recovery_seconds,
            "source_count": len(sources),
            "sources": sources,
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()

    def _record(self, source: str) -> SourceHealthRecord:
        record = self._records.get(source)
        if record is None:
            record = SourceHealthRecord(source=source)
            self._records[source] = record
        return record

    @staticmethod
    def _serialize(record: SourceHealthRecord) -> dict[str, Any]:
        success_rate = (
            record.successful_requests / record.total_requests
            if record.total_requests
            else None
        )
        return {
            "source": record.source,
            "circuit_state": record.circuit_state,
            "total_requests": record.total_requests,
            "successful_requests": record.successful_requests,
            "failed_requests": record.failed_requests,
            "rejected_requests": record.rejected_requests,
            "success_rate": round(success_rate, 4) if success_rate is not None else None,
            "consecutive_failures": record.consecutive_failures,
            "last_attempt_at": isoformat_or_none(record.last_attempt_at),
            "last_success_at": isoformat_or_none(record.last_success_at),
            "last_failure_at": isoformat_or_none(record.last_failure_at),
            "opened_at": isoformat_or_none(record.opened_at),
            "next_probe_at": isoformat_or_none(record.next_probe_at),
            "last_error_code": record.last_error_code,
        }


def normalize_source(source: str) -> str:
    normalized = source.strip().lower()
    if not normalized:
        raise ValueError("source must not be empty")
    return normalized


def isoformat_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
