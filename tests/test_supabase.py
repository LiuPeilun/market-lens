from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx

from market_lens.storage.supabase import (
    AuthenticatedUser,
    SupabaseError,
    SupabaseRepository,
    SupabaseRESTClient,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_supabase_client_gets_authenticated_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        assert request.headers["apikey"] == "publishable"
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(200, json={"id": str(USER_ID), "email": "user@example.com"})

    client = SupabaseRESTClient(
        "https://example.supabase.co",
        "publishable",
        transport=httpx.MockTransport(handler),
    )

    user = client.get_user("access-token")

    assert user.id == USER_ID
    assert user.email == "user@example.com"


def test_supabase_client_hides_auth_error_details() -> None:
    client = SupabaseRESTClient(
        "https://example.supabase.co",
        "publishable",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"message": "token details"})
        ),
    )

    try:
        client.get_user("expired-token")
    except SupabaseError as exc:
        assert str(exc) == "Supabase authentication failed or access was denied"
    else:
        raise AssertionError("Expected SupabaseError")


class FakeRESTClient:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, str, dict[str, object]]] = []
        self.updates: list[tuple[str, str, dict[str, str], dict[str, object]]] = []
        self.upserts: list[tuple[str, str, dict[str, object], str]] = []

    def insert(self, table: str, access_token: str, payload: dict[str, object]):
        self.inserts.append((table, access_token, payload))
        return {"id": "22222222-2222-2222-2222-222222222222", **payload}

    def update_where(
        self,
        table: str,
        access_token: str,
        params: dict[str, str],
        payload: dict[str, object],
    ):
        self.updates.append((table, access_token, params, payload))
        return {"id": "22222222-2222-2222-2222-222222222222", **payload}

    def upsert(
        self,
        table: str,
        access_token: str,
        payload: dict[str, object],
        *,
        on_conflict: str,
    ):
        self.upserts.append((table, access_token, payload, on_conflict))
        return {
            "id": "44444444-4444-4444-4444-444444444444",
            "size_bytes": len(str(payload["content"]).encode("utf-8")),
            "updated_at": "2026-07-20T00:00:00+00:00",
            **payload,
        }


def test_repository_saves_analysis_for_authenticated_user() -> None:
    client = FakeRESTClient()
    repository = SupabaseRepository(client=client)  # type: ignore[arg-type]
    user = AuthenticatedUser(USER_ID, "user@example.com", "access-token")

    row = repository.save_analysis(
        user,
        request_params={"start": "2024-01-01"},
        result={"asset_type": "stock", "code": "600519", "name": "贵州茅台"},
    )

    assert row["id"] == "22222222-2222-2222-2222-222222222222"
    table, token, payload = client.inserts[0]
    assert table == "analysis_runs"
    assert token == "access-token"
    assert payload["user_id"] == str(USER_ID)
    assert payload["asset_code"] == "600519"


def test_repository_saves_tool_invocation_audit() -> None:
    client = FakeRESTClient()
    repository = SupabaseRepository(client=client)  # type: ignore[arg-type]
    user = AuthenticatedUser(USER_ID, "user@example.com", "access-token")

    repository.save_tool_invocation(
        user=user,
        session_id=None,
        tool_name="finance.analyze_asset",
        capability="finance",
        risk_level="compute",
        execution_target="trusted_local",
        policy_decision="allow",
        status="success",
        duration_ms=123,
        input_summary={"code": "600519"},
        error_code=None,
    )

    table, token, payload = client.inserts[0]
    assert table == "tool_invocations"
    assert token == "access-token"
    assert payload["user_id"] == str(USER_ID)
    assert payload["tool_name"] == "finance.analyze_asset"
    assert payload["input_summary"] == {"code": "600519"}


def test_repository_creates_and_atomically_transitions_tool_approval() -> None:
    client = FakeRESTClient()
    repository = SupabaseRepository(client=client)  # type: ignore[arg-type]
    user = AuthenticatedUser(USER_ID, "user@example.com", "access-token")
    session_id = UUID("33333333-3333-3333-3333-333333333333")
    approval_id = UUID("22222222-2222-2222-2222-222222222222")

    row = repository.create_tool_approval(
        user,
        session_id,
        approval_id=approval_id,
        tool_name="code.run_python",
        tool_alias="code__run_python",
        tool_call_id="call-1",
        risk_level="write",
        execution_target="sandbox_required",
        reason="approval required",
        input_summary={"code": "print(1)"},
        arguments_digest="a" * 64,
        checkpoint={"version": 1},
        citations=[],
        signature="b" * 64,
        expires_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )
    transitioned = repository.transition_tool_approval(
        user,
        approval_id,
        expected_status="pending",
        status="approved",
        expected_signature="b" * 64,
        resolved_at=datetime(2026, 7, 20, 11, 55, tzinfo=UTC),
    )

    assert row["tool_name"] == "code.run_python"
    assert transitioned["status"] == "approved"
    table, _, params, payload = client.updates[0]
    assert table == "tool_approvals"
    assert params["status"] == "eq.pending"
    assert params["signature"] == f"eq.{'b' * 64}"
    assert payload["status"] == "approved"


def test_repository_expires_stale_pending_tool_approvals() -> None:
    client = FakeRESTClient()
    repository = SupabaseRepository(client=client)  # type: ignore[arg-type]
    user = AuthenticatedUser(USER_ID, "user@example.com", "access-token")
    now = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)

    result = repository.expire_stale_tool_approvals(user, now)

    assert result is True
    assert len(client.updates) == 1
    pending = client.updates[0]
    assert pending[0] == "tool_approvals"
    assert pending[2]["status"] == "eq.pending"
    assert pending[2]["expires_at"] == f"lte.{now.isoformat()}"
    assert pending[3] == {"status": "expired", "resolved_at": now.isoformat()}


def test_repository_writes_workspace_file_with_session_path_conflict_key() -> None:
    client = FakeRESTClient()
    repository = SupabaseRepository(client=client)  # type: ignore[arg-type]
    user = AuthenticatedUser(USER_ID, "user@example.com", "access-token")
    session_id = UUID("33333333-3333-3333-3333-333333333333")

    row = repository.write_workspace_file(
        user,
        session_id,
        "notes/result.txt",
        "valuation result",
    )

    table, token, payload, conflict = client.upserts[0]
    assert table == "workspace_files"
    assert token == "access-token"
    assert conflict == "session_id,path"
    assert payload["user_id"] == str(USER_ID)
    assert row["path"] == "notes/result.txt"
