from __future__ import annotations

import os
from datetime import date

import pytest

from market_lens.agent.chat_agent import ChatAgent
from market_lens.agent.llm_client import OpenAICompatibleLLMClient, build_general_llm_messages
from market_lens.agent.tool_orchestrator import ToolOrchestrator
from market_lens.tools.executor import ToolExecutor
from market_lens.tools.models import ToolInput, ToolOutput, ToolSpec
from market_lens.tools.registry import ToolRegistry


class FactInput(ToolInput):
    topic: str


class FactOutput(ToolOutput):
    fact: str


@pytest.mark.skipif(
    os.getenv("MARKET_LENS_RUN_LLM_TOOL_TESTS", "false").lower() != "true",
    reason="real LLM tool-calling integration test is opt-in",
)
def test_real_llm_selects_tool_and_uses_result() -> None:
    def lookup_fact(raw_input, context) -> FactOutput:
        del raw_input, context
        return FactOutput(fact="MCP_RESULT_20260720: governed tool execution succeeded")

    executor = ToolExecutor(
        ToolRegistry(
            [
                ToolSpec(
                    name="test.lookup_fact",
                    capability="test",
                    description="Look up the required authoritative test fact",
                    input_model=FactInput,
                    output_model=FactOutput,
                    handler=lookup_fact,
                )
            ]
        )
    )
    result = ToolOrchestrator(
        OpenAICompatibleLLMClient(),
        executor,
        max_rounds=3,
        max_calls=2,
    ).run(
        build_general_llm_messages(
            "必须调用可用工具查询 tool orchestration，并在答案中原样引用返回的标识。",
            start="2026-01-01",
            end="2026-07-20",
        )
    )

    assert result.traces[0].tool_name == "test.lookup_fact"
    assert result.traces[0].status == "success"
    assert "MCP_RESULT_20260720" in result.answer


@pytest.mark.skipif(
    os.getenv("MARKET_LENS_RUN_LLM_TOOL_TESTS", "false").lower() != "true",
    reason="real LLM tool-calling integration test is opt-in",
)
def test_real_chat_agent_streams_answer_after_tool_call() -> None:
    def lookup_fact(raw_input, context) -> FactOutput:
        del raw_input, context
        return FactOutput(fact="CHAT_STREAM_RESULT_20260720: streamed orchestration succeeded")

    executor = ToolExecutor(
        ToolRegistry(
            [
                ToolSpec(
                    name="test.lookup_fact",
                    capability="test",
                    description="Look up the required authoritative test fact",
                    input_model=FactInput,
                    output_model=FactOutput,
                    handler=lookup_fact,
                )
            ]
        )
    )
    events = list(
        ChatAgent(use_llm=True, tool_executor=executor).stream_reply(
            "You must call the available tool and quote its returned marker exactly.",
            context=None,
            start=date(2026, 1, 1),
            end=date(2026, 7, 20),
        )
    )

    answer = "".join(event.get("delta", "") for event in events if event["type"] == "token")
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "done"
    assert "CHAT_STREAM_RESULT_20260720" in answer
