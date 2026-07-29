from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from market_lens.api.app import health
from market_lens.data import eastmoney
from market_lens.data.eastmoney import (
    EastmoneyClient,
    EastmoneyError,
    SourceCircuitOpenError,
    source_health_registry,
)
from market_lens.data.source_health import SourceHealthRegistry


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, *, ttl_seconds: int) -> str | None:
        del ttl_seconds
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def test_source_health_opens_probes_and_recovers() -> None:
    clock = MutableClock()
    registry = SourceHealthRegistry(
        failure_threshold=2,
        recovery_seconds=30,
        clock=clock,
    )

    assert registry.acquire_request("API.EXAMPLE.COM") is True
    registry.record_failure("api.example.com", error_code="connect_error")
    assert registry.acquire_request("api.example.com") is True
    registry.record_failure("api.example.com", error_code="timeout")

    opened = registry.snapshot()
    source = opened["sources"][0]
    assert opened["status"] == "unavailable"
    assert source["source"] == "api.example.com"
    assert source["circuit_state"] == "open"
    assert source["consecutive_failures"] == 2
    assert source["total_requests"] == 2
    assert source["success_rate"] == 0.0
    assert source["last_success_at"] is None
    assert source["last_error_code"] == "timeout"

    assert registry.acquire_request("api.example.com") is False
    clock.advance(seconds=30)
    assert registry.acquire_request("api.example.com") is True
    assert registry.acquire_request("api.example.com") is False

    half_open = registry.snapshot()["sources"][0]
    assert half_open["circuit_state"] == "half_open"
    assert half_open["rejected_requests"] == 2

    registry.record_success("api.example.com")
    recovered = registry.snapshot()
    source = recovered["sources"][0]
    assert recovered["status"] == "healthy"
    assert source["circuit_state"] == "closed"
    assert source["consecutive_failures"] == 0
    assert source["successful_requests"] == 1
    assert source["success_rate"] == pytest.approx(1 / 3, abs=0.0001)
    assert source["last_success_at"] == clock.now.isoformat()
    assert source["last_error_code"] is None


def test_half_open_failure_reopens_circuit() -> None:
    clock = MutableClock()
    registry = SourceHealthRegistry(
        failure_threshold=1,
        recovery_seconds=10,
        clock=clock,
    )
    registry.record_failure("api.example.com", error_code="connect_error")
    clock.advance(seconds=10)

    assert registry.acquire_request("api.example.com") is True
    registry.record_failure("api.example.com", error_code="protocol_error")

    source = registry.snapshot()["sources"][0]
    assert source["circuit_state"] == "open"
    assert source["consecutive_failures"] == 2
    assert source["next_probe_at"] == (clock.now + timedelta(seconds=10)).isoformat()


def test_one_open_source_degrades_but_does_not_mark_all_sources_unavailable() -> None:
    registry = SourceHealthRegistry(failure_threshold=1, recovery_seconds=10)
    registry.record_failure("failed.example.com", error_code="connect_error")
    registry.record_success("healthy.example.com")

    assert registry.snapshot()["status"] == "degraded"


def test_eastmoney_transport_records_one_failure_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SourceHealthRegistry(failure_threshold=1, recovery_seconds=60)
    request_count = 0

    class FailingHttpClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FailingHttpClient:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def get(self, url: str) -> None:
            nonlocal request_count
            request_count += 1
            raise httpx.ConnectError(f"private failure detail for {url}")

    monkeypatch.setattr(eastmoney.httpx, "Client", FailingHttpClient)
    monkeypatch.setattr(eastmoney.time, "sleep", lambda seconds: None)
    client = EastmoneyClient(
        cache=MemoryCache(),  # type: ignore[arg-type]
        snapshot_store=object(),  # type: ignore[arg-type]
        source_health=registry,
    )
    client.retries = 2
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    with pytest.raises(EastmoneyError, match="after 3 attempts"):
        client._get_text(url, ttl_seconds=1)
    with pytest.raises(SourceCircuitOpenError, match="circuit is open"):
        client._get_text(url, ttl_seconds=1)

    source = registry.snapshot()["sources"][0]
    assert request_count == 3
    assert source["total_requests"] == 1
    assert source["failed_requests"] == 1
    assert source["rejected_requests"] == 1
    assert source["last_error_code"] == "connect_error"
    assert "private failure detail" not in str(source)


def test_health_endpoint_exposes_safe_source_diagnostics() -> None:
    source_health_registry.reset()
    try:
        source_health_registry.record_failure(
            "push2his.eastmoney.com",
            error_code="protocol_error",
        )

        payload = health()
        source = payload["data_sources"]["sources"][0]

        assert payload["data_source_status"] == "degraded"
        assert source["source"] == "push2his.eastmoney.com"
        assert source["consecutive_failures"] == 1
        assert source["last_failure_at"] is not None
        assert source["circuit_state"] == "closed"
        assert source["last_error_code"] == "protocol_error"
    finally:
        source_health_registry.reset()
