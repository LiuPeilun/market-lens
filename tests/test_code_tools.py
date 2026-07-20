from __future__ import annotations

from uuid import UUID

from market_lens.capabilities.code.tools import RUN_PYTHON_TOOL, register_code_tools
from market_lens.sandbox.models import SandboxRequest, SandboxResult, SandboxStatus
from market_lens.sandbox.runner import SandboxRunner
from market_lens.tools.executor import ToolExecutor, tool_arguments_digest
from market_lens.tools.models import ToolApprovalGrant, ToolStatus
from market_lens.tools.policy import ToolPolicy
from market_lens.tools.registry import ToolRegistry


class FakeSandboxRunner(SandboxRunner):
    def __init__(self) -> None:
        self.requests: list[SandboxRequest] = []

    @property
    def backend_name(self) -> str:
        return "fake"

    def is_available(self) -> bool:
        return True

    def run(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return SandboxResult(
            backend="fake",
            status=SandboxStatus.SUCCESS,
            exit_code=0,
            stdout="42\n",
            duration_ms=12,
        )


def test_python_tool_requires_exact_approval_and_uses_fixed_sandbox_limits() -> None:
    runner = FakeSandboxRunner()
    registry = ToolRegistry()
    register_code_tools(registry, runner)
    executor = ToolExecutor(registry, ToolPolicy(sandbox_available=True))
    arguments = {"code": "print(6 * 7)", "timeout_seconds": 5}

    pending = executor.execute(RUN_PYTHON_TOOL, arguments)
    approved = executor.execute(
        RUN_PYTHON_TOOL,
        arguments,
        approval=ToolApprovalGrant(
            approval_id=UUID("22222222-2222-2222-2222-222222222222"),
            tool_name=RUN_PYTHON_TOOL,
            arguments_digest=tool_arguments_digest(arguments),
        ),
    )

    assert pending.status is ToolStatus.CONFIRMATION_REQUIRED
    assert len(runner.requests) == 1
    assert approved.status is ToolStatus.SUCCESS
    assert approved.data["stdout"] == "42\n"
    request = runner.requests[0]
    assert request.network_allowlist == []
    assert request.limits.timeout_seconds == 5
    assert request.limits.memory_mb == 256
    assert request.limits.max_output_bytes == 20_000
