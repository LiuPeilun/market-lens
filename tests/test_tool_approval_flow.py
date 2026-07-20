from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import UUID

from market_lens.agent.chat_agent import ChatAgent
from market_lens.agent.llm_client import LLMChatTurn, LLMToolCall
from market_lens.capabilities.code.tools import register_code_tools
from market_lens.sandbox.models import SandboxRequest, SandboxResult, SandboxStatus
from market_lens.sandbox.runner import SandboxRunner
from market_lens.tools.executor import ToolExecutor
from market_lens.tools.models import ToolApprovalGrant
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
        )


class FakeLLMClient:
    def __init__(self) -> None:
        self.turns = [
            LLMChatTurn(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call-code",
                        name="code__run_python",
                        arguments={"code": "print(6 * 7)", "timeout_seconds": 5},
                    )
                ],
            ),
            LLMChatTurn(content="The result is ready", tool_calls=[]),
        ]

    def complete_turn(self, messages, tools=None) -> LLMChatTurn:
        del messages, tools
        return self.turns.pop(0)

    def complete(self, messages) -> str:
        del messages
        return "unused"

    def stream_complete(self, messages) -> Iterator[str]:
        assert "42" in messages[-1]["content"]
        yield "计算结果是 42。"


def test_chat_agent_pauses_then_resumes_exact_sandbox_tool_call() -> None:
    runner = FakeSandboxRunner()
    registry = ToolRegistry()
    register_code_tools(registry, runner)
    agent = ChatAgent(
        llm_client=FakeLLMClient(),  # type: ignore[arg-type]
        use_llm=True,
        tool_executor=ToolExecutor(
            registry,
            ToolPolicy(sandbox_available=True),
        ),
    )

    initial = list(
        agent.stream_reply(
            "For test/repo, run Python to calculate 6 * 7.",
            context=None,
            start=date(2026, 1, 1),
            end=date(2026, 7, 20),
        )
    )
    approval_event = initial[-1]
    approval = approval_event["approval"]

    assert approval_event["type"] == "approval_required"
    assert runner.requests == []

    resumed = list(
        agent.resume_stream(
            approval_event["checkpoint"],
            approved=True,
            grant=ToolApprovalGrant(
                approval_id=UUID("22222222-2222-2222-2222-222222222222"),
                tool_name=approval["tool_name"],
                arguments_digest=approval["arguments_digest"],
            ),
        )
    )

    assert len(runner.requests) == 1
    assert "".join(event.get("delta", "") for event in resumed) == "计算结果是 42。"
    assert resumed[-1] == {"type": "done"}
