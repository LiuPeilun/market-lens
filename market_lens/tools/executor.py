from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime
from time import monotonic
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from market_lens.tools.models import (
    PolicyDecision,
    ToolContext,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from market_lens.tools.policy import ToolPolicy
from market_lens.tools.registry import ToolNotFoundError, ToolRegistry

logger = logging.getLogger(__name__)


class ToolPublicError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolInvocationError(ValueError):
    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.message or "Tool invocation failed")
        self.result = result


class ToolAuditRecorder(Protocol):
    def record(
        self,
        spec: ToolSpec,
        context: ToolContext,
        result: ToolResult,
        input_summary: dict[str, Any],
    ) -> None: ...


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolPolicy | None = None,
        audit_recorder: ToolAuditRecorder | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolPolicy()
        self.audit_recorder = audit_recorder

    def allowed_schemas(self, context: ToolContext | None = None) -> list[dict[str, Any]]:
        invocation_context = context or ToolContext()
        return [
            spec.schema()
            for spec in self.registry.list()
            if self.policy.evaluate(spec, invocation_context).decision is PolicyDecision.ALLOW
        ]

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        started = monotonic()
        invocation_context = context or ToolContext()
        try:
            spec = self.registry.get(tool_name)
        except ToolNotFoundError:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.DENIED,
                policy_decision=PolicyDecision.DENY,
                error_code="unknown_tool",
                message="The requested tool is not registered",
                duration_ms=_duration_ms(started),
            )

        input_summary = summarize_arguments(arguments)
        try:
            validated_input = spec.input_model.model_validate(arguments)
        except ValidationError:
            result = ToolResult(
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                policy_decision=PolicyDecision.DENY,
                error_code="invalid_input",
                message="Tool input validation failed",
                duration_ms=_duration_ms(started),
            )
            self._record(spec, invocation_context, result, input_summary)
            return result

        evaluation = self.policy.evaluate(spec, invocation_context)
        if evaluation.decision is PolicyDecision.DENY:
            result = ToolResult(
                tool_name=tool_name,
                status=ToolStatus.DENIED,
                policy_decision=evaluation.decision,
                error_code="policy_denied",
                message=evaluation.reason,
                duration_ms=_duration_ms(started),
            )
            self._record(spec, invocation_context, result, input_summary)
            return result
        if evaluation.decision is PolicyDecision.CONFIRMATION_REQUIRED:
            result = ToolResult(
                tool_name=tool_name,
                status=ToolStatus.CONFIRMATION_REQUIRED,
                policy_decision=evaluation.decision,
                error_code="confirmation_required",
                message=evaluation.reason,
                duration_ms=_duration_ms(started),
            )
            self._record(spec, invocation_context, result, input_summary)
            return result

        result = self._run(spec, validated_input, invocation_context, started)
        self._record(spec, invocation_context, result, input_summary)
        return result

    def _run(
        self,
        spec: ToolSpec,
        validated_input: BaseModel,
        context: ToolContext,
        started: float,
    ) -> ToolResult:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-lens-tool")
        future = pool.submit(spec.handler, validated_input, context)
        timed_out = False
        try:
            raw_output = future.result(timeout=spec.timeout_seconds)
            if isinstance(raw_output, BaseModel):
                raw_output = raw_output.model_dump(mode="python")
            output = spec.output_model.model_validate(raw_output)
        except TimeoutError:
            timed_out = True
            future.cancel()
            return ToolResult(
                tool_name=spec.name,
                status=ToolStatus.ERROR,
                policy_decision=PolicyDecision.ALLOW,
                error_code="tool_timeout",
                message="Tool execution exceeded its time limit",
                duration_ms=_duration_ms(started),
            )
        except ToolPublicError as exc:
            return ToolResult(
                tool_name=spec.name,
                status=ToolStatus.ERROR,
                policy_decision=PolicyDecision.ALLOW,
                error_code=exc.code,
                message=exc.message,
                duration_ms=_duration_ms(started),
            )
        except ValidationError:
            logger.exception("Tool returned invalid output: %s", spec.name)
            return ToolResult(
                tool_name=spec.name,
                status=ToolStatus.ERROR,
                policy_decision=PolicyDecision.ALLOW,
                error_code="invalid_output",
                message="Tool returned an invalid result",
                duration_ms=_duration_ms(started),
            )
        except Exception:
            logger.exception("Tool execution failed: %s", spec.name)
            return ToolResult(
                tool_name=spec.name,
                status=ToolStatus.ERROR,
                policy_decision=PolicyDecision.ALLOW,
                error_code="tool_execution_failed",
                message="Tool execution failed",
                duration_ms=_duration_ms(started),
            )
        finally:
            pool.shutdown(wait=not timed_out, cancel_futures=True)

        return ToolResult(
            tool_name=spec.name,
            status=ToolStatus.SUCCESS,
            policy_decision=PolicyDecision.ALLOW,
            data=output.model_dump(mode="json"),
            duration_ms=_duration_ms(started),
        )

    def _record(
        self,
        spec: ToolSpec,
        context: ToolContext,
        result: ToolResult,
        input_summary: dict[str, Any],
    ) -> None:
        if self.audit_recorder is None:
            return
        try:
            self.audit_recorder.record(spec, context, result, input_summary)
        except Exception:
            logger.exception("Tool audit recording failed: %s", spec.name)


def summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _summarize_value(value, key=str(key), depth=0)
        for key, value in arguments.items()
    }


def _summarize_value(value: Any, key: str, depth: int) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if depth >= 3:
        return "[TRUNCATED]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        return {
            str(child_key): _summarize_value(
                child_value,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in list(value.items())[:20]
        }
    if isinstance(value, list | tuple):
        return [_summarize_value(item, key=key, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value if len(value) <= 200 else f"{value[:200]}..."
    if isinstance(value, date | datetime | UUID):
        return str(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:200]


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    sensitive_names = {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "private_key",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
    return normalized in sensitive_names or normalized.endswith(("_token", "_secret", "_key"))


def require_tool_data(result: ToolResult) -> dict[str, Any]:
    if result.status is ToolStatus.SUCCESS and result.data is not None:
        return result.data
    raise ToolInvocationError(result)


def _duration_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
