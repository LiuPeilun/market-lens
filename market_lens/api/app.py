from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query

from market_lens import __version__
from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.api.schemas import AnalyzeRequest, AnalyzeResponse, AssetSearchResponse
from market_lens.data.eastmoney import EastmoneyClient, EastmoneyError

app = FastAPI(
    title="Market Lens API",
    version=__version__,
    description="Agent service for market data retrieval and valuation analysis.",
)


def get_client() -> EastmoneyClient:
    return EastmoneyClient()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


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
        rows = client.get_stock_history(
            symbol,
            start=start,
            end=end or date.today(),
            period=period,
            adjust=adjust,
        )
        valuations = client.get_stock_valuation(symbol)
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
        rows = client.get_fund_nav(code, start=start, end=end or date.today())
        fund_name = client.get_fund_name(code)
    except (ValueError, EastmoneyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "code": code,
        "name": fund_name,
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
        rows = client.search_assets(keyword, asset_type=asset_type, limit=limit)
    except EastmoneyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    items = [
        {
            "asset_type": item.asset_type,
            "code": item.code,
            "name": item.name,
            "market": item.market,
            "quote_id": item.quote_id,
            "source_type": item.source_type,
        }
        for item in rows
    ]
    return AssetSearchResponse(keyword=keyword, count=len(items), items=items)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    agent = MarketAnalysisAgent()
    try:
        result = agent.analyze(
            asset_type=request.asset_type,
            code=request.code,
            start=request.start,
            end=request.end or date.today(),
        )
    except (ValueError, EastmoneyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalyzeResponse(result=result)
