from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from market_lens.agent.llm_client import (
    LLMChatTurn,
    LLMError,
    LLMToolCall,
    OpenAICompatibleLLMClient,
)
from market_lens.config import settings
from market_lens.tools.executor import (
    ToolExecutor,
    summarize_arguments,
    tool_arguments_digest,
)
from market_lens.tools.models import (
    PolicyDecision,
    ToolApprovalGrant,
    ToolContext,
    ToolResult,
    ToolStatus,
)

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ToolTrace:
    tool_name: str
    status: str
    error_code: str | None = None


@dataclass(frozen=True)
class PendingToolApproval:
    tool_name: str
    tool_alias: str
    tool_call_id: str
    arguments: dict[str, Any]
    arguments_digest: str
    input_summary: dict[str, Any]
    reason: str
    risk: str
    execution_target: str


@dataclass(frozen=True)
class OrchestrationResult:
    answer: str | None
    traces: list[ToolTrace]
    approval: PendingToolApproval | None = None
    checkpoint: dict[str, Any] | None = None


@dataclass(frozen=True)
class StreamPreparation:
    messages: list[dict[str, Any]]
    traces: list[ToolTrace]
    content: str | None = None
    approval: PendingToolApproval | None = None
    checkpoint: dict[str, Any] | None = None


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
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.tool_context = tool_context or ToolContext()
        self.max_rounds = max_rounds or settings.llm_tool_max_rounds
        self.max_calls = max_calls or settings.llm_tool_max_calls
        self.max_result_chars = max_result_chars or settings.llm_tool_result_max_chars
        self.progress_callback = progress_callback
        if min(self.max_rounds, self.max_calls, self.max_result_chars) <= 0:
            raise ValueError("Tool orchestration limits must be greater than zero")

    def run(self, messages: list[dict[str, Any]]) -> OrchestrationResult:
        prepared = self._advance(
            [dict(message) for message in messages],
            traces=[],
            calls_used=0,
            rounds_used=0,
        )
        if prepared.approval is not None:
            return OrchestrationResult(
                answer=None,
                traces=prepared.traces,
                approval=prepared.approval,
                checkpoint=prepared.checkpoint,
            )
        if prepared.content:
            return OrchestrationResult(answer=prepared.content, traces=prepared.traces)
        turn = self.llm_client.complete_turn(prepared.messages)
        if turn.tool_calls or not turn.content:
            raise LLMError("LLM finished tool orchestration without an answer")
        return OrchestrationResult(answer=turn.content, traces=prepared.traces)

    def prepare_stream(self, messages: list[dict[str, Any]]) -> StreamPreparation:
        return self._advance(
            [dict(message) for message in messages],
            traces=[],
            calls_used=0,
            rounds_used=0,
        )

    def resume_stream(
        self,
        checkpoint: dict[str, Any],
        *,
        approved: bool,
        grant: ToolApprovalGrant | None = None,
    ) -> StreamPreparation:
        state = _parse_checkpoint(checkpoint)
        pending = state["pending"]
        messages = state["messages"]
        traces = state["traces"]

        if approved:
            if grant is None:
                raise LLMError("Approved tool resumption requires an approval grant")
            self._emit_tool_progress(
                pending.tool_call_id,
                pending.tool_name,
                "running",
            )
            try:
                result = self.tool_executor.execute(
                    pending.tool_name,
                    pending.arguments,
                    context=self.tool_context,
                    approval=grant,
                )
            except Exception:
                self._emit_tool_progress(
                    pending.tool_call_id,
                    pending.tool_name,
                    "failed",
                )
                raise
            if result.status is ToolStatus.CONFIRMATION_REQUIRED:
                raise LLMError("Approval grant did not match the pending tool invocation")
        else:
            result = ToolResult(
                tool_name=pending.tool_name,
                status=ToolStatus.DENIED,
                policy_decision=PolicyDecision.DENY,
                error_code="user_denied",
                message="The user denied this tool invocation",
            )

        self._emit_tool_progress(
            pending.tool_call_id,
            pending.tool_name,
            _progress_status(result),
        )
        traces.append(_trace_from_result(result))
        messages.append(
            _tool_message(
                pending.tool_call_id,
                pending.tool_alias,
                _tool_result_payload(result),
                self.max_result_chars,
            )
        )
        for call in state["deferred_calls"]:
            payload = {
                "status": "denied",
                "error_code": "parallel_call_deferred",
                "message": "This parallel call was deferred while another call required approval",
            }
            traces.append(
                ToolTrace(
                    tool_name=call.name,
                    status="denied",
                    error_code="parallel_call_deferred",
                )
            )
            messages.append(
                _tool_message(call.id, call.name, payload, self.max_result_chars)
            )

        return self._advance(
            messages,
            traces=traces,
            calls_used=state["calls_used"],
            rounds_used=state["rounds_used"],
        )

    def _advance(
        self,
        messages: list[dict[str, Any]],
        *,
        traces: list[ToolTrace],
        calls_used: int,
        rounds_used: int,
    ) -> StreamPreparation:
        tools, alias_map = self._tool_catalog()
        if not tools:
            return StreamPreparation(messages=messages, traces=traces)

        while rounds_used < self.max_rounds:
            round_number = rounds_used + 1
            self._emit_progress(
                {
                    "type": "progress",
                    "id": f"planning:{round_number}",
                    "stage": "planning",
                    "status": "running",
                    "title": "正在规划下一步",
                }
            )
            try:
                turn = self.llm_client.complete_turn(messages, tools)
            except Exception:
                self._emit_progress(
                    {
                        "type": "progress",
                        "id": f"planning:{round_number}",
                        "stage": "planning",
                        "status": "failed",
                        "title": "步骤规划失败",
                    }
                )
                raise
            rounds_used += 1
            self._emit_progress(
                {
                    "type": "progress",
                    "id": f"planning:{round_number}",
                    "stage": "planning",
                    "status": "completed",
                    "title": "已完成步骤规划",
                    "detail": (
                        f"将调用 {len(turn.tool_calls)} 个工具"
                        if turn.tool_calls
                        else "无需继续调用工具"
                    ),
                }
            )
            if not turn.tool_calls:
                return StreamPreparation(messages=messages, traces=traces, content=turn.content)
            calls_used, approval, deferred_calls = self._execute_turn(
                messages,
                turn,
                alias_map,
                traces,
                calls_used,
            )
            if approval is not None:
                return StreamPreparation(
                    messages=messages,
                    traces=traces,
                    approval=approval,
                    checkpoint=_checkpoint_payload(
                        messages=messages,
                        traces=traces,
                        pending=approval,
                        deferred_calls=deferred_calls,
                        calls_used=calls_used,
                        rounds_used=rounds_used,
                    ),
                )
        raise LLMError("LLM tool orchestration exceeded its round limit")

    def _tool_catalog(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        tools: list[dict[str, Any]] = []
        alias_map: dict[str, str] = {}
        used_aliases: set[str] = set()
        for schema in self.tool_executor.offered_schemas(self.tool_context):
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
    ) -> tuple[int, PendingToolApproval | None, list[LLMToolCall]]:
        if calls_used + len(turn.tool_calls) > self.max_calls:
            raise LLMError("LLM tool orchestration exceeded its call limit")
        calls_used += len(turn.tool_calls)
        messages.append(_assistant_tool_message(turn))

        for index, call in enumerate(turn.tool_calls):
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
                self._emit_progress(
                    {
                        "type": "progress",
                        "id": f"tool:{call.id}",
                        "stage": "tool",
                        "status": "failed",
                        "title": "工具调用失败",
                        "detail": "模型请求了未开放的工具",
                        "tool_name": call.name,
                    }
                )
                messages.append(_tool_message(call.id, call.name, payload, self.max_result_chars))
                continue

            self._emit_tool_progress(call.id, public_name, "running")
            try:
                result = self.tool_executor.execute(
                    public_name,
                    call.arguments,
                    context=self.tool_context,
                )
            except Exception:
                self._emit_tool_progress(call.id, public_name, "failed")
                raise
            traces.append(_trace_from_result(result))
            if result.status is ToolStatus.CONFIRMATION_REQUIRED:
                spec = self.tool_executor.registry.get(public_name)
                approval = PendingToolApproval(
                    tool_name=public_name,
                    tool_alias=call.name,
                    tool_call_id=call.id,
                    arguments=call.arguments,
                    arguments_digest=tool_arguments_digest(call.arguments),
                    input_summary=summarize_arguments(call.arguments),
                    reason=result.message or "This tool requires user approval",
                    risk=spec.risk.value,
                    execution_target=spec.execution_target.value,
                )
                self._emit_tool_progress(
                    call.id,
                    public_name,
                    "waiting_approval",
                )
                return calls_used, approval, turn.tool_calls[index + 1 :]

            self._emit_tool_progress(
                call.id,
                public_name,
                _progress_status(result),
            )
            messages.append(
                _tool_message(
                    call.id,
                    call.name,
                    _tool_result_payload(result),
                    self.max_result_chars,
                )
            )
        return calls_used, None, []

    def _emit_progress(self, event: dict[str, Any]) -> None:
        if self.progress_callback is not None:
            self.progress_callback(event)

    def _emit_tool_progress(
        self,
        call_id: str,
        tool_name: str,
        status: str,
    ) -> None:
        title, detail = _tool_progress_label(tool_name, status)
        self._emit_progress(
            {
                "type": "progress",
                "id": f"tool:{call_id}",
                "stage": "tool",
                "status": status,
                "title": title,
                "detail": detail,
                "tool_name": tool_name,
            }
        )


def _progress_status(result: ToolResult) -> str:
    if result.status is ToolStatus.SUCCESS:
        return "completed"
    if result.status is ToolStatus.CONFIRMATION_REQUIRED:
        return "waiting_approval"
    return "failed"


def _tool_progress_label(tool_name: str, status: str) -> tuple[str, str]:
    if tool_name == "finance.search_assets":
        action = "搜索市场标的"
    elif tool_name == "finance.analyze_asset":
        action = "获取市场数据并执行分析"
    elif tool_name == "workspace.list_files":
        action = "读取工作区文件列表"
    elif tool_name == "workspace.read_file":
        action = "读取工作区文件"
    elif tool_name == "workspace.write_file":
        action = "写入工作区文件"
    elif tool_name.startswith("code."):
        action = "执行沙箱任务"
    elif tool_name.startswith("mcp.deepwiki."):
        action = "查询 DeepWiki"
    elif tool_name.startswith("mcp."):
        server = tool_name.split(".", 2)[1]
        action = f"查询 {server}"
    else:
        action = "调用工具"

    if status == "completed":
        return f"已完成{action}", tool_name
    if status == "failed":
        return f"{action}失败", tool_name
    if status == "waiting_approval":
        return f"{action}等待审批", tool_name
    return f"正在{action}", tool_name


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


def _tool_message(
    call_id: str,
    alias: str,
    payload: dict[str, Any],
    max_chars: int,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": alias,
        "content": _bounded_json(payload, max_chars),
    }


def _trace_from_result(result: ToolResult) -> ToolTrace:
    return ToolTrace(
        tool_name=result.tool_name,
        status=result.status.value,
        error_code=result.error_code,
    )


def _tool_result_payload(result: ToolResult) -> dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "status": result.status.value,
        "data": result.data,
        "error_code": result.error_code,
        "message": result.message,
    }


def _checkpoint_payload(
    *,
    messages: list[dict[str, Any]],
    traces: list[ToolTrace],
    pending: PendingToolApproval,
    deferred_calls: list[LLMToolCall],
    calls_used: int,
    rounds_used: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "messages": messages,
        "traces": [asdict(trace) for trace in traces],
        "pending": asdict(pending),
        "deferred_calls": [asdict(call) for call in deferred_calls],
        "calls_used": calls_used,
        "rounds_used": rounds_used,
    }


def _parse_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        if payload.get("version") != 1:
            raise ValueError("unsupported version")
        messages = payload["messages"]
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise ValueError("invalid messages")
        pending = PendingToolApproval(**payload["pending"])
        traces = [ToolTrace(**item) for item in payload.get("traces", [])]
        deferred = [LLMToolCall(**item) for item in payload.get("deferred_calls", [])]
        calls_used = int(payload["calls_used"])
        rounds_used = int(payload["rounds_used"])
        if min(calls_used, rounds_used) < 0:
            raise ValueError("invalid counters")
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError("Stored tool approval checkpoint is invalid") from exc
    return {
        "messages": [dict(message) for message in messages],
        "traces": traces,
        "pending": pending,
        "deferred_calls": deferred,
        "calls_used": calls_used,
        "rounds_used": rounds_used,
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
