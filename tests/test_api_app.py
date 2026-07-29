from __future__ import annotations

import json
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from market_lens.api.app import (
    app,
    classify_api_error,
    save_tool_approval_event,
    to_sse_data,
    verify_tool_approval_signature,
)
from market_lens.api.auth import get_current_user
from market_lens.api.persistence import PersistenceTracker
from market_lens.api.schemas import AnalysisHistoryResponse, AnalyzeResponse, ChatResponse
from market_lens.storage.supabase import AuthenticatedUser, SupabaseError
from market_lens.tools.models import PolicyDecision, ToolResult, ToolStatus


def test_to_sse_data_json_encodes_dates() -> None:
    data = to_sse_data({"type": "meta", "analysis": {"as_of": date(2026, 7, 3)}})

    assert data.startswith("data: ")
    assert data.endswith("\n\n")

    payload = json.loads(data.removeprefix("data: ").strip())
    assert payload == {"type": "meta", "analysis": {"as_of": "2026-07-03"}}


def test_to_sse_data_preserves_structured_progress_event() -> None:
    progress = {
        "type": "progress",
        "id": "tool:call-1",
        "stage": "tool",
        "status": "running",
        "title": "正在查询 DeepWiki",
        "detail": "mcp.deepwiki.ask_question",
        "tool_name": "mcp.deepwiki.ask_question",
    }

    payload = json.loads(to_sse_data(progress).removeprefix("data: ").strip())

    assert payload == progress


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
    assert response.result.assessment.status == "complete"
    assert response.result.assessment.method == "fundamental_valuation"
    assert response.result.assessment.dimensions.quality.score == 72.0
    assert chat_response.analysis is not None
    assert chat_response.analysis.assessment is not None
    assert stream_payload["analysis"]["assessment"]["dimensions"]["valuation"]["score"] == 42.0
    assert response.result.research is not None
    assert response.result.research["scoring_eligible"] is False
    assert chat_response.analysis.research is not None
    assert stream_payload["analysis"]["research"]["route"]["main_model"] == "technology_rd"


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
    assert history.items[0].result.research is None
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
        result["research"] = {
            "route": {
                "asset_type": "stock",
                "main_model": "technology_rd",
                "scoring_eligible": False,
            },
            "datasets": {},
            "scoring_eligible": False,
        }
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
            "status": "complete",
            "method": "fundamental_valuation",
            "fallback_reasons": [],
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


def unavailable_analysis_result_payload() -> dict:
    result = analysis_result_payload(include_assessment=True)
    result["valuation"].update(
        {
            "method": "unavailable",
            "score": None,
            "level": "unknown",
            "level_zh": "未评分",
            "confidence": 0.0,
        }
    )
    result["performance"] = {
        "sample_size": 0,
        "total_return": None,
        "annualized_return": None,
        "max_drawdown": None,
    }
    assessment = result["assessment"]
    assessment["status"] = "unavailable"
    assessment["method"] = "unavailable"
    assessment["fallback_reasons"] = ["market_data_upstream_unavailable"]
    assessment["overall_confidence"] = 0.0
    for dimension in ("valuation", "quality"):
        assessment["dimensions"][dimension].update(
            {
                "score": None,
                "level": "unknown",
                "level_zh": "未评分",
                "confidence": 0.0,
            }
        )
    assessment["data_quality"]["chat_tool_failure"] = {
        "tool_name": "finance.analyze_asset",
        "error_code": "market_data_upstream_unavailable",
        "category": "upstream_unavailable",
        "retryable": True,
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


@pytest.mark.parametrize(
    ("error_code", "error_category", "retryable", "expected_status"),
    [
        ("invalid_input", "invalid_request", False, 400),
        ("market_data_upstream_unavailable", "upstream_unavailable", True, 503),
        ("fund_nav_data_unavailable", "data_unavailable", False, 422),
        ("tool_execution_failed", "internal_error", False, 500),
    ],
)
def test_analyze_maps_tool_failures_to_stable_error_taxonomy(
    monkeypatch,
    error_code: str,
    error_category: str,
    retryable: bool,
    expected_status: int,
) -> None:
    class FakeExecutor:
        def execute(self, *args, **kwargs) -> ToolResult:
            return ToolResult(
                tool_name="finance.analyze_asset",
                status=ToolStatus.ERROR,
                policy_decision=PolicyDecision.ALLOW,
                error_code=error_code,
                error_category=error_category,
                retryable=retryable,
                message=(
                    "private internal detail"
                    if error_category == "internal_error"
                    else "Public failure detail"
                ),
            )

    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "user@example.com",
        "token",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("market_lens.api.app.get_client", lambda: object())
    monkeypatch.setattr("market_lens.api.app.get_repository", lambda: object())
    monkeypatch.setattr(
        "market_lens.api.app.build_default_executor",
        lambda **kwargs: FakeExecutor(),
    )
    try:
        response = TestClient(app).post(
            "/api/analyze",
            json={
                "asset_type": "fund",
                "code": "025856",
                "start": "2024-01-01",
                "end": "2026-07-15",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == expected_status
    payload = response.json()
    assert payload["code"] == error_code
    assert payload["category"] == error_category
    assert payload["retryable"] is retryable
    if error_category == "internal_error":
        assert payload["detail"] == "Market analysis failed unexpectedly"
        assert "private" not in payload["detail"]
    else:
        assert payload["detail"] == "Public failure detail"


def test_persistence_failure_has_distinct_error_category() -> None:
    error = classify_api_error(SupabaseError("storage unavailable"))

    assert error.code == "persistence_failed"
    assert error.category.value == "persistence_error"
    assert error.retryable is True


def test_persistence_tracker_reports_partial_failure() -> None:
    tracker = PersistenceTracker()

    saved = tracker.attempt("saved_operation", lambda: "row-id")

    def fail() -> None:
        raise SupabaseError("storage unavailable")

    failed = tracker.attempt("failed_operation", fail)
    report = tracker.report()

    assert saved == "row-id"
    assert failed is None
    assert report.status == "partial"
    assert report.error_code == "persistence_partial_failure"
    assert report.retryable is True
    assert report.failed_operations == ["failed_operation"]


def test_analyze_returns_computed_result_when_persistence_fails(monkeypatch) -> None:
    class FakeExecutor:
        def execute(self, *args, **kwargs) -> ToolResult:
            return ToolResult(
                tool_name="finance.analyze_asset",
                status=ToolStatus.SUCCESS,
                policy_decision=PolicyDecision.ALLOW,
                data={"result": analysis_result_payload(include_assessment=True)},
            )

    class FakeRepository:
        def save_analysis(self, *args, **kwargs):
            raise SupabaseError("storage unavailable")

    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "user@example.com",
        "token",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("market_lens.api.app.get_client", lambda: object())
    monkeypatch.setattr("market_lens.api.app.get_repository", FakeRepository)
    monkeypatch.setattr(
        "market_lens.api.app.build_default_executor",
        lambda **kwargs: FakeExecutor(),
    )
    try:
        response = TestClient(app).post(
            "/api/analyze",
            json={
                "asset_type": "stock",
                "code": "600519",
                "start": "2024-01-01",
                "end": "2026-07-15",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["code"] == "600519"
    assert payload["analysis_id"] is None
    assert payload["persistence"] == {
        "status": "failed",
        "error_code": "persistence_failed",
        "retryable": True,
        "failed_operations": ["analysis_result"],
    }


def test_chat_returns_answer_when_post_compute_persistence_fails(monkeypatch) -> None:
    class FakeRepository:
        def expire_stale_tool_approvals(self, *args, **kwargs):
            return []

        def ensure_chat_session(self, *args, **kwargs):
            return {"id": "33333333-3333-3333-3333-333333333333"}

        def save_analysis(self, *args, **kwargs):
            raise SupabaseError("analysis save unavailable")

        def update_chat_session(self, *args, **kwargs):
            raise SupabaseError("session update unavailable")

        def save_chat_message(self, *args, **kwargs):
            raise SupabaseError("message save unavailable")

    class FakeChatAgent:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def reply(self, **kwargs):
            return {
                "answer": "分析已经完成",
                "intent": "analyze_asset",
                "asset": {"asset_type": "stock", "code": "600519", "name": "贵州茅台"},
                "analysis": analysis_result_payload(include_assessment=True),
                "candidates": [],
                "citations": [],
            }

    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "user@example.com",
        "token",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("market_lens.api.app.get_client", lambda: object())
    monkeypatch.setattr("market_lens.api.app.get_repository", FakeRepository)
    monkeypatch.setattr("market_lens.api.app.ChatAgent", FakeChatAgent)
    monkeypatch.setattr(
        "market_lens.api.app.build_default_executor",
        lambda **kwargs: object(),
    )
    try:
        response = TestClient(app).post(
            "/api/chat",
            json={
                "message": "分析贵州茅台",
                "start": "2024-01-01",
                "end": "2026-07-15",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "分析已经完成"
    assert payload["analysis"]["code"] == "600519"
    assert payload["persistence"]["status"] == "failed"
    assert payload["persistence"]["failed_operations"] == [
        "chat_analysis",
        "chat_session_context",
        "chat_user_message",
        "chat_assistant_message",
    ]


def test_chat_persists_structured_unavailable_analysis(monkeypatch) -> None:
    captured: dict = {}

    class FakeRepository:
        def expire_stale_tool_approvals(self, *args, **kwargs):
            return []

        def ensure_chat_session(self, *args, **kwargs):
            return {"id": "33333333-3333-3333-3333-333333333333"}

        def save_analysis(self, *args, **kwargs):
            captured["analysis"] = kwargs["result"]
            return {"id": "44444444-4444-4444-4444-444444444444"}

        def update_chat_session(self, *args, **kwargs):
            return {"id": "33333333-3333-3333-3333-333333333333"}

        def save_chat_message(self, *args, **kwargs):
            return {"id": "message-id"}

    class FakeChatAgent:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def reply(self, **kwargs):
            return {
                "answer": "本次暂时无法完成估值，请稍后重试。",
                "intent": "analyze_asset",
                "asset": {"asset_type": "stock", "code": "600519", "name": "贵州茅台"},
                "analysis": unavailable_analysis_result_payload(),
                "candidates": [],
                "citations": [],
            }

    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "user@example.com",
        "token",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("market_lens.api.app.get_client", lambda: object())
    monkeypatch.setattr("market_lens.api.app.get_repository", FakeRepository)
    monkeypatch.setattr("market_lens.api.app.ChatAgent", FakeChatAgent)
    monkeypatch.setattr(
        "market_lens.api.app.build_default_executor",
        lambda **kwargs: object(),
    )
    try:
        response = TestClient(app).post(
            "/api/chat",
            json={
                "message": "分析贵州茅台",
                "start": "2024-01-01",
                "end": "2026-07-15",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["assessment"]["status"] == "unavailable"
    assert payload["persistence"]["status"] == "saved"
    assert captured["analysis"]["assessment"]["data_quality"][
        "chat_tool_failure"
    ]["retryable"] is True


def test_chat_stream_continues_after_post_compute_persistence_fails(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.message_calls = 0

        def expire_stale_tool_approvals(self, *args, **kwargs):
            return []

        def ensure_chat_session(self, *args, **kwargs):
            return {"id": "33333333-3333-3333-3333-333333333333"}

        def save_analysis(self, *args, **kwargs):
            raise SupabaseError("analysis save unavailable")

        def update_chat_session(self, *args, **kwargs):
            raise SupabaseError("session update unavailable")

        def save_chat_message(self, *args, **kwargs):
            self.message_calls += 1
            if self.message_calls > 1:
                raise SupabaseError("assistant save unavailable")
            return {"id": "message-id"}

    class FakeChatAgent:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def stream_reply(self, **kwargs):
            yield {
                "type": "meta",
                "intent": "analyze_asset",
                "asset": {"asset_type": "stock", "code": "600519", "name": "贵州茅台"},
                "analysis": analysis_result_payload(include_assessment=True),
                "candidates": [],
                "citations": [],
            }
            yield {"type": "token", "delta": "分析已经完成"}
            yield {"type": "done"}

    repository = FakeRepository()
    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "user@example.com",
        "token",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("market_lens.api.app.get_client", lambda: object())
    monkeypatch.setattr("market_lens.api.app.get_repository", lambda: repository)
    monkeypatch.setattr("market_lens.api.app.ChatAgent", FakeChatAgent)
    monkeypatch.setattr(
        "market_lens.api.app.build_default_executor",
        lambda **kwargs: object(),
    )
    try:
        response = TestClient(app).post(
            "/api/chat/stream",
            json={
                "message": "分析贵州茅台",
                "start": "2024-01-01",
                "end": "2026-07-15",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == ["meta", "token", "done"]
    assert events[0]["analysis"]["code"] == "600519"
    assert events[1]["delta"] == "分析已经完成"
    assert events[2]["persistence"]["status"] == "failed"
    assert events[2]["persistence"]["failed_operations"] == [
        "chat_analysis",
        "chat_session_context",
        "chat_assistant_message",
    ]


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
