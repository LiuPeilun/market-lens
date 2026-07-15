from __future__ import annotations

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

    def insert(self, table: str, access_token: str, payload: dict[str, object]):
        self.inserts.append((table, access_token, payload))
        return {"id": "22222222-2222-2222-2222-222222222222", **payload}


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
