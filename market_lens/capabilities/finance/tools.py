from __future__ import annotations

from pydantic import BaseModel

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.capabilities.finance.schemas import (
    AnalyzeAssetInput,
    AnalyzeAssetOutput,
    SearchAssetsInput,
    SearchAssetsOutput,
)
from market_lens.data.eastmoney import EastmoneyClient, EastmoneyError
from market_lens.tools.executor import ToolPublicError
from market_lens.tools.models import ExecutionTarget, ToolContext, ToolRisk, ToolSpec
from market_lens.tools.registry import ToolRegistry

SEARCH_ASSETS_TOOL = "finance.search_assets"
ANALYZE_ASSET_TOOL = "finance.analyze_asset"


class FinanceToolHandlers:
    def __init__(
        self,
        data_client: EastmoneyClient,
        analysis_agent: MarketAnalysisAgent,
    ) -> None:
        self.data_client = data_client
        self.analysis_agent = analysis_agent

    def search_assets(
        self,
        raw_input: BaseModel,
        context: ToolContext,
    ) -> SearchAssetsOutput:
        del context
        args = SearchAssetsInput.model_validate(raw_input)
        try:
            search_options = {"limit": args.limit}
            if args.asset_type is not None:
                search_options["asset_type"] = args.asset_type
            rows = self.data_client.search_assets(args.keyword, **search_options)
        except (ValueError, EastmoneyError) as exc:
            raise ToolPublicError("market_data_error", str(exc)) from exc
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
            if item.asset_type in {"stock", "fund"}
        ]
        return SearchAssetsOutput(keyword=args.keyword, count=len(items), items=items)

    def analyze_asset(
        self,
        raw_input: BaseModel,
        context: ToolContext,
    ) -> AnalyzeAssetOutput:
        del context
        args = AnalyzeAssetInput.model_validate(raw_input)
        try:
            result = self.analysis_agent.analyze(
                asset_type=args.asset_type,
                code=args.code,
                start=args.start,
                end=args.end,
            )
        except (ValueError, EastmoneyError) as exc:
            raise ToolPublicError("market_analysis_error", str(exc)) from exc
        return AnalyzeAssetOutput(result=result)


def register_finance_tools(
    registry: ToolRegistry,
    data_client: EastmoneyClient,
    analysis_agent: MarketAnalysisAgent,
) -> None:
    handlers = FinanceToolHandlers(data_client, analysis_agent)
    registry.register(
        ToolSpec(
            name=SEARCH_ASSETS_TOOL,
            capability="finance",
            description="Search stocks and funds by code or name.",
            input_model=SearchAssetsInput,
            output_model=SearchAssetsOutput,
            handler=handlers.search_assets,
            risk=ToolRisk.READ,
            execution_target=ExecutionTarget.TRUSTED_LOCAL,
            timeout_seconds=20,
            idempotent=True,
            requires_network=True,
        )
    )
    registry.register(
        ToolSpec(
            name=ANALYZE_ASSET_TOOL,
            capability="finance",
            description="Retrieve market data and calculate a stock or fund valuation.",
            input_model=AnalyzeAssetInput,
            output_model=AnalyzeAssetOutput,
            handler=handlers.analyze_asset,
            risk=ToolRisk.COMPUTE,
            execution_target=ExecutionTarget.TRUSTED_LOCAL,
            timeout_seconds=90,
            idempotent=True,
            requires_network=True,
        )
    )
