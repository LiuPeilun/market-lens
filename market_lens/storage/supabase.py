from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from market_lens.config import settings


class SupabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None
    access_token: str


class SupabaseRESTClient:
    def __init__(
        self,
        url: str | None = None,
        publishable_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = (url or settings.supabase_url or "").rstrip("/")
        self.publishable_key = publishable_key or settings.supabase_publishable_key or ""
        if not self.url or not self.publishable_key:
            raise SupabaseError("Supabase is not configured on the API server")
        self.transport = transport

    def get_user(self, access_token: str) -> AuthenticatedUser:
        payload = self._request("GET", "/auth/v1/user", access_token=access_token)
        try:
            user_id = UUID(str(payload["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SupabaseError("Supabase returned an invalid user payload") from exc
        return AuthenticatedUser(
            id=user_id,
            email=payload.get("email"),
            access_token=access_token,
        )

    def select(
        self,
        table: str,
        access_token: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/rest/v1/{table}",
            access_token=access_token,
            params=params,
        )
        return payload if isinstance(payload, list) else []

    def insert(
        self,
        table: str,
        access_token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        rows = self._request(
            "POST",
            f"/rest/v1/{table}",
            access_token=access_token,
            json=payload,
            prefer="return=representation",
        )
        if not isinstance(rows, list) or not rows:
            raise SupabaseError(f"Supabase did not return the inserted {table} row")
        return rows[0]

    def update(
        self,
        table: str,
        access_token: str,
        row_id: UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        rows = self._request(
            "PATCH",
            f"/rest/v1/{table}",
            access_token=access_token,
            params={"id": f"eq.{row_id}"},
            json=payload,
            prefer="return=representation",
        )
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def update_where(
        self,
        table: str,
        access_token: str,
        params: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        rows = self._request(
            "PATCH",
            f"/rest/v1/{table}",
            access_token=access_token,
            params=params,
            json=payload,
            prefer="return=representation",
        )
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def upsert(
        self,
        table: str,
        access_token: str,
        payload: dict[str, Any],
        *,
        on_conflict: str,
    ) -> dict[str, Any]:
        rows = self._request(
            "POST",
            f"/rest/v1/{table}",
            access_token=access_token,
            params={"on_conflict": on_conflict},
            json=payload,
            prefer="resolution=merge-duplicates,return=representation",
        )
        if not isinstance(rows, list) or not rows:
            raise SupabaseError(f"Supabase did not return the upserted {table} row")
        return rows[0]

    def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {
            "apikey": self.publishable_key,
            "Authorization": f"Bearer {access_token}",
        }
        if prefer:
            headers["Prefer"] = prefer
        try:
            with httpx.Client(
                timeout=settings.http_timeout,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = client.request(
                    method,
                    f"{self.url}{path}",
                    headers=headers,
                    params=params,
                    json=json,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = supabase_error_message(exc.response)
            raise SupabaseError(message) from exc
        except httpx.HTTPError as exc:
            raise SupabaseError(f"Supabase request failed: {exc}") from exc
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SupabaseError("Supabase returned an invalid JSON response") from exc


class SupabaseRepository:
    def __init__(self, client: SupabaseRESTClient | None = None) -> None:
        self.client = client or SupabaseRESTClient()

    def save_analysis(
        self,
        user: AuthenticatedUser,
        request_params: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.client.insert(
            "analysis_runs",
            user.access_token,
            {
                "user_id": str(user.id),
                "asset_type": result.get("asset_type"),
                "asset_code": result.get("code"),
                "asset_name": result.get("name"),
                "request_params": request_params,
                "result": result,
            },
        )

    def create_chat_session(
        self,
        user: AuthenticatedUser,
        title: str,
    ) -> dict[str, Any]:
        return self.client.insert(
            "chat_sessions",
            user.access_token,
            {
                "id": str(uuid4()),
                "user_id": str(user.id),
                "title": title[:120] or "新对话",
            },
        )

    def get_chat_session(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
    ) -> dict[str, Any] | None:
        rows = self.client.select(
            "chat_sessions",
            user.access_token,
            {
                "select": "*",
                "id": f"eq.{session_id}",
                "user_id": f"eq.{user.id}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def ensure_chat_session(
        self,
        user: AuthenticatedUser,
        session_id: UUID | None,
        title: str,
    ) -> dict[str, Any]:
        if session_id is not None:
            session = self.get_chat_session(user, session_id)
            if session is None:
                raise SupabaseError("Chat session was not found or is not accessible")
            return session
        return self.create_chat_session(user, title)

    def update_chat_session(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
        asset: dict[str, Any] | None,
    ) -> None:
        if not asset:
            return
        self.client.update(
            "chat_sessions",
            user.access_token,
            session_id,
            {
                "asset_type": asset.get("asset_type"),
                "asset_code": asset.get("code"),
                "asset_name": asset.get("name"),
            },
        )

    def save_chat_message(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
        role: str,
        content: str,
        citations: list[str] | None = None,
        analysis_run_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.insert(
            "chat_messages",
            user.access_token,
            {
                "user_id": str(user.id),
                "session_id": str(session_id),
                "role": role,
                "content": content,
                "citations": citations or [],
                "analysis_run_id": analysis_run_id,
            },
        )

    def save_tool_invocation(
        self,
        user: AuthenticatedUser,
        session_id: UUID | None,
        tool_name: str,
        capability: str,
        risk_level: str,
        execution_target: str,
        policy_decision: str,
        status: str,
        duration_ms: int,
        input_summary: dict[str, Any],
        error_code: str | None,
    ) -> dict[str, Any]:
        return self.client.insert(
            "tool_invocations",
            user.access_token,
            {
                "user_id": str(user.id),
                "session_id": str(session_id) if session_id else None,
                "tool_name": tool_name,
                "capability": capability,
                "risk_level": risk_level,
                "execution_target": execution_target,
                "policy_decision": policy_decision,
                "status": status,
                "duration_ms": duration_ms,
                "input_summary": input_summary,
                "error_code": error_code,
            },
        )

    def create_tool_approval(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
        *,
        approval_id: UUID,
        tool_name: str,
        tool_alias: str,
        tool_call_id: str,
        risk_level: str,
        execution_target: str,
        reason: str,
        input_summary: dict[str, Any],
        arguments_digest: str,
        checkpoint: dict[str, Any],
        citations: list[str],
        signature: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        return self.client.insert(
            "tool_approvals",
            user.access_token,
            {
                "id": str(approval_id),
                "user_id": str(user.id),
                "session_id": str(session_id),
                "tool_name": tool_name,
                "tool_alias": tool_alias,
                "tool_call_id": tool_call_id,
                "risk_level": risk_level,
                "execution_target": execution_target,
                "status": "pending",
                "reason": reason,
                "input_summary": input_summary,
                "arguments_digest": arguments_digest,
                "checkpoint": checkpoint,
                "citations": citations,
                "signature": signature,
                "expires_at": expires_at.isoformat(),
            },
        )

    def get_tool_approval(
        self,
        user: AuthenticatedUser,
        approval_id: UUID,
    ) -> dict[str, Any] | None:
        rows = self.client.select(
            "tool_approvals",
            user.access_token,
            {
                "select": "*",
                "id": f"eq.{approval_id}",
                "user_id": f"eq.{user.id}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def transition_tool_approval(
        self,
        user: AuthenticatedUser,
        approval_id: UUID,
        *,
        expected_status: str,
        status: str,
        expected_signature: str,
        resolved_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"status": status}
        if resolved_at is not None:
            payload["resolved_at"] = resolved_at.isoformat()
        return self.client.update_where(
            "tool_approvals",
            user.access_token,
            {
                "id": f"eq.{approval_id}",
                "user_id": f"eq.{user.id}",
                "status": f"eq.{expected_status}",
                "signature": f"eq.{expected_signature}",
            },
            payload,
        )

    def expire_stale_tool_approvals(
        self,
        user: AuthenticatedUser,
        now: datetime,
    ) -> bool:
        timestamp = now.isoformat()
        pending = self.client.update_where(
            "tool_approvals",
            user.access_token,
            {
                "user_id": f"eq.{user.id}",
                "status": "eq.pending",
                "expires_at": f"lte.{timestamp}",
                "select": "id,status",
            },
            {"status": "expired", "resolved_at": timestamp},
        )
        return pending is not None

    def list_workspace_files(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
    ) -> list[dict[str, Any]]:
        return self.client.select(
            "workspace_files",
            user.access_token,
            {
                "select": "id,path,size_bytes,content_type,created_at,updated_at",
                "user_id": f"eq.{user.id}",
                "session_id": f"eq.{session_id}",
                "order": "path.asc",
            },
        )

    def get_workspace_file(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
        path: str,
    ) -> dict[str, Any] | None:
        rows = self.client.select(
            "workspace_files",
            user.access_token,
            {
                "select": "id,path,content,size_bytes,content_type,created_at,updated_at",
                "user_id": f"eq.{user.id}",
                "session_id": f"eq.{session_id}",
                "path": f"eq.{path}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def write_workspace_file(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        return self.client.upsert(
            "workspace_files",
            user.access_token,
            {
                "user_id": str(user.id),
                "session_id": str(session_id),
                "path": path,
                "content": content,
                "content_type": "text/plain",
            },
            on_conflict="session_id,path",
        )

    def list_analyses(self, user: AuthenticatedUser, limit: int) -> list[dict[str, Any]]:
        return self.client.select(
            "analysis_runs",
            user.access_token,
            {
                "select": (
                    "id,asset_type,asset_code,asset_name,request_params,result,created_at"
                ),
                "user_id": f"eq.{user.id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    def list_chat_sessions(
        self,
        user: AuthenticatedUser,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.client.select(
            "chat_sessions",
            user.access_token,
            {
                "select": "id,title,asset_type,asset_code,asset_name,created_at,updated_at",
                "user_id": f"eq.{user.id}",
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )

    def list_chat_messages(
        self,
        user: AuthenticatedUser,
        session_id: UUID,
    ) -> list[dict[str, Any]]:
        if self.get_chat_session(user, session_id) is None:
            raise SupabaseError("Chat session was not found or is not accessible")
        return self.client.select(
            "chat_messages",
            user.access_token,
            {
                "select": "id,role,content,citations,analysis_run_id,created_at",
                "session_id": f"eq.{session_id}",
                "order": "created_at.asc",
            },
        )


def supabase_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    detail = payload.get("message") or payload.get("msg") or payload.get("error_description")
    if response.status_code in {401, 403}:
        return "Supabase authentication failed or access was denied"
    if detail:
        return f"Supabase request failed: {detail}"
    return f"Supabase request failed with status {response.status_code}"
