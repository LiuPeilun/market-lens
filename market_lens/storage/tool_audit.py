from __future__ import annotations

from typing import Any

from market_lens.storage.supabase import AuthenticatedUser, SupabaseRepository
from market_lens.tools.executor import ToolAuditRecorder
from market_lens.tools.models import ToolContext, ToolResult, ToolSpec


class SupabaseToolAuditRecorder(ToolAuditRecorder):
    def __init__(self, repository: SupabaseRepository, user: AuthenticatedUser) -> None:
        self.repository = repository
        self.user = user

    def record(
        self,
        spec: ToolSpec,
        context: ToolContext,
        result: ToolResult,
        input_summary: dict[str, Any],
    ) -> None:
        self.repository.save_tool_invocation(
            user=self.user,
            session_id=context.session_id,
            tool_name=spec.name,
            capability=spec.capability,
            risk_level=spec.risk.value,
            execution_target=spec.execution_target.value,
            policy_decision=result.policy_decision.value,
            status=result.status.value,
            duration_ms=result.duration_ms,
            input_summary=input_summary,
            error_code=result.error_code,
        )
