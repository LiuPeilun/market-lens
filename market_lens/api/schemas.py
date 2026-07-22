from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FlexibleResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AssessmentFactor(FlexibleResponseModel):
    key: str | None = None
    name: str | None = None
    category: Literal["valuation", "quality", "product"] | None = None
    value: Any = None
    unit: str | None = None
    source_as_of: date | None = None
    score: float | None = None
    direction: str | None = None
    normalization: str | None = None
    weight: float | None = None
    effective_weight: float | None = None
    sample_size: int | None = None
    coverage: float | None = None
    source: str | None = None
    status: str | None = None
    reason: str | None = None


class AssessmentDimension(FlexibleResponseModel):
    model: str
    score: float | None = None
    level: str
    level_zh: str
    confidence: float = 0.0
    factors: list[AssessmentFactor] = Field(default_factory=list)
    weight_coverage: float = 0.0
    data_coverage: float = 0.0
    sample_adequacy: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class AssessmentDimensions(BaseModel):
    valuation: AssessmentDimension
    quality: AssessmentDimension
    product: AssessmentDimension | None = None


class AssessmentDataQuality(FlexibleResponseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_as_of: date | None = None
    retrieved_at: str | None = None


class ValuationAssessment(FlexibleResponseModel):
    schema_version: str
    model_version: str
    profile: str
    analysis_as_of: date | None = None
    dimensions: AssessmentDimensions
    overall_confidence: float = 0.0
    attractiveness: float | None = None
    confidence_detail: dict[str, Any] = Field(default_factory=dict)
    data_quality: AssessmentDataQuality
    routing: dict[str, Any] | None = None


class AnalysisResult(FlexibleResponseModel):
    asset_type: Literal["stock", "fund"]
    code: str
    name: str | None = None
    as_of: date | None = None
    valuation: dict[str, Any]
    performance: dict[str, Any]
    notes: list[str] = Field(default_factory=list)
    assessment: ValuationAssessment | None = None
    research: dict[str, Any] | None = None


class AnalyzeRequest(BaseModel):
    asset_type: Literal["stock", "fund"]
    code: str = Field(min_length=1)
    start: date
    end: date | None = None


class AnalyzeResponse(BaseModel):
    result: AnalysisResult
    analysis_id: UUID | None = None


class ChatAssetContext(BaseModel):
    asset_type: Literal["stock", "fund"]
    code: str
    name: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    context: ChatAssetContext | None = None
    start: date
    end: date | None = None
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    answer: str
    intent: str
    asset: dict[str, Any] | None = None
    analysis: AnalysisResult | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    session_id: UUID | None = None


class AnalysisHistoryItem(FlexibleResponseModel):
    id: UUID
    asset_type: Literal["stock", "fund"]
    asset_code: str
    asset_name: str | None = None
    request_params: dict[str, Any] = Field(default_factory=dict)
    result: AnalysisResult
    created_at: datetime


class AnalysisHistoryResponse(BaseModel):
    count: int
    items: list[AnalysisHistoryItem]


class ToolApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]


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
