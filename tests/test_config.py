from __future__ import annotations

import pytest

from market_lens.config import Settings


def test_development_allows_ephemeral_approval_signing_key() -> None:
    Settings(
        env="development",
        tool_approval_signing_key_configured=False,
    ).validate_runtime()


def test_production_requires_configured_approval_signing_key() -> None:
    with pytest.raises(ValueError, match="SIGNING_KEY is required"):
        Settings(
            env="production",
            tool_approval_signing_key_configured=False,
        ).validate_runtime()


def test_production_accepts_configured_approval_signing_key() -> None:
    Settings(
        env="production",
        tool_approval_signing_key="x" * 32,
        tool_approval_signing_key_configured=True,
    ).validate_runtime()


@pytest.mark.parametrize("ttl", [29, 3601])
def test_runtime_rejects_unsafe_approval_ttl(ttl: int) -> None:
    with pytest.raises(ValueError, match="TTL_SECONDS must be between"):
        Settings(tool_approval_ttl_seconds=ttl).validate_runtime()
