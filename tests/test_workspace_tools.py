from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from market_lens.capabilities.workspace.tools import (
    LIST_FILES_TOOL,
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    register_workspace_tools,
)
from market_lens.tools.executor import ToolExecutor, tool_arguments_digest
from market_lens.tools.models import ToolApprovalGrant, ToolStatus
from market_lens.tools.registry import ToolRegistry


class FakeWorkspaceStore:
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}

    def list_files(self) -> list[dict]:
        return list(self.files.values())

    def read_file(self, path: str) -> dict | None:
        return self.files.get(path)

    def write_file(self, path: str, content: str) -> dict:
        row = {
            "path": path,
            "content": content,
            "size_bytes": len(content.encode("utf-8")),
            "content_type": "text/plain",
            "updated_at": datetime(2026, 7, 20, tzinfo=UTC).isoformat(),
        }
        self.files[path] = row
        return row


def build_executor(store: FakeWorkspaceStore) -> ToolExecutor:
    registry = ToolRegistry()
    register_workspace_tools(registry, store)
    return ToolExecutor(registry)


def test_workspace_write_requires_approval_then_read_is_allowed() -> None:
    store = FakeWorkspaceStore()
    executor = build_executor(store)
    arguments = {"path": "notes/result.txt", "content": "valuation result"}

    pending = executor.execute(WRITE_FILE_TOOL, arguments)
    approved = executor.execute(
        WRITE_FILE_TOOL,
        arguments,
        approval=ToolApprovalGrant(
            approval_id=UUID("22222222-2222-2222-2222-222222222222"),
            tool_name=WRITE_FILE_TOOL,
            arguments_digest=tool_arguments_digest(arguments),
        ),
    )
    listed = executor.execute(LIST_FILES_TOOL, {})
    read = executor.execute(READ_FILE_TOOL, {"path": "notes/result.txt"})

    assert pending.status is ToolStatus.CONFIRMATION_REQUIRED
    assert approved.status is ToolStatus.SUCCESS
    assert listed.data["files"][0]["path"] == "notes/result.txt"
    assert read.data["content"] == "valuation result"


def test_workspace_rejects_path_traversal_before_store_access() -> None:
    store = FakeWorkspaceStore()
    result = build_executor(store).execute(READ_FILE_TOOL, {"path": "../secret.txt"})

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "invalid_input"
    assert store.files == {}
