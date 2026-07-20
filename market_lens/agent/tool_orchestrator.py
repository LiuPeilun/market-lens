from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from market_lens.agent.llm_client import LLMChatTurn, LLMError, OpenAICompatibleLLMClient
from market_lens.config import settings
from market_lens.tools.executor import ToolExecutor
from market_lens.tools.models import ToolContext, ToolResult


@dataclass(frozen=True)
class ToolTrace:
    tool_name: str
    status: str
    error_code: str | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    answer: str
    traces: list[ToolTrace]


@dataclass(frozen=True)
class StreamPreparation:
    messages: list[dict[str, Any]]
    traces: list[ToolTrace]


class ToolOrchestrator:
    def __init__(
        self,
        llm_client: OpenAICompatibleLLMClient,
        tool_executor: ToolExecutor,
        tool_context: ToolContext | None = None,
        *,
        max_rounds: int | None = None,
        max_calls: int | None = None,
        max_result_chars: int | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.tool_context = tool_context or ToolContext()
        self.max_rounds = max_rounds or settings.llm_tool_max_rounds
        self.max_calls = max_calls or settings.llm_tool_max_calls
        self.max_result_chars = max_result_chars or settings.llm_tool_result_max_chars
        if min(self.max_rounds, self.max_calls, self.max_result_chars) <= 0:
            raise ValueError("Tool orchestration limits must be greater than zero")

    def run(self, messages: list[dict[str, Any]]) -> OrchestrationResult:
        working = [dict(message) for message in messages]
        tools, alias_map = self._tool_catalog()
        if not tools:
            return OrchestrationResult(answer=self.llm_client.complete(working), traces=[])

        traces: list[ToolTrace] = []
        calls_used = 0
        for _ in range(self.max_rounds):
            turn = self.llm_client.complete_turn(working, tools)
            if not turn.tool_calls:
                if not turn.content:
                    raise LLMError("LLM finished tool orchestration without an answer")
                return OrchestrationResult(answer=turn.content, traces=traces)
            calls_used = self._execute_turn(
                working,
                turn,
                alias_map,
                traces,
                calls_used,
            )
        raise LLMError("LLM tool orchestration exceeded its round limit")

    def prepare_stream(self, messages: list[dict[str, Any]]) -> StreamPreparation:
        working = [dict(message) for message in messages]
        tools, alias_map = self._tool_catalog()
        if not tools:
            return StreamPreparation(messages=working, traces=[])

        traces: list[ToolTrace] = []
        calls_used = 0
        for _ in range(self.max_rounds):
            turn = self.llm_client.complete_turn(working, tools)
            if not turn.tool_calls:
                return StreamPreparation(messages=working, traces=traces)
            calls_used = self._execute_turn(
                working,
                turn,
                alias_map,
                traces,
                calls_used,
            )
        raise LLMError("LLM tool orchestration exceeded its round limit")

    def _tool_catalog(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        tools: list[dict[str, Any]] = []
        alias_map: dict[str, str] = {}
        used_aliases: set[str] = set()
        for schema in self.tool_executor.allowed_schemas(self.tool_context):
            public_name = str(schema["name"])
            alias = _tool_alias(public_name, used_aliases)
            used_aliases.add(alias)
            alias_map[alias] = public_name
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": alias,
                        "description": f"{schema['description']} (Market Lens: {public_name})",
                        "parameters": schema["input_schema"],
                    },
                }
            )
        return tools, alias_map

    def _execute_turn(
        self,
        messages: list[dict[str, Any]],
        turn: LLMChatTurn,
        alias_map: dict[str, str],
        traces: list[ToolTrace],
        calls_used: int,
    ) -> int:
        if calls_used + len(turn.tool_calls) > self.max_calls:
            raise LLMError("LLM tool orchestration exceeded its call limit")
        messages.append(_assistant_tool_message(turn))
        for call in turn.tool_calls:
            public_name = alias_map.get(call.name)
            if public_name is None:
                payload = {
                    "status": "denied",
                    "error_code": "unknown_tool_alias",
                    "message": "The requested tool was not offered to the model",
                }
                traces.append(
                    ToolTrace(
                        tool_name=call.name,
                        status="denied",
                        error_code="unknown_tool_alias",
                    )
                )
            else:
                result = self.tool_executor.execute(
                    public_name,
                    call.arguments,
                    context=self.tool_context,
                )
                payload = _tool_result_payload(result)
                traces.append(
                    ToolTrace(
                        tool_name=public_name,
                        status=result.status.value,
                        error_code=result.error_code,
                    )
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": _bounded_json(payload, self.max_result_chars),
                }
            )
        return calls_used + len(turn.tool_calls)


def _assistant_tool_message(turn: LLMChatTurn) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": turn.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in turn.tool_calls
        ],
    }


def _tool_result_payload(result: ToolResult) -> dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "status": result.status.value,
        "data": result.data,
        "error_code": result.error_code,
        "message": result.message,
    }


def _bounded_json(payload: dict[str, Any], max_chars: int) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return encoded
    return json.dumps(
        {
            "status": payload.get("status"),
            "error_code": "tool_result_truncated",
            "truncated": True,
            "content_preview": encoded[:max_chars],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _tool_alias(public_name: str, used: set[str]) -> str:
    alias = re.sub(r"[^A-Za-z0-9_-]+", "__", public_name).strip("_")
    if not alias or not alias[0].isalpha():
        alias = f"tool__{alias}"
    if len(alias) > 64 or alias in used:
        digest = hashlib.sha256(public_name.encode("utf-8")).hexdigest()[:10]
        alias = f"{alias[:53]}_{digest}"
    return alias
