from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from market_lens.agent.llm_client import LLMChatTurn, LLMToolCall
from market_lens.agent.tool_orchestrator import ToolOrchestrator
from market_lens.tools.executor import ToolExecutor
from market_lens.tools.models import (
    ExecutionTarget,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolRisk,
    ToolSpec,
)
from market_lens.tools.policy import ToolPolicy
from market_lens.tools.registry import ToolRegistry


class LookupInput(ToolInput):
    query: str


class LookupOutput(ToolOutput):
    answer: str


class FakeLLMClient:
    def __init__(self, turns: list[LLMChatTurn], final_stream: list[str] | None = None) -> None:
        self.turns = list(turns)
        self.final_stream = final_stream or []
        self.requests: list[dict[str, Any]] = []
        self.complete_calls = 0

    def complete_turn(self, messages, tools=None) -> LLMChatTurn:
        self.requests.append({"messages": messages, "tools": tools})
        return self.turns.pop(0)

    def complete(self, messages) -> str:
        self.complete_calls += 1
        return "direct answer"

    def stream_complete(self, messages) -> Iterator[str]:
        self.requests.append({"stream_messages": messages})
        yield from self.final_stream


def make_spec(risk: ToolRisk = ToolRisk.READ) -> ToolSpec:
    def handler(raw_input: BaseModel, context: ToolContext) -> LookupOutput:
        del context
        validated = LookupInput.model_validate(raw_input)
        return LookupOutput(answer=f"found:{validated.query}")

    return ToolSpec(
        name="research.lookup",
        capability="research",
        description="Look up reviewed research",
        input_model=LookupInput,
        output_model=LookupOutput,
        handler=handler,
        risk=risk,
        execution_target=ExecutionTarget.TRUSTED_LOCAL,
    )


def test_orchestrator_executes_tool_and_returns_follow_up_answer() -> None:
    client = FakeLLMClient(
        [
            LLMChatTurn(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="research__lookup",
                        arguments={"query": "MCP"},
                    )
                ],
            ),
            LLMChatTurn(content="grounded answer", tool_calls=[]),
        ]
    )
    executor = ToolExecutor(ToolRegistry([make_spec()]))

    result = ToolOrchestrator(client, executor).run(
        [{"role": "user", "content": "Explain MCP"}]
    )

    assert result.answer == "grounded answer"
    assert result.traces[0].tool_name == "research.lookup"
    assert result.traces[0].status == "success"
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "found:MCP" in tool_message["content"]


def test_orchestrator_does_not_offer_confirmation_required_tools() -> None:
    client = FakeLLMClient([])
    executor = ToolExecutor(
        ToolRegistry([make_spec(ToolRisk.WRITE)]),
        ToolPolicy(),
    )

    result = ToolOrchestrator(client, executor).run(
        [{"role": "user", "content": "Change it"}]
    )

    assert result.answer == "direct answer"
    assert result.traces == []
    assert client.complete_calls == 1


def test_stream_preparation_executes_tools_before_final_stream() -> None:
    client = FakeLLMClient(
        [
            LLMChatTurn(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="research__lookup",
                        arguments={"query": "DeepWiki"},
                    )
                ],
            ),
            LLMChatTurn(content="draft answer", tool_calls=[]),
        ],
        final_stream=["final ", "answer"],
    )
    orchestrator = ToolOrchestrator(client, ToolExecutor(ToolRegistry([make_spec()])))

    prepared = orchestrator.prepare_stream([{"role": "user", "content": "Question"}])
    streamed = list(client.stream_complete(prepared.messages))

    assert prepared.messages[-1]["role"] == "tool"
    assert streamed == ["final ", "answer"]
    assert prepared.traces[0].status == "success"
