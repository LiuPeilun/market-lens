from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator

from market_lens.tools.models import ToolInput, ToolOutput


class SearchAssetsInput(ToolInput):
    keyword: str = Field(min_length=1, max_length=120)
    asset_type: Literal["stock", "fund"] | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("keyword must not be blank")
        return normalized


class AssetSearchItem(ToolOutput):
    asset_type: Literal["stock", "fund"]
    code: str
    name: str
    market: str | None = None
    quote_id: str | None = None
    source_type: str | None = None


class SearchAssetsOutput(ToolOutput):
    keyword: str
    count: int
    items: list[AssetSearchItem]


class AnalyzeAssetInput(ToolInput):
    asset_type: Literal["stock", "fund"]
    code: str = Field(min_length=1, max_length=32)
    start: date
    end: date

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("code must not be blank")
        return normalized


class AnalyzeAssetOutput(ToolOutput):
    result: dict[str, Any]
