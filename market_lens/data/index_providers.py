from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

OfficialIndexProviderKey = Literal["cnindex", "csindex"]


@dataclass(frozen=True)
class OfficialIndexProviderCapabilities:
    key: OfficialIndexProviderKey
    official_identity: bool
    top_constituent_weights: bool
    full_constituent_weights: bool
    valuation_history: bool


CNINDEX_CAPABILITIES = OfficialIndexProviderCapabilities(
    key="cnindex",
    official_identity=True,
    top_constituent_weights=True,
    full_constituent_weights=False,
    valuation_history=False,
)

CSINDEX_CAPABILITIES = OfficialIndexProviderCapabilities(
    key="csindex",
    official_identity=True,
    top_constituent_weights=True,
    full_constituent_weights=True,
    valuation_history=True,
)


def normalize_official_index_code(code: str) -> str:
    normalized = str(code).strip().upper()
    if not re.fullmatch(r"[A-Z0-9.]+", normalized):
        raise ValueError(f"Invalid official index code: {code!r}")
    return normalized


def official_index_provider_capabilities(
    code: str,
) -> OfficialIndexProviderCapabilities:
    normalized = normalize_official_index_code(code)
    if re.fullmatch(r"399\d{3}", normalized):
        return CNINDEX_CAPABILITIES
    return CSINDEX_CAPABILITIES


def official_index_constituent_provider(code: str) -> OfficialIndexProviderKey:
    return official_index_provider_capabilities(code).key


def official_index_valuation_provider(
    code: str,
) -> OfficialIndexProviderKey | None:
    capabilities = official_index_provider_capabilities(code)
    return capabilities.key if capabilities.valuation_history else None
