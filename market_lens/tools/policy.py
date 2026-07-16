from __future__ import annotations

from market_lens.tools.models import (
    ExecutionTarget,
    PolicyDecision,
    PolicyEvaluation,
    ToolContext,
    ToolRisk,
    ToolSpec,
)


class ToolPolicy:
    def __init__(
        self,
        sandbox_available: bool = False,
        remote_mcp_available: bool = False,
    ) -> None:
        self.sandbox_available = sandbox_available
        self.remote_mcp_available = remote_mcp_available

    def evaluate(self, spec: ToolSpec, context: ToolContext) -> PolicyEvaluation:
        del context
        if spec.risk is ToolRisk.DESTRUCTIVE:
            return PolicyEvaluation(
                decision=PolicyDecision.DENY,
                reason="Destructive tools are disabled by default",
            )
        if (
            spec.execution_target is ExecutionTarget.SANDBOX_REQUIRED
            and not self.sandbox_available
        ):
            return PolicyEvaluation(
                decision=PolicyDecision.DENY,
                reason="A sandbox execution backend is required but unavailable",
            )
        if (
            spec.execution_target is ExecutionTarget.REMOTE_MCP
            and not self.remote_mcp_available
        ):
            return PolicyEvaluation(
                decision=PolicyDecision.DENY,
                reason="A remote MCP connection is required but unavailable",
            )
        if spec.risk in {ToolRisk.WRITE, ToolRisk.EXTERNAL_SIDE_EFFECT}:
            return PolicyEvaluation(
                decision=PolicyDecision.CONFIRMATION_REQUIRED,
                reason="This tool can change external state and requires user confirmation",
            )
        return PolicyEvaluation(
            decision=PolicyDecision.ALLOW,
            reason="Tool is allowed by the default policy",
        )
