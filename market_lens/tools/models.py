from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRisk(StrEnum):
    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"


class ExecutionTarget(StrEnum):
    TRUSTED_LOCAL = "trusted_local"
    SANDBOX_REQUIRED = "sandbox_required"
    REMOTE_MCP = "remote_mcp"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DENY = "deny"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    ERROR = "error"


class ToolContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID | None = None
    session_id: UUID | None = None
    request_id: str | None = None


class ToolApprovalGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    tool_name: str
    arguments_digest: str


class PolicyEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: PolicyDecision
    reason: str


class ToolResult(BaseModel):
    tool_name: str
    status: ToolStatus
    policy_decision: PolicyDecision
    data: dict[str, Any] | None = None
    citations: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None
    duration_ms: int = 0


ToolHandler = Callable[[BaseModel, ToolContext], BaseModel | dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: str
    description: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.READ
    execution_target: ExecutionTarget = ExecutionTarget.TRUSTED_LOCAL
    timeout_seconds: float = 30.0
    idempotent: bool = True
    requires_network: bool = False
    input_schema_override: dict[str, Any] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capability": self.capability,
            "description": self.description,
            "input_schema": self.input_schema_override or self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "risk": self.risk.value,
            "execution_target": self.execution_target.value,
            "timeout_seconds": self.timeout_seconds,
            "idempotent": self.idempotent,
            "requires_network": self.requires_network,
        }
