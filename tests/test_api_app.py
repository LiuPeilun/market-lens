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
from market_lens.storage.supabase import AuthenticatedUser


def test_to_sse_data_json_encodes_dates() -> None:
    data = to_sse_data({"type": "meta", "analysis": {"as_of": date(2026, 7, 3)}})

    assert data.startswith("data: ")
    assert data.endswith("\n\n")

    payload = json.loads(data.removeprefix("data: ").strip())
    assert payload == {"type": "meta", "analysis": {"as_of": "2026-07-03"}}


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
