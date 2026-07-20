from __future__ import annotations

from pydantic import Field

from market_lens.sandbox.models import SandboxLimits
from market_lens.sandbox.runner import SandboxRunner
from market_lens.tools.models import (
    ExecutionTarget,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolRisk,
    ToolSpec,
)
from market_lens.tools.registry import ToolRegistry

RUN_PYTHON_TOOL = "code.run_python"


class RunPythonInput(ToolInput):
    code: str = Field(min_length=1, max_length=20_000)
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class RunPythonOutput(ToolOutput):
    backend: str
    sandbox_status: str
    exit_code: int | None = None
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool
    error_code: str | None = None
    message: str | None = None
    duration_ms: int


def register_code_tools(registry: ToolRegistry, runner: SandboxRunner) -> None:
    def run_python(raw_input, context: ToolContext) -> RunPythonOutput:
        del context
        request = RunPythonInput.model_validate(raw_input)
        result = runner.run_python(
            request.code,
            limits=SandboxLimits(
                timeout_seconds=request.timeout_seconds,
                memory_mb=256,
                cpu_count=0.5,
                pids_limit=64,
                max_output_bytes=20_000,
                max_artifact_bytes=1_000_000,
            ),
        )
        return RunPythonOutput(
            backend=result.backend,
            sandbox_status=result.status.value,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            output_truncated=result.output_truncated,
            error_code=result.error_code,
            message=result.message,
            duration_ms=result.duration_ms,
        )

    registry.register(
        ToolSpec(
            name=RUN_PYTHON_TOOL,
            capability="code_execution",
            description=(
                "Execute Python source code in an ephemeral isolated sandbox with no network "
                "access and fixed resource limits"
            ),
            input_model=RunPythonInput,
            output_model=RunPythonOutput,
            handler=run_python,
            risk=ToolRisk.WRITE,
            execution_target=ExecutionTarget.SANDBOX_REQUIRED,
            timeout_seconds=45,
            idempotent=False,
            requires_network=False,
        )
    )
