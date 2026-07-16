from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from market_lens import __version__
from market_lens.agent.chat_agent import ChatAgent, ChatAssetContext
from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.api.auth import get_current_user
from market_lens.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AssetSearchResponse,
    ChatRequest,
    ChatResponse,
)
from market_lens.capabilities.finance.tools import ANALYZE_ASSET_TOOL, SEARCH_ASSETS_TOOL
from market_lens.config import settings
from market_lens.data.eastmoney import EastmoneyClient, EastmoneyError, stock_bars_from_valuations
from market_lens.storage.supabase import (
    AuthenticatedUser,
    SupabaseError,
    SupabaseRepository,
)
from market_lens.storage.tool_audit import SupabaseToolAuditRecorder
from market_lens.tools.catalog import build_default_executor
from market_lens.tools.executor import require_tool_data
from market_lens.tools.models import ToolContext

app = FastAPI(
    title="Market Lens API",
    version=__version__,
    description="Agent service for market data retrieval and valuation analysis.",
)


def get_client() -> EastmoneyClient:
    return EastmoneyClient()


def get_repository() -> SupabaseRepository:
    return SupabaseRepository()


def to_sse_data(event: dict[str, Any]) -> str:
    payload = jsonable_encoder(event)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "version": __version__,
        "supabase_configured": settings.supabase_configured,
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
            build_default_executor(data_client=client).execute(
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


@app.get("/api/history/analyses")
def analysis_history(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    try:
        items = get_repository().list_analyses(user, limit)
    except SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"count": len(items), "items": items}


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
