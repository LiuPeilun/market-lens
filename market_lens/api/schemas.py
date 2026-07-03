from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    asset_type: Literal["stock", "fund"]
    code: str = Field(min_length=1)
    start: date
    end: date | None = None


class AnalyzeResponse(BaseModel):
    result: dict[str, Any]


class AssetSearchItem(BaseModel):
    asset_type: Literal["stock", "fund"]
    code: str
    name: str
    market: str | None = None
    quote_id: str | None = None
    source_type: str | None = None


class AssetSearchResponse(BaseModel):
    keyword: str
    count: int
    items: list[AssetSearchItem]


class ErrorResponse(BaseModel):
    detail: str
