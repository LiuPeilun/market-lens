from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from market_lens.agent.llm_client import (
    LLMError,
    OpenAICompatibleLLMClient,
    build_general_llm_messages,
    build_llm_messages,
)
from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.agent.tool_orchestrator import ToolOrchestrator, ToolTrace
from market_lens.capabilities.finance.tools import ANALYZE_ASSET_TOOL, SEARCH_ASSETS_TOOL
from market_lens.config import settings
from market_lens.data.eastmoney import EastmoneyClient, is_a_share_symbol
from market_lens.tools.catalog import build_default_executor
from market_lens.tools.executor import ToolExecutor, require_tool_data
from market_lens.tools.models import ToolContext
from market_lens.types import AssetType
from market_lens.valuation.metrics import format_pct

ChatIntent = Literal[
    "analyze_asset",
    "explain_valuation",
    "performance_summary",
    "risk_summary",
    "data_source",
    "clarify_asset",
    "need_asset",
    "general_query",
]


@dataclass(frozen=True)
class ChatAssetContext:
    asset_type: AssetType
    code: str
    name: str | None = None


@dataclass
class PreparedChatReply:
    answer: str
    intent: ChatIntent
    asset: dict[str, Any] | None
    analysis: dict[str, Any] | None
    candidates: list[dict[str, Any]]
    citations: list[str]
    llm_messages: list[dict[str, Any]] | None = None


class ChatAgent:
    def __init__(
        self,
        data_client: EastmoneyClient | None = None,
        analysis_agent: MarketAnalysisAgent | None = None,
        llm_client: OpenAICompatibleLLMClient | None = None,
        use_llm: bool | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_context: ToolContext | None = None,
    ) -> None:
        self.data_client = data_client or EastmoneyClient()
        self.analysis_agent = analysis_agent or MarketAnalysisAgent(self.data_client)
        self.llm_client = llm_client or OpenAICompatibleLLMClient()
        self.use_llm = settings.llm_enabled if use_llm is None else use_llm
        self.tool_executor = tool_executor or build_default_executor(
            data_client=self.data_client,
            analysis_agent=self.analysis_agent,
        )
        self.tool_context = tool_context or ToolContext()

    def reply(
        self,
        message: str,
        context: ChatAssetContext | None,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        prepared = self.prepare_reply(message, context, start, end)
        if prepared.llm_messages is None:
            return prepared_to_response(prepared)
        answer = self._generate_answer(
            template_answer=prepared.answer,
            citations=prepared.citations,
            llm_messages=prepared.llm_messages,
        )
        return {
            "answer": answer,
            "intent": prepared.intent,
            "asset": prepared.asset,
            "analysis": prepared.analysis,
            "candidates": prepared.candidates,
            "citations": prepared.citations,
        }

    def prepare_reply(
        self,
        message: str,
        context: ChatAssetContext | None,
        start: date,
        end: date,
    ) -> PreparedChatReply:
        normalized_message = message.strip()
        intent = classify_intent(normalized_message)
        asset = self._resolve_asset(normalized_message, context)
        if asset is None:
            if self.use_llm:
                return PreparedChatReply(
                    answer="当前无法完成通用研究回答，请稍后重试。",
                    intent="general_query",
                    asset=None,
                    analysis=None,
                    candidates=[],
                    citations=[],
                    llm_messages=build_general_llm_messages(
                        normalized_message,
                        start=start.isoformat(),
                        end=end.isoformat(),
                    ),
                )
            return PreparedChatReply(
                answer="请告诉我要分析的基金或股票名称/代码，例如“南方红利低波”或“600519”。",
                intent="need_asset",
                asset=None,
                analysis=None,
                candidates=[],
                citations=[],
            )

        tool_data = require_tool_data(
            self.tool_executor.execute(
                ANALYZE_ASSET_TOOL,
                {
                    "asset_type": asset.asset_type,
                    "code": asset.code,
                    "start": start,
                    "end": end,
                },
                context=self.tool_context,
            )
        )
        analysis = tool_data["result"]
        asset_payload = {
            "asset_type": analysis.get("asset_type", asset.asset_type),
            "code": analysis.get("code", asset.code),
            "name": analysis.get("name") or asset.name,
        }
        template_answer = build_answer(intent, normalized_message, analysis)
        citations = build_citations(analysis)
        return PreparedChatReply(
            answer=template_answer,
            intent=intent,
            asset=asset_payload,
            analysis=analysis,
            candidates=[],
            citations=citations,
            llm_messages=build_llm_messages(
                user_message=normalized_message,
                intent=intent,
                template_answer=template_answer,
                analysis=analysis,
                citations=citations,
            ),
        )

    def stream_reply(
        self,
        message: str,
        context: ChatAssetContext | None,
        start: date,
        end: date,
    ) -> Iterator[dict[str, Any]]:
        prepared = self.prepare_reply(message, context, start, end)
        yield {
            "type": "meta",
            "intent": prepared.intent,
            "asset": prepared.asset,
            "analysis": prepared.analysis,
            "candidates": prepared.candidates,
            "citations": prepared.citations,
        }
        if prepared.llm_messages is None or not self.use_llm:
            yield {"type": "token", "delta": prepared.answer}
            yield {"type": "done"}
            return
        try:
            orchestration = ToolOrchestrator(
                self.llm_client,
                self.tool_executor,
                self.tool_context,
            ).prepare_stream(prepared.llm_messages)
            prepared.citations.extend(_tool_citations(orchestration.traces))
            emitted = False
            for delta in self.llm_client.stream_complete(orchestration.messages):
                emitted = True
                yield {"type": "token", "delta": delta}
            if not emitted:
                yield {"type": "token", "delta": prepared.answer}
        except LLMError:
            prepared.citations.append("LLM 流式生成失败，已回退到规则模板回答。")
            yield {"type": "token", "delta": prepared.answer}
        yield {"type": "done"}

    def _generate_answer(
        self,
        template_answer: str,
        citations: list[str],
        llm_messages: list[dict[str, Any]],
    ) -> str:
        if not self.use_llm:
            return template_answer
        try:
            result = ToolOrchestrator(
                self.llm_client,
                self.tool_executor,
                self.tool_context,
            ).run(llm_messages)
            citations.extend(_tool_citations(result.traces))
            return result.answer
        except LLMError:
            citations.append("LLM 生成失败，已回退到规则模板回答。")
            return template_answer

    def _resolve_asset(
        self,
        message: str,
        context: ChatAssetContext | None,
    ) -> ChatAssetContext | None:
        code = extract_code(message)
        if code:
            return ChatAssetContext(asset_type=infer_asset_type(code), code=code)

        if is_repository_research_query(message):
            return context

        keyword = extract_asset_keyword(message)
        if keyword:
            tool_data = require_tool_data(
                self.tool_executor.execute(
                    SEARCH_ASSETS_TOOL,
                    {"keyword": keyword, "limit": 5},
                    context=self.tool_context,
                )
            )
            candidates = tool_data["items"]
            if candidates:
                candidate = candidates[0]
                return ChatAssetContext(
                    asset_type=candidate["asset_type"],
                    code=candidate["code"],
                    name=candidate["name"],
                )

        return context


def classify_intent(message: str) -> ChatIntent:
    if contains_any(message, ("数据", "来源", "方法", "怎么算", "怎么计算", "为什么")):
        return "data_source"
    if contains_any(message, ("回撤", "风险", "波动", "亏损")):
        return "risk_summary"
    if contains_any(message, ("收益", "年化", "表现", "涨幅", "跌幅")):
        return "performance_summary"
    if contains_any(message, ("估值", "贵", "便宜", "高估", "低估", "分位")):
        return "explain_valuation"
    return "analyze_asset"


def prepared_to_response(prepared: PreparedChatReply) -> dict[str, Any]:
    return {
        "answer": prepared.answer,
        "intent": prepared.intent,
        "asset": prepared.asset,
        "analysis": prepared.analysis,
        "candidates": prepared.candidates,
        "citations": prepared.citations,
    }


def contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in value for keyword in keywords)


def is_repository_research_query(message: str) -> bool:
    lowered = message.lower()
    return bool(
        re.search(r"\b[a-z0-9_.-]+/[a-z0-9_.-]+\b", lowered)
        or contains_any(
            lowered,
            ("github", "deepwiki", "仓库", "代码库", "repository", "repo", "sdk", "框架文档"),
        )
    )


def extract_code(message: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message)
    return match.group(1) if match else None


def infer_asset_type(code: str) -> AssetType:
    try:
        return "stock" if is_a_share_symbol(code) else "fund"
    except ValueError:
        return "fund"


def extract_asset_keyword(message: str) -> str | None:
    cleaned = re.sub(r"[，。！？、,.!?;；:：()（）【】\[\]\"'“”]", " ", message)
    cleaned = re.sub(
        r"(帮我|请|看看|看下|分析|一下|现在|目前|当前|是否|是不是|"
        r"怎么样|如何|贵不贵|便宜吗|高估吗|低估吗|估值|收益|回撤|风险|"
        r"基金|股票|可以|吗|呢|的|这只|这个)",
        " ",
        cleaned,
    )
    tokens = [token.strip() for token in re.split(r"\s+", cleaned) if token.strip()]
    tokens = [token for token in tokens if not re.fullmatch(r"[A-Za-z0-9]+", token)]
    if not tokens:
        return None
    return max(tokens, key=len)


def build_answer(intent: ChatIntent, message: str, analysis: dict[str, Any]) -> str:
    del message
    asset_label = format_asset_label(analysis)
    valuation = analysis.get("valuation") or {}
    performance = analysis.get("performance") or {}

    if intent == "data_source":
        method = valuation.get("method") or "unknown"
        data_source = analysis.get("data_source") or "market_data"
        return (
            f"{asset_label} 的分析数据源是 {data_source}，估值方法是 {method}。"
            f"当前估值结论为 {valuation.get('level_zh') or '未知'}，"
            f"置信度为 {format_confidence(valuation.get('confidence'))}。"
        )

    if intent == "risk_summary":
        drawdown_text = format_pct(performance.get("max_drawdown")) or "暂无"
        return (
            f"{asset_label} 的区间最大回撤为 {drawdown_text}。"
            f"样本数量为 {performance.get('sample_size', 0)}，"
            "回撤只描述历史波动，不代表未来风险上限。"
        )

    if intent == "performance_summary":
        return (
            f"{asset_label} 区间总收益为 {performance.get('total_return_text') or '暂无'}，"
            f"年化收益为 {performance.get('annualized_return_text') or '暂无'}，"
            f"最大回撤为 {performance.get('max_drawdown_text') or '暂无'}。"
        )

    if intent == "explain_valuation":
        return (
            f"{asset_label} 当前综合估值为 {valuation.get('level_zh') or '未知'}，"
            f"估值分为 {format_score(valuation.get('score'))}，"
            f"置信度为 {format_confidence(valuation.get('confidence'))}。"
            f"使用的方法是 {valuation.get('method') or 'unknown'}。"
        )

    return (
        f"{asset_label} 已完成分析。综合估值为 {valuation.get('level_zh') or '未知'}，"
        f"估值分为 {format_score(valuation.get('score'))}；"
        f"区间总收益为 {performance.get('total_return_text') or '暂无'}，"
        f"最大回撤为 {performance.get('max_drawdown_text') or '暂无'}。"
    )


def build_citations(analysis: dict[str, Any]) -> list[str]:
    valuation = analysis.get("valuation") or {}
    citations = ["收益和回撤来自历史行情/净值数据。"]
    method = valuation.get("method")
    if method == "index_price_percentile_proxy":
        citations.append("ETF 估值使用跟踪指数价格历史分位作为代理。")
    elif method == "historical_percentile_multi_factor":
        citations.append("股票估值使用 PE/PB/PS/PCF 等历史分位综合评分。")
    elif method:
        citations.append(f"估值方法：{method}。")
    return citations


def format_asset_label(analysis: dict[str, Any]) -> str:
    name = analysis.get("name")
    code = analysis.get("code")
    if name and code:
        return f"{name}（{code}）"
    return str(code or name or "该资产")


def format_score(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value:.1f}"
    return "暂无"


def format_confidence(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value * 100:.0f}%"
    return "暂无"


def _tool_citations(traces: list[ToolTrace]) -> list[str]:
    return [
        f"工具调用：{trace.tool_name}（{trace.status}）"
        for trace in traces
        if trace.tool_name
    ]
