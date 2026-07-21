from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient

from market_lens.api.app import (
    app,
    save_tool_approval_event,
    to_sse_data,
    verify_tool_approval_signature,
)
from market_lens.api.schemas import AnalysisHistoryResponse, AnalyzeResponse, ChatResponse
from market_lens.storage.supabase import AuthenticatedUser


def test_to_sse_data_json_encodes_dates() -> None:
    data = to_sse_data({"type": "meta", "analysis": {"as_of": date(2026, 7, 3)}})

    assert data.startswith("data: ")
    assert data.endswith("\n\n")

    payload = json.loads(data.removeprefix("data: ").strip())
    assert payload == {"type": "meta", "analysis": {"as_of": "2026-07-03"}}


def test_api_contract_accepts_v2_assessment_in_analysis_and_stream_meta() -> None:
    result = analysis_result_payload(include_assessment=True)

    response = AnalyzeResponse.model_validate({"result": result})
    chat_response = ChatResponse.model_validate(
        {
            "answer": "已完成分析",
            "intent": "explain_valuation",
            "analysis": result,
        }
    )
    stream_payload = json.loads(
        to_sse_data({"type": "meta", "analysis": result})
        .removeprefix("data: ")
        .strip()
    )

    assert response.result.assessment is not None
    assert response.result.assessment.dimensions.quality.score == 72.0
    assert chat_response.analysis is not None
    assert chat_response.analysis.assessment is not None
    assert stream_payload["analysis"]["assessment"]["dimensions"]["valuation"]["score"] == 42.0


def test_history_contract_keeps_legacy_results_compatible() -> None:
    history = AnalysisHistoryResponse.model_validate(
        {
            "count": 1,
            "items": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "asset_type": "stock",
                    "asset_code": "600519",
                    "asset_name": "贵州茅台",
                    "request_params": {},
                    "result": analysis_result_payload(include_assessment=False),
                    "created_at": "2026-07-21T12:00:00Z",
                }
            ],
        }
    )

    assert history.items[0].result.assessment is None
    assert history.items[0].result.valuation["score"] == 42.0


def analysis_result_payload(*, include_assessment: bool) -> dict:
    result = {
        "asset_type": "stock",
        "code": "600519",
        "name": "贵州茅台",
        "as_of": "2026-07-21",
        "valuation": {
            "method": "historical_percentile_multi_factor",
            "score": 42.0,
            "level": "normal",
            "level_zh": "正常估值",
            "confidence": 0.6,
        },
        "performance": {"sample_size": 100},
        "notes": [],
    }
    if include_assessment:
        dimension = {
            "model": "generic_non_financial_valuation_v1",
            "score": 42.0,
            "level": "normal",
            "level_zh": "正常估值",
            "confidence": 0.6,
            "factors": [],
            "weight_coverage": 0.8,
            "data_coverage": 0.8,
            "sample_adequacy": 1.0,
            "warnings": [],
        }
        result["assessment"] = {
            "schema_version": "2",
            "model_version": "valuation-v2.2.0-fund-product-models",
            "profile": "generic_non_financial",
            "analysis_as_of": "2026-07-21",
            "dimensions": {
                "valuation": dimension,
                "quality": {
                    **dimension,
                    "model": "generic_non_financial_quality_v1",
                    "score": 72.0,
                    "level": "high",
                    "level_zh": "较高",
                },
                "product": None,
            },
            "overall_confidence": 0.6,
            "attractiveness": None,
            "confidence_detail": {},
            "data_quality": {
                "sources": [],
                "warnings": [],
                "source_as_of": "2026-07-21",
                "retrieved_at": "2026-07-21T12:00:00Z",
            },
        }
    return result


def test_analyze_requires_authentication() -> None:
    response = TestClient(app).post(
        "/api/analyze",
        json={
            "asset_type": "stock",
            "code": "600519",
            "start": "2024-01-01",
            "end": "2026-07-15",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_approval_event_persists_checkpoint_without_exposing_it() -> None:
    captured: dict = {}

    class FakeRepository:
        def create_tool_approval(self, user, session_id, **payload):
            captured.update(
                {
                    **payload,
                    "user_id": str(user.id),
                    "session_id": str(session_id),
                }
            )
            return {
                "id": str(payload["approval_id"]),
                "tool_name": payload["tool_name"],
                "risk_level": payload["risk_level"],
                "execution_target": payload["execution_target"],
                "reason": payload["reason"],
                "input_summary": payload["input_summary"],
                "status": "pending",
                "expires_at": payload["expires_at"].isoformat(),
            }

    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "user@example.com",
        "token",
    )
    event = save_tool_approval_event(
        FakeRepository(),  # type: ignore[arg-type]
        user,
        UUID("33333333-3333-3333-3333-333333333333"),
        {
            "type": "approval_required",
            "approval": {
                "tool_name": "code.run_python",
                "tool_alias": "code__run_python",
                "tool_call_id": "call-1",
                "arguments_digest": "a" * 64,
                "input_summary": {"code": "print(1)"},
                "reason": "approval required",
                "risk": "write",
                "execution_target": "sandbox_required",
            },
            "checkpoint": {"version": 1, "messages": []},
            "citations": [],
        },
    )

    assert UUID(event["approval"]["id"])
    assert "checkpoint" not in event
    signed_row = {
        **captured,
        "id": str(captured.pop("approval_id")),
        "expires_at": captured["expires_at"].isoformat(),
    }
    assert verify_tool_approval_signature(signed_row) is True
    signed_row["checkpoint"] = {"version": 1, "messages": [{"role": "user"}]}
    assert verify_tool_approval_signature(signed_row) is False
