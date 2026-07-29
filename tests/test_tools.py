from __future__ import annotations

from datetime import date
from time import sleep
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from market_lens.capabilities.finance.schemas import AnalyzeAssetInput, SearchAssetsInput
from market_lens.capabilities.finance.tools import (
    ANALYZE_ASSET_TIMEOUT_SECONDS,
    ANALYZE_ASSET_TOOL,
    FinanceToolHandlers,
    register_finance_tools,
)
from market_lens.data.eastmoney import EastmoneyError
from market_lens.errors import DataUnavailableError
from market_lens.tools.executor import (
    ToolExecutor,
    ToolInvocationError,
    ToolPublicError,
    require_tool_data,
    tool_arguments_digest,
)
from market_lens.tools.models import (
    ExecutionTarget,
    PolicyDecision,
    ToolApprovalGrant,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolRisk,
    ToolSpec,
    ToolStatus,
)
from market_lens.tools.policy import ToolPolicy
from market_lens.tools.registry import ToolRegistry, ToolRegistryError


class EchoInput(ToolInput):
    value: str


class EchoOutput(ToolOutput):
    echoed: str


def echo_handler(raw_input: BaseModel, context: ToolContext) -> EchoOutput:
    del context
    args = EchoInput.model_validate(raw_input)
    return EchoOutput(echoed=args.value)


def make_spec(
    name: str = "test.echo",
    risk: ToolRisk = ToolRisk.READ,
    execution_target: ExecutionTarget = ExecutionTarget.TRUSTED_LOCAL,
    handler=echo_handler,
    timeout_seconds: float = 1,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        capability="test",
        description="Echo a value.",
        input_model=EchoInput,
        output_model=EchoOutput,
        handler=handler,
        risk=risk,
        execution_target=execution_target,
        timeout_seconds=timeout_seconds,
    )


def test_registry_rejects_duplicate_and_invalid_names() -> None:
    registry = ToolRegistry([make_spec()])

    with pytest.raises(ToolRegistryError, match="already registered"):
        registry.register(make_spec())
    with pytest.raises(ToolRegistryError, match="namespaced"):
        registry.register(make_spec(name="invalid"))


def test_registry_exports_protocol_neutral_json_schemas() -> None:
    registry = ToolRegistry([make_spec()])

    schema = registry.schemas()[0]

    assert schema["name"] == "test.echo"
    assert schema["risk"] == "read"
    assert schema["input_schema"]["properties"]["value"]["type"] == "string"


def test_finance_analysis_outer_timeout_covers_bounded_fallback_path() -> None:
    registry = ToolRegistry()
    register_finance_tools(
        registry,
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    assert ANALYZE_ASSET_TIMEOUT_SECONDS == 300
    assert (
        registry.get(ANALYZE_ASSET_TOOL).timeout_seconds
        == ANALYZE_ASSET_TIMEOUT_SECONDS
    )


@pytest.mark.parametrize("risk", [ToolRisk.READ, ToolRisk.COMPUTE])
def test_default_policy_allows_read_and_compute_tools(risk: ToolRisk) -> None:
    evaluation = ToolPolicy().evaluate(make_spec(risk=risk), ToolContext())

    assert evaluation.decision is PolicyDecision.ALLOW


@pytest.mark.parametrize("risk", [ToolRisk.WRITE, ToolRisk.EXTERNAL_SIDE_EFFECT])
def test_default_policy_requires_confirmation_for_external_changes(risk: ToolRisk) -> None:
    result = ToolExecutor(ToolRegistry([make_spec(risk=risk)])).execute(
        "test.echo",
        {"value": "hello"},
    )

    assert result.status is ToolStatus.CONFIRMATION_REQUIRED
    assert result.error_code == "confirmation_required"


def test_executor_approval_is_bound_to_tool_and_exact_arguments() -> None:
    executor = ToolExecutor(ToolRegistry([make_spec(risk=ToolRisk.WRITE)]))
    grant = ToolApprovalGrant(
        approval_id=UUID("22222222-2222-2222-2222-222222222222"),
        tool_name="test.echo",
        arguments_digest=tool_arguments_digest({"value": "approved"}),
    )

    approved = executor.execute(
        "test.echo",
        {"value": "approved"},
        approval=grant,
    )
    changed = executor.execute(
        "test.echo",
        {"value": "changed"},
        approval=grant,
    )

    assert approved.status is ToolStatus.SUCCESS
    assert changed.status is ToolStatus.CONFIRMATION_REQUIRED


def test_default_policy_denies_destructive_tools() -> None:
    result = ToolExecutor(
        ToolRegistry([make_spec(risk=ToolRisk.DESTRUCTIVE)])
    ).execute("test.echo", {"value": "hello"})

    assert result.status is ToolStatus.DENIED
    assert result.error_code == "policy_denied"


def test_policy_denies_unavailable_sandbox_execution() -> None:
    result = ToolExecutor(
        ToolRegistry(
            [make_spec(execution_target=ExecutionTarget.SANDBOX_REQUIRED)]
        )
    ).execute("test.echo", {"value": "hello"})

    assert result.status is ToolStatus.DENIED
    assert "sandbox" in (result.message or "").lower()


def test_executor_validates_input_before_running_handler() -> None:
    invoked = False

    def handler(raw_input: BaseModel, context: ToolContext) -> EchoOutput:
        nonlocal invoked
        del raw_input, context
        invoked = True
        return EchoOutput(echoed="unexpected")

    result = ToolExecutor(ToolRegistry([make_spec(handler=handler)])).execute(
        "test.echo",
        {},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "invalid_input"
    assert invoked is False


def test_executor_returns_validated_output() -> None:
    result = ToolExecutor(ToolRegistry([make_spec()])).execute(
        "test.echo",
        {"value": "hello"},
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.data == {"echoed": "hello"}


def test_executor_rejects_unknown_tools() -> None:
    result = ToolExecutor(ToolRegistry()).execute("test.unknown", {})

    assert result.status is ToolStatus.DENIED
    assert result.error_code == "unknown_tool"


def test_executor_reports_timeout() -> None:
    def slow_handler(raw_input: BaseModel, context: ToolContext) -> EchoOutput:
        del context
        args = EchoInput.model_validate(raw_input)
        sleep(0.05)
        return EchoOutput(echoed=args.value)

    result = ToolExecutor(
        ToolRegistry([make_spec(handler=slow_handler, timeout_seconds=0.01)])
    ).execute("test.echo", {"value": "hello"})

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "tool_timeout"


def test_executor_hides_unexpected_exception_details() -> None:
    def failing_handler(raw_input: BaseModel, context: ToolContext) -> EchoOutput:
        del raw_input, context
        raise RuntimeError("secret internal stack detail")

    result = ToolExecutor(
        ToolRegistry([make_spec(handler=failing_handler)])
    ).execute("test.echo", {"value": "hello"})

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "tool_execution_failed"
    assert "secret" not in (result.message or "")


def test_executor_preserves_explicit_public_errors() -> None:
    def public_failure(raw_input: BaseModel, context: ToolContext) -> EchoOutput:
        del raw_input, context
        raise ToolPublicError(
            "upstream_unavailable",
            "Data source is unavailable",
            category="upstream_unavailable",
            retryable=True,
        )

    result = ToolExecutor(
        ToolRegistry([make_spec(handler=public_failure)])
    ).execute("test.echo", {"value": "hello"})

    assert result.error_code == "upstream_unavailable"
    assert result.error_category == "upstream_unavailable"
    assert result.retryable is True
    assert result.message == "Data source is unavailable"


def test_tool_invocation_error_is_not_a_value_error() -> None:
    result = ToolExecutor(ToolRegistry([make_spec()])).execute("test.echo", {})

    with pytest.raises(ToolInvocationError) as caught:
        require_tool_data(result)

    assert not isinstance(caught.value, ValueError)
    assert caught.value.result.error_category == "invalid_request"


def test_finance_analysis_preserves_data_unavailable_category() -> None:
    class AnalysisAgent:
        def analyze(self, **kwargs):
            raise DataUnavailableError(
                "fund_nav_data_unavailable",
                "No verified NAV data is available",
            )

    handlers = FinanceToolHandlers(object(), AnalysisAgent())  # type: ignore[arg-type]

    with pytest.raises(ToolPublicError) as caught:
        handlers.analyze_asset(
            AnalyzeAssetInput(
                asset_type="fund",
                code="025856",
                start=date(2024, 1, 1),
                end=date(2026, 7, 15),
            ),
            ToolContext(),
        )

    assert caught.value.code == "fund_nav_data_unavailable"
    assert caught.value.category == "data_unavailable"
    assert caught.value.retryable is False


def test_finance_analysis_input_normalizes_code_and_rejects_reversed_dates() -> None:
    valid = AnalyzeAssetInput(
        asset_type="stock",
        code=" 600519 ",
        start=date(2024, 1, 1),
        end=date(2026, 7, 15),
    )

    assert valid.code == "600519"
    with pytest.raises(ValidationError, match="start must be on or before end"):
        AnalyzeAssetInput(
            asset_type="stock",
            code="600519",
            start=date(2026, 7, 15),
            end=date(2024, 1, 1),
        )


def test_finance_search_classifies_upstream_failure_as_retryable() -> None:
    class DataClient:
        def search_assets(self, *args, **kwargs):
            raise EastmoneyError("upstream disconnected")

    handlers = FinanceToolHandlers(DataClient(), object())  # type: ignore[arg-type]

    with pytest.raises(ToolPublicError) as caught:
        handlers.search_assets(
            SearchAssetsInput(keyword="华夏", limit=5),
            ToolContext(),
        )

    assert caught.value.code == "asset_search_upstream_unavailable"
    assert caught.value.category == "upstream_unavailable"
    assert caught.value.retryable is True


class FakeAuditRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, spec, context, result, input_summary) -> None:
        self.records.append(
            {
                "spec": spec,
                "context": context,
                "result": result,
                "input_summary": input_summary,
            }
        )


def test_executor_redacts_sensitive_audit_arguments() -> None:
    recorder = FakeAuditRecorder()
    executor = ToolExecutor(
        ToolRegistry([make_spec()]),
        audit_recorder=recorder,
    )

    executor.execute(
        "test.echo",
        {
            "value": "hello",
            "api_key": "must-not-be-recorded",
            "nested": {"access_token": "must-not-be-recorded"},
        },
    )

    summary = recorder.records[0]["input_summary"]
    assert summary["api_key"] == "[REDACTED]"
    assert summary["nested"]["access_token"] == "[REDACTED]"
    assert "must-not-be-recorded" not in str(summary)
