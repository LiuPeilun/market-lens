from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from market_lens import __version__
from market_lens.agent.chat_agent import ChatAgent, ChatAssetContext
from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.api.auth import get_current_user
from market_lens.api.schemas import (
    AnalysisHistoryResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    AssetSearchResponse,
    ChatRequest,
    ChatResponse,
    ToolApprovalDecisionRequest,
)
from market_lens.capabilities.finance.tools import ANALYZE_ASSET_TOOL, SEARCH_ASSETS_TOOL
from market_lens.config import settings
from market_lens.data.eastmoney import EastmoneyClient, EastmoneyError, stock_bars_from_valuations
from market_lens.mcp.factory import build_mcp_gateway
from market_lens.sandbox.factory import build_sandbox_runner
from market_lens.storage.supabase import (
    AuthenticatedUser,
    SupabaseError,
    SupabaseRepository,
)
from market_lens.storage.tool_audit import SupabaseToolAuditRecorder
from market_lens.storage.workspace import SupabaseWorkspaceStore
from market_lens.tools.catalog import build_default_executor
from market_lens.tools.executor import require_tool_data
from market_lens.tools.models import ToolApprovalGrant, ToolContext

mcp_gateway = build_mcp_gateway()
sandbox_runner = build_sandbox_runner()
logger = logging.getLogger(__name__)


async def maintain_mcp_discovery() -> None:
    await mcp_gateway.astart()
    retry_seconds = settings.mcp_discovery_retry_seconds
    while retry_seconds > 0 and not mcp_gateway.is_available() and mcp_gateway.startup_errors:
        await asyncio.sleep(retry_seconds)
        await mcp_gateway.arefresh()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    del application
    settings.validate_runtime()
    discovery_task: asyncio.Task[None] | None = None
    if settings.mcp_startup_strict:
        await mcp_gateway.astart()
    else:
        discovery_task = asyncio.create_task(maintain_mcp_discovery())
    try:
        yield
    finally:
        if discovery_task is not None:
            if not discovery_task.done():
                discovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await discovery_task
        await mcp_gateway.aclose()


app = FastAPI(
    title="Market Lens API",
    version=__version__,
    description="Agent service for market data retrieval and valuation analysis.",
    lifespan=lifespan,
)


def get_client() -> EastmoneyClient:
    return EastmoneyClient()


def get_repository() -> SupabaseRepository:
    return SupabaseRepository()


def to_sse_data(event: dict[str, Any]) -> str:
    payload = jsonable_encoder(event)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/health")
def health() -> dict[str, Any]:
    configured = any(server.enabled for server in mcp_gateway.config.servers)
    if mcp_gateway.is_available():
        mcp_status = "available"
    elif not configured:
        mcp_status = "disabled"
    elif not mcp_gateway.has_started():
        mcp_status = "starting"
    else:
        mcp_status = "degraded"
    return {
        "status": "ok",
        "version": __version__,
        "supabase_configured": settings.supabase_configured,
        "mcp_available": mcp_gateway.is_available(),
        "mcp_status": mcp_status,
        "mcp_failed_servers": sorted(mcp_gateway.startup_errors),
        "sandbox_available": sandbox_runner.is_available(),
        "sandbox_backend": sandbox_runner.backend_name,
        "approval_signing_key_configured": settings.tool_approval_signing_key_configured,
    }


@app.get("/api/stocks/{symbol}/history")
def stock_history(
    symbol: str,
    start: Annotated[date, Query()],
    end: Annotated[date | None, Query()] = None,
    period: Annotated[str, Query(pattern="^(daily|weekly|monthly)$")] = "daily",
    adjust: Annotated[str, Query(pattern="^(none|qfq|hfq)$")] = "qfq",
) -> dict[str, object]:
    client = get_client()
    try:
        valuations = client.get_stock_valuation(symbol)
        try:
            rows = client.get_stock_history(
                symbol,
                start=start,
                end=end or date.today(),
                period=period,
                adjust=adjust,
            )
        except EastmoneyError:
            rows = stock_bars_from_valuations(
                [item for item in valuations if start <= item.date <= (end or date.today())]
            )
        stock_name = next((item.name for item in reversed(valuations) if item.name), None)
    except (ValueError, EastmoneyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "symbol": symbol,
        "name": stock_name,
        "count": len(rows),
        "items": [item.__dict__ for item in rows],
    }


@app.get("/api/stocks/{symbol}/valuation")
def stock_valuation(symbol: str) -> dict[str, object]:
    client = get_client()
    try:
        rows = client.get_stock_valuation(symbol)
    except (ValueError, EastmoneyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stock_name = next((item.name for item in reversed(rows) if item.name), None)
    return {
        "symbol": symbol,
        "name": stock_name,
        "count": len(rows),
        "items": [item.__dict__ for item in rows],
    }


@app.get("/api/funds/{code}/nav")
def fund_nav(
    code: str,
    start: Annotated[date, Query()],
    end: Annotated[date | None, Query()] = None,
) -> dict[str, object]:
    client = get_client()
    try:
        try:
            rows = client.get_exchange_fund_price_nav(code, start=start, end=end or date.today())
        except EastmoneyError:
            rows = []
        data_source = "exchange_price_history" if rows else "fund_nav_history"
        if not rows:
            rows = client.get_fund_nav(code, start=start, end=end or date.today())
        fund_name = client.get_fund_name(code)
    except (ValueError, EastmoneyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "code": code,
        "name": fund_name,
        "data_source": data_source,
        "count": len(rows),
        "items": [item.__dict__ for item in rows],
    }


@app.get("/api/search", response_model=AssetSearchResponse)
def search_assets(
    keyword: Annotated[str, Query(min_length=1)],
    asset_type: Annotated[Literal["stock", "fund"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> AssetSearchResponse:
    client = get_client()
    try:
        tool_data = require_tool_data(
            build_default_executor(data_client=client, mcp_gateway=mcp_gateway).execute(
                SEARCH_ASSETS_TOOL,
                {"keyword": keyword, "asset_type": asset_type, "limit": limit},
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AssetSearchResponse.model_validate(tool_data)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(
    request: AnalyzeRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AnalyzeResponse:
    client = get_client()
    agent = MarketAnalysisAgent(client)
    repository = get_repository()
    executor = build_default_executor(
        data_client=client,
        analysis_agent=agent,
        audit_recorder=SupabaseToolAuditRecorder(repository, user),
        mcp_gateway=mcp_gateway,
    )
    try:
        tool_data = require_tool_data(
            executor.execute(
                ANALYZE_ASSET_TOOL,
                {
                    "asset_type": request.asset_type,
                    "code": request.code,
                    "start": request.start,
                    "end": request.end or date.today(),
                },
                context=ToolContext(user_id=user.id),
            )
        )
        result = tool_data["result"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        row = repository.save_analysis(
            user,
            request_params=jsonable_encoder(request.model_dump()),
            result=jsonable_encoder(result),
        )
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AnalyzeResponse(result=result, analysis_id=row.get("id"))


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> ChatResponse:
    client = get_client()
    repository = get_repository()
    context = None
    if request.context is not None:
        context = ChatAssetContext(
            asset_type=request.context.asset_type,
            code=request.context.code,
            name=request.context.name,
        )
    try:
        expire_stale_tool_approvals(repository, user)
        session = repository.ensure_chat_session(
            user,
            session_id=request.session_id,
            title=request.message,
        )
        session_id = UUID(str(session["id"]))
        analysis_agent = MarketAnalysisAgent(client)
        agent = ChatAgent(
            data_client=client,
            analysis_agent=analysis_agent,
            tool_executor=build_default_executor(
                data_client=client,
                analysis_agent=analysis_agent,
                audit_recorder=SupabaseToolAuditRecorder(repository, user),
                mcp_gateway=mcp_gateway,
                sandbox_runner=sandbox_runner,
                workspace_store=SupabaseWorkspaceStore(repository, user, session_id),
            ),
            tool_context=ToolContext(user_id=user.id, session_id=session_id),
        )
        result = agent.reply(
            message=request.message,
            context=context,
            start=request.start,
            end=request.end or date.today(),
        )
        analysis_id = save_chat_analysis(repository, user, request, result.get("analysis"))
        repository.update_chat_session(user, session_id, result.get("asset"))
        repository.save_chat_message(user, session_id, "user", request.message)
        repository.save_chat_message(
            user,
            session_id,
            "assistant",
            result["answer"],
            citations=result.get("citations"),
            analysis_run_id=analysis_id,
        )
    except (ValueError, EastmoneyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(**result, session_id=session_id)


@app.post("/api/chat/stream")
def chat_stream(
    request: ChatRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> StreamingResponse:
    client = get_client()
    repository = get_repository()
    context = None
    if request.context is not None:
        context = ChatAssetContext(
            asset_type=request.context.asset_type,
            code=request.context.code,
            name=request.context.name,
        )

    try:
        expire_stale_tool_approvals(repository, user)
        session = repository.ensure_chat_session(
            user,
            session_id=request.session_id,
            title=request.message,
        )
        session_id = UUID(str(session["id"]))
        analysis_agent = MarketAnalysisAgent(client)
        agent = ChatAgent(
            data_client=client,
            analysis_agent=analysis_agent,
            tool_executor=build_default_executor(
                data_client=client,
                analysis_agent=analysis_agent,
                audit_recorder=SupabaseToolAuditRecorder(repository, user),
                mcp_gateway=mcp_gateway,
                sandbox_runner=sandbox_runner,
                workspace_store=SupabaseWorkspaceStore(repository, user, session_id),
            ),
            tool_context=ToolContext(user_id=user.id, session_id=session_id),
        )
        repository.save_chat_message(user, session_id, "user", request.message)
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def event_stream():
        answer_parts: list[str] = []
        citations: list[str] = []
        analysis_id: str | None = None
        try:
            for event in agent.stream_reply(
                message=request.message,
                context=context,
                start=request.start,
                end=request.end or date.today(),
            ):
                if event.get("type") == "meta":
                    citations = event.get("citations") or []
                    analysis_id = save_chat_analysis(
                        repository,
                        user,
                        request,
                        event.get("analysis"),
                    )
                    repository.update_chat_session(user, session_id, event.get("asset"))
                    event["session_id"] = str(session_id)
                elif event.get("type") == "citations":
                    citations = event.get("citations") or citations
                elif event.get("type") == "approval_required":
                    citations = event.get("citations") or citations
                    event = save_tool_approval_event(
                        repository,
                        user,
                        session_id,
                        event,
                    )
                elif event.get("type") == "token":
                    answer_parts.append(str(event.get("delta") or ""))
                elif event.get("type") == "done":
                    repository.save_chat_message(
                        user,
                        session_id,
                        "assistant",
                        "".join(answer_parts),
                        citations=citations,
                        analysis_run_id=analysis_id,
                    )
                    event["session_id"] = str(session_id)
                yield to_sse_data(event)
        except (ValueError, EastmoneyError) as exc:
            payload = {"type": "error", "message": str(exc)}
            yield to_sse_data(payload)
        except Exception as exc:
            payload = {"type": "error", "message": f"Chat stream failed: {exc}"}
            yield to_sse_data(payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tool-approvals/{approval_id}/stream")
def resume_tool_approval(
    approval_id: UUID,
    request: ToolApprovalDecisionRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> StreamingResponse:
    repository = get_repository()
    now = datetime.now(UTC)
    try:
        approval = repository.get_tool_approval(user, approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Tool approval was not found")
        if not verify_tool_approval_signature(approval):
            raise HTTPException(status_code=409, detail="Tool approval signature is invalid")
        if approval.get("status") != "pending":
            raise HTTPException(status_code=409, detail="Tool approval is no longer pending")
        if _parse_timestamp(approval.get("expires_at")) <= now:
            repository.transition_tool_approval(
                user,
                approval_id,
                expected_status="pending",
                status="expired",
                expected_signature=str(approval["signature"]),
                resolved_at=now,
            )
            logger.info(
                "Tool approval expired approval_id=%s user_id=%s tool_name=%s",
                approval_id,
                user.id,
                approval.get("tool_name"),
            )
            raise HTTPException(status_code=409, detail="Tool approval has expired")

        decision_status = "approved" if request.decision == "approve" else "denied"
        approval = repository.transition_tool_approval(
            user,
            approval_id,
            expected_status="pending",
            status=decision_status,
            expected_signature=str(approval["signature"]),
            resolved_at=now,
        )
        if approval is None:
            raise HTTPException(status_code=409, detail="Tool approval was already resolved")
        if not verify_tool_approval_signature(approval):
            raise HTTPException(status_code=409, detail="Tool approval signature is invalid")
        logger.info(
            "Tool approval resolved approval_id=%s user_id=%s tool_name=%s status=%s",
            approval_id,
            user.id,
            approval.get("tool_name"),
            decision_status,
        )
        session_id = UUID(str(approval["session_id"]))
        client = get_client()
        analysis_agent = MarketAnalysisAgent(client)
        agent = ChatAgent(
            data_client=client,
            analysis_agent=analysis_agent,
            tool_executor=build_default_executor(
                data_client=client,
                analysis_agent=analysis_agent,
                audit_recorder=SupabaseToolAuditRecorder(repository, user),
                mcp_gateway=mcp_gateway,
                sandbox_runner=sandbox_runner,
                workspace_store=SupabaseWorkspaceStore(repository, user, session_id),
            ),
            tool_context=ToolContext(
                user_id=user.id,
                session_id=session_id,
                request_id=str(approval_id),
            ),
        )
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    approved = request.decision == "approve"
    grant = None
    if approved:
        grant = ToolApprovalGrant(
            approval_id=approval_id,
            tool_name=str(approval["tool_name"]),
            arguments_digest=str(approval["arguments_digest"]),
        )

    def event_stream():
        answer_parts: list[str] = []
        citations = list(approval.get("citations") or [])
        try:
            for event in agent.resume_stream(
                approval["checkpoint"],
                approved=approved,
                grant=grant,
            ):
                event_type = event.get("type")
                if event_type == "citations":
                    citations = list(dict.fromkeys([*citations, *(event.get("citations") or [])]))
                    event["citations"] = citations
                elif event_type == "approval_required":
                    if approved:
                        repository.transition_tool_approval(
                            user,
                            approval_id,
                            expected_status="approved",
                            status="executed",
                            expected_signature=str(approval["signature"]),
                        )
                    event = save_tool_approval_event(
                        repository,
                        user,
                        session_id,
                        event,
                    )
                elif event_type == "token":
                    answer_parts.append(str(event.get("delta") or ""))
                elif event_type == "done":
                    repository.save_chat_message(
                        user,
                        session_id,
                        "assistant",
                        "".join(answer_parts),
                        citations=citations,
                    )
                    if approved:
                        repository.transition_tool_approval(
                            user,
                            approval_id,
                            expected_status="approved",
                            status="executed",
                            expected_signature=str(approval["signature"]),
                        )
                    event["session_id"] = str(session_id)
                elif event_type == "error" and approved:
                    repository.transition_tool_approval(
                        user,
                        approval_id,
                        expected_status="approved",
                        status="failed",
                        expected_signature=str(approval["signature"]),
                    )
                yield to_sse_data(event)
        except Exception as exc:
            if approved:
                repository.transition_tool_approval(
                    user,
                    approval_id,
                    expected_status="approved",
                    status="failed",
                    expected_signature=str(approval["signature"]),
                )
            yield to_sse_data({"type": "error", "message": f"Tool resumption failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/history/analyses", response_model=AnalysisHistoryResponse)
def analysis_history(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> AnalysisHistoryResponse:
    try:
        items = get_repository().list_analyses(user, limit)
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AnalysisHistoryResponse(count=len(items), items=items)


@app.get("/api/history/chat-sessions")
def chat_session_history(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    try:
        items = get_repository().list_chat_sessions(user, limit)
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"count": len(items), "items": items}


@app.get("/api/history/chat-sessions/{session_id}/messages")
def chat_message_history(
    session_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict[str, Any]:
    try:
        items = get_repository().list_chat_messages(user, session_id)
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"count": len(items), "items": items}


def save_tool_approval_event(
    repository: SupabaseRepository,
    user: AuthenticatedUser,
    session_id: UUID,
    event: dict[str, Any],
) -> dict[str, Any]:
    pending = event.get("approval")
    checkpoint = event.get("checkpoint")
    if not isinstance(pending, dict) or not isinstance(checkpoint, dict):
        raise ValueError("Tool approval event is invalid")
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.tool_approval_ttl_seconds)
    approval_id = uuid4()
    signature_payload = {
        "id": str(approval_id),
        "user_id": str(user.id),
        "session_id": str(session_id),
        "tool_name": str(pending["tool_name"]),
        "tool_alias": str(pending["tool_alias"]),
        "tool_call_id": str(pending["tool_call_id"]),
        "risk_level": str(pending["risk"]),
        "execution_target": str(pending["execution_target"]),
        "reason": str(pending["reason"]),
        "input_summary": dict(pending.get("input_summary") or {}),
        "arguments_digest": str(pending["arguments_digest"]),
        "checkpoint": checkpoint,
        "citations": list(event.get("citations") or []),
        "expires_at": expires_at.isoformat(),
    }
    signature = _sign_tool_approval(signature_payload)
    row = repository.create_tool_approval(
        user,
        session_id,
        approval_id=approval_id,
        tool_name=str(pending["tool_name"]),
        tool_alias=str(pending["tool_alias"]),
        tool_call_id=str(pending["tool_call_id"]),
        risk_level=str(pending["risk"]),
        execution_target=str(pending["execution_target"]),
        reason=str(pending["reason"]),
        input_summary=dict(pending.get("input_summary") or {}),
        arguments_digest=str(pending["arguments_digest"]),
        checkpoint=checkpoint,
        citations=list(event.get("citations") or []),
        signature=signature,
        expires_at=expires_at,
    )
    logger.info(
        "Tool approval created approval_id=%s user_id=%s session_id=%s tool_name=%s",
        row["id"],
        user.id,
        session_id,
        row["tool_name"],
    )
    return {
        "type": "approval_required",
        "session_id": str(session_id),
        "citations": list(event.get("citations") or []),
        "approval": {
            "id": str(row["id"]),
            "tool_name": row["tool_name"],
            "risk_level": row["risk_level"],
            "execution_target": row["execution_target"],
            "reason": row["reason"],
            "input_summary": row.get("input_summary") or {},
            "status": row["status"],
            "expires_at": row["expires_at"],
        },
    }


def expire_stale_tool_approvals(
    repository: SupabaseRepository,
    user: AuthenticatedUser,
) -> None:
    try:
        expired = repository.expire_stale_tool_approvals(user, datetime.now(UTC))
    except SupabaseError:
        logger.warning(
            "Tool approval cleanup failed user_id=%s",
            user.id,
            exc_info=True,
        )
        return
    if expired:
        logger.info(
            "Tool approval cleanup completed user_id=%s expired_pending=true",
            user.id,
        )


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise HTTPException(status_code=409, detail="Tool approval expiration is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Tool approval expiration is invalid") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def verify_tool_approval_signature(row: dict[str, Any]) -> bool:
    try:
        payload = {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "session_id": str(row["session_id"]),
            "tool_name": str(row["tool_name"]),
            "tool_alias": str(row["tool_alias"]),
            "tool_call_id": str(row["tool_call_id"]),
            "risk_level": str(row["risk_level"]),
            "execution_target": str(row["execution_target"]),
            "reason": str(row["reason"]),
            "input_summary": dict(row.get("input_summary") or {}),
            "arguments_digest": str(row["arguments_digest"]),
            "checkpoint": dict(row["checkpoint"]),
            "citations": list(row.get("citations") or []),
            "expires_at": _parse_timestamp(row["expires_at"]).isoformat(),
        }
        expected = _sign_tool_approval(payload)
        supplied = str(row["signature"])
    except (HTTPException, KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, supplied)


def _sign_tool_approval(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        settings.tool_approval_signing_key.encode("utf-8"),
        encoded,
        hashlib.sha256,
    ).hexdigest()


def save_chat_analysis(
    repository: SupabaseRepository,
    user: AuthenticatedUser,
    request: ChatRequest,
    analysis: dict[str, Any] | None,
) -> str | None:
    if analysis is None:
        return None
    row = repository.save_analysis(
        user,
        request_params=jsonable_encoder(
            {
                "source": "chat",
                "message": request.message,
                "start": request.start,
                "end": request.end,
            }
        ),
        result=jsonable_encoder(analysis),
    )
    return str(row.get("id")) if row.get("id") else None
