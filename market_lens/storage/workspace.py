from __future__ import annotations

from typing import Any
from uuid import UUID

from market_lens.storage.supabase import AuthenticatedUser, SupabaseRepository


class SupabaseWorkspaceStore:
    def __init__(
        self,
        repository: SupabaseRepository,
        user: AuthenticatedUser,
        session_id: UUID,
    ) -> None:
        self.repository = repository
        self.user = user
        self.session_id = session_id

    def list_files(self) -> list[dict[str, Any]]:
        return self.repository.list_workspace_files(self.user, self.session_id)

    def read_file(self, path: str) -> dict[str, Any] | None:
        return self.repository.get_workspace_file(self.user, self.session_id, path)

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        return self.repository.write_workspace_file(
            self.user,
            self.session_id,
            path,
            content,
        )
