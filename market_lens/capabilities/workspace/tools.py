from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import Field, field_validator

from market_lens.sandbox.models import validate_relative_path
from market_lens.tools.executor import ToolPublicError
from market_lens.tools.models import (
    ExecutionTarget,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolRisk,
    ToolSpec,
)
from market_lens.tools.registry import ToolRegistry

LIST_FILES_TOOL = "workspace.list_files"
READ_FILE_TOOL = "workspace.read_file"
WRITE_FILE_TOOL = "workspace.write_file"


class WorkspaceStore(Protocol):
    def list_files(self) -> list[dict[str, Any]]: ...

    def read_file(self, path: str) -> dict[str, Any] | None: ...

    def write_file(self, path: str, content: str) -> dict[str, Any]: ...


class EmptyInput(ToolInput):
    pass


class FilePathInput(ToolInput):
    path: str = Field(min_length=1, max_length=240)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if not re.fullmatch(r"[\w./ -]+", normalized):
            raise ValueError("workspace paths contain unsupported characters")
        return normalized


class WriteFileInput(FilePathInput):
    content: str = Field(max_length=200_000)


class WorkspaceFileInfo(ToolOutput):
    path: str
    size_bytes: int
    content_type: str
    updated_at: str


class ListFilesOutput(ToolOutput):
    files: list[WorkspaceFileInfo]


class ReadFileOutput(WorkspaceFileInfo):
    content: str


class WriteFileOutput(WorkspaceFileInfo):
    created: bool


def register_workspace_tools(registry: ToolRegistry, store: WorkspaceStore) -> None:
    def list_files(raw_input, context: ToolContext) -> ListFilesOutput:
        del context
        EmptyInput.model_validate(raw_input)
        return ListFilesOutput(files=[_file_info(row) for row in store.list_files()])

    def read_file(raw_input, context: ToolContext) -> ReadFileOutput:
        del context
        request = FilePathInput.model_validate(raw_input)
        row = store.read_file(request.path)
        if row is None:
            raise ToolPublicError("workspace_file_not_found", "Workspace file was not found")
        return ReadFileOutput(content=str(row["content"]), **_file_info(row).model_dump())

    def write_file(raw_input, context: ToolContext) -> WriteFileOutput:
        del context
        request = WriteFileInput.model_validate(raw_input)
        existing = store.read_file(request.path)
        row = store.write_file(request.path, request.content)
        return WriteFileOutput(created=existing is None, **_file_info(row).model_dump())

    registry.register(
        ToolSpec(
            name=LIST_FILES_TOOL,
            capability="filesystem",
            description="List text files in the current chat session's private virtual workspace",
            input_model=EmptyInput,
            output_model=ListFilesOutput,
            handler=list_files,
            risk=ToolRisk.READ,
            execution_target=ExecutionTarget.TRUSTED_LOCAL,
            requires_network=True,
        )
    )
    registry.register(
        ToolSpec(
            name=READ_FILE_TOOL,
            capability="filesystem",
            description=(
                "Read one text file from the current chat session's private virtual workspace"
            ),
            input_model=FilePathInput,
            output_model=ReadFileOutput,
            handler=read_file,
            risk=ToolRisk.READ,
            execution_target=ExecutionTarget.TRUSTED_LOCAL,
            requires_network=True,
        )
    )
    registry.register(
        ToolSpec(
            name=WRITE_FILE_TOOL,
            capability="filesystem",
            description=(
                "Create or replace one text file in the current chat session's private "
                "virtual workspace"
            ),
            input_model=WriteFileInput,
            output_model=WriteFileOutput,
            handler=write_file,
            risk=ToolRisk.WRITE,
            execution_target=ExecutionTarget.TRUSTED_LOCAL,
            idempotent=True,
            requires_network=True,
        )
    )


def _file_info(row: dict[str, Any]) -> WorkspaceFileInfo:
    return WorkspaceFileInfo(
        path=str(row["path"]),
        size_bytes=int(row.get("size_bytes") or len(str(row.get("content") or "").encode("utf-8"))),
        content_type=str(row.get("content_type") or "text/plain"),
        updated_at=str(row["updated_at"]),
    )
