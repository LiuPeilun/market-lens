from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from market_lens.agent.llm_client import LLMChatTurn, LLMToolCall
from market_lens.agent.tool_orchestrator import ToolOrchestrator
from market_lens.tools.executor import ToolExecutor, ToolPublicError
from market_lens.tools.models import (
    ExecutionTarget,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolSpec,
)
from market_lens.tools.registry import ToolRegistry


class LookupInput(ToolInput):
    query: str


class LookupOutput(ToolOutput):
    answer: str


class PartialFailureLLMClient:
    def __init__(self) -> None:
        self.turns = [
            LLMChatTurn(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call-failed",
                        name="research__fail",
                        arguments={"query": "primary source"},
                    ),
                    LLMToolCall(
                        id="call-success",
                        name="research__lookup",
                        arguments={"query": "fallback source"},
                    ),
                ],
            ),
            LLMChatTurn(
                content="answer from the available evidence",
                tool_calls=[],
            ),
        ]
        self.requests: list[dict[str, Any]] = []

    def complete_turn(self, messages, tools=None) -> LLMChatTurn:
        self.requests.append({"messages": messages, "tools": tools})
        return self.turns.pop(0)


def test_orchestrator_keeps_successful_result_when_sibling_tool_fails() -> None:
    def failing_handler(raw_input: BaseModel, context: ToolContext) -> LookupOutput:
        del raw_input, context
        raise ToolPublicError(
            "research_upstream_timeout",
            "Research provider timed out",
            category="upstream_unavailable",
            retryable=True,
        )

    def successful_handler(
        raw_input: BaseModel,
        context: ToolContext,
    ) -> LookupOutput:
        del context
        value = LookupInput.model_validate(raw_input)
        return LookupOutput(answer=f"found:{value.query}")

    registry = ToolRegistry(
        [
            ToolSpec(
                name="research.fail",
                capability="research",
                description="Inject a reviewed research failure",
                input_model=LookupInput,
                output_model=LookupOutput,
                handler=failing_handler,
                execution_target=ExecutionTarget.TRUSTED_LOCAL,
            ),
            ToolSpec(
                name="research.lookup",
                capability="research",
                description="Return reviewed fallback research",
                input_model=LookupInput,
                output_model=LookupOutput,
                handler=successful_handler,
                execution_target=ExecutionTarget.TRUSTED_LOCAL,
            ),
        ]
    )
    client = PartialFailureLLMClient()
    progress: list[dict[str, Any]] = []

    result = ToolOrchestrator(
        client,  # type: ignore[arg-type]
        ToolExecutor(registry),
        progress_callback=progress.append,
    ).run([{"role": "user", "content": "Compare the sources"}])

    assert result.answer == "answer from the available evidence"
    assert [
        (trace.tool_name, trace.status, trace.error_code)
        for trace in result.traces
    ] == [
        ("research.fail", "error", "research_upstream_timeout"),
        ("research.lookup", "success", None),
    ]
    follow_up_messages = client.requests[1]["messages"]
    failed_message = next(
        message
        for message in follow_up_messages
        if message.get("tool_call_id") == "call-failed"
    )
    successful_message = next(
        message
        for message in follow_up_messages
        if message.get("tool_call_id") == "call-success"
    )
    assert '"retryable":true' in failed_message["content"]
    assert "Research provider timed out" in failed_message["content"]
    assert "found:fallback source" in successful_message["content"]
    progress_states = [(event["id"], event["status"]) for event in progress]
    assert ("tool:call-failed", "failed") in progress_states
    assert ("tool:call-success", "completed") in progress_states
