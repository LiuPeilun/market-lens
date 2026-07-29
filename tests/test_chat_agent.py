from __future__ import annotations

from datetime import date

from market_lens.agent.chat_agent import ChatAgent, ChatAssetContext, extract_asset_keyword
from market_lens.agent.llm_client import LLMChatTurn, LLMToolCall
from market_lens.api.schemas import AnalysisResult
from market_lens.data.eastmoney import EastmoneyError
from market_lens.errors import DataUnavailableError
from market_lens.tools.executor import ToolExecutor
from market_lens.tools.models import ToolInput, ToolOutput, ToolSpec
from market_lens.tools.registry import ToolRegistry
from market_lens.types import AssetSearchResult


class FakeDataClient:
    def search_assets(self, keyword: str, limit: int = 5) -> list[AssetSearchResult]:
        del limit
        if keyword == "南方红利低波":
            return [
                AssetSearchResult(
                    asset_type="fund",
                    code="515450",
                    name="红利低波50ETF南方",
                    market="基金",
                    quote_id="1.515450",
                    source_type="Fund",
                    raw={},
                )
            ]
        return []


class FakeAnalysisAgent:
    def analyze(
        self,
        asset_type: str,
        code: str,
        start: date,
        end: date,
    ) -> dict[str, object]:
        del start, end
        return {
            "asset_type": asset_type,
            "code": code,
            "name": "红利低波50ETF南方" if code == "515450" else "贵州茅台",
            "as_of": "2026-07-03",
            "valuation": {
                "method": "index_price_percentile_proxy",
                "score": 72.34,
                "level_zh": "正常估值偏上",
                "confidence": 0.6,
            },
            "assessment": {
                "schema_version": "2",
                "model_version": "valuation-v2.2.0-fund-product-models",
                "profile": "etf",
                "analysis_as_of": "2026-07-03",
                "dimensions": {
                    "valuation": {
                        "score": 72.34,
                        "level_zh": "正常估值偏上",
                        "confidence": 0.6,
                    },
                    "quality": {
                        "score": 68.0,
                        "level_zh": "较高",
                        "confidence": 0.5,
                    },
                    "product": {
                        "score": 80.0,
                        "level_zh": "很高",
                        "confidence": 0.8,
                    },
                },
                "overall_confidence": 0.5,
                "attractiveness": None,
            },
            "performance": {
                "sample_size": 100,
                "total_return": 0.2,
                "annualized_return": 0.08,
                "max_drawdown": -0.15,
                "total_return_text": "20.00%",
                "annualized_return_text": "8.00%",
                "max_drawdown_text": "-15.00%",
            },
            "notes": [],
        }


class FailingAnalysisAgent:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def analyze(
        self,
        asset_type: str,
        code: str,
        start: date,
        end: date,
    ) -> dict[str, object]:
        del asset_type, code, start, end
        raise self.error


class FakeLLMClient:
    def complete_turn(self, messages, tools=None) -> LLMChatTurn:
        assert messages
        assert tools
        return LLMChatTurn(content="LLM 生成的自然语言回答", tool_calls=[])

    def complete(self, messages: list[dict[str, str]]) -> str:
        assert messages
        return "LLM 生成的自然语言回答"

    def stream_complete(self, messages: list[dict[str, str]]):
        assert messages
        yield "流式"
        yield "回答"


class ResearchInput(ToolInput):
    repoName: str
    question: str


class ResearchOutput(ToolOutput):
    answer: str


class FakeToolCallingLLM:
    def __init__(self) -> None:
        self.turn = 0

    def complete_turn(self, messages, tools=None) -> LLMChatTurn:
        assert messages
        assert tools
        self.turn += 1
        if self.turn == 1:
            return LLMChatTurn(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="mcp__deepwiki__ask_question",
                        arguments={
                            "repoName": "modelcontextprotocol/servers",
                            "question": "What is this repository?",
                        },
                    )
                ],
            )
        return LLMChatTurn(content="这是经过工具结果支撑的仓库说明。", tool_calls=[])

    def complete(self, messages) -> str:
        raise AssertionError("Tool orchestration should use complete_turn")


def test_extract_asset_keyword_from_question() -> None:
    assert extract_asset_keyword("帮我看看南方红利低波现在贵不贵") == "南方红利低波"


def test_chat_agent_resolves_asset_and_answers_valuation() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FakeAnalysisAgent(),
        use_llm=False,
    )

    result = agent.reply(
        message="帮我看看南方红利低波现在贵不贵",
        context=None,
        start=date(2018, 1, 1),
        end=date(2026, 7, 3),
    )

    assert result["intent"] == "explain_valuation"
    assert result["asset"]["code"] == "515450"
    assert result["analysis"]["valuation"]["score"] == 72.34
    assert "正常估值偏上" in result["answer"]
    assert "底层资产质量" in result["answer"]
    assert "基金产品质量" in result["answer"]
    assert "不合成为未经回测的吸引力评级" in result["answer"]
    assert any("价格位置代理" in citation for citation in result["citations"])


def test_chat_agent_uses_context_for_follow_up() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FakeAnalysisAgent(),
        use_llm=False,
    )

    result = agent.reply(
        message="最大回撤怎么样",
        context=ChatAssetContext(asset_type="fund", code="515450", name="红利低波50ETF南方"),
        start=date(2018, 1, 1),
        end=date(2026, 7, 3),
    )

    assert result["intent"] == "risk_summary"
    assert "最大回撤" in result["answer"]
    assert "-15.00%" in result["answer"]


def test_chat_agent_can_use_llm_answer() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FakeAnalysisAgent(),
        llm_client=FakeLLMClient(),
        use_llm=True,
    )

    result = agent.reply(
        message="南方红利低波贵不贵",
        context=None,
        start=date(2018, 1, 1),
        end=date(2026, 7, 3),
    )

    assert result["answer"] == "LLM 生成的自然语言回答"


def test_chat_agent_streams_llm_answer() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FakeAnalysisAgent(),
        llm_client=FakeLLMClient(),
        use_llm=True,
    )

    events = list(
        agent.stream_reply(
            message="南方红利低波贵不贵",
            context=None,
            start=date(2018, 1, 1),
            end=date(2026, 7, 3),
        )
    )

    progress = [event for event in events if event["type"] == "progress"]
    meta = next(event for event in events if event["type"] == "meta")
    tokens = [event for event in events if event["type"] == "token"]

    assert meta["asset"]["code"] == "515450"
    assert meta["analysis"]["assessment"]["overall_confidence"] == 0.5
    assert tokens == [
        {"type": "token", "delta": "流式"},
        {"type": "token", "delta": "回答"},
    ]
    assert [(event["id"], event["status"]) for event in progress] == [
        ("resolve_asset", "running"),
        ("search_asset", "running"),
        ("search_asset", "completed"),
        ("resolve_asset", "completed"),
        ("analyze_asset", "running"),
        ("analyze_asset", "completed"),
        ("planning:1", "running"),
        ("planning:1", "completed"),
        ("answer", "running"),
        ("answer", "completed"),
    ]
    assert events[-1] == {"type": "done"}


def test_chat_agent_uses_tools_for_general_repository_question() -> None:
    def research_handler(raw_input, context) -> ResearchOutput:
        del context
        value = ResearchInput.model_validate(raw_input)
        return ResearchOutput(answer=f"Documentation for {value.repoName}")

    executor = ToolExecutor(
        ToolRegistry(
            [
                ToolSpec(
                    name="mcp.deepwiki.ask_question",
                    capability="research",
                    description="Ask a repository question",
                    input_model=ResearchInput,
                    output_model=ResearchOutput,
                    handler=research_handler,
                )
            ]
        )
    )
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FakeAnalysisAgent(),
        llm_client=FakeToolCallingLLM(),
        tool_executor=executor,
        use_llm=True,
    )

    result = agent.reply(
        message="modelcontextprotocol/servers 仓库是做什么的？",
        context=None,
        start=date(2026, 1, 1),
        end=date(2026, 7, 20),
    )

    assert result["intent"] == "general_query"
    assert result["answer"] == "这是经过工具结果支撑的仓库说明。"
    assert result["citations"] == ["工具调用：mcp.deepwiki.ask_question（success）"]


def test_chat_returns_structured_unavailable_when_finance_upstream_fails() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FailingAnalysisAgent(
            EastmoneyError("secret upstream host disconnected")
        ),
        use_llm=False,
    )

    result = agent.reply(
        message="分析 600519",
        context=None,
        start=date(2024, 1, 1),
        end=date(2026, 7, 20),
    )

    analysis = result["analysis"]
    AnalysisResult.model_validate(analysis)
    diagnostic = analysis["assessment"]["data_quality"]["chat_tool_failure"]
    assert analysis["assessment"]["status"] == "unavailable"
    assert analysis["valuation"]["score"] is None
    assert diagnostic == {
        "tool_name": "finance.analyze_asset",
        "error_code": "market_data_upstream_unavailable",
        "category": "upstream_unavailable",
        "retryable": True,
    }
    assert "暂时无法完成估值" in result["answer"]
    assert "稍后重新发起分析" in result["answer"]
    assert "secret upstream" not in str(result)
    assert result["citations"] == [
        "分析工具状态：finance.analyze_asset"
        "（market_data_upstream_unavailable，可重试）。"
    ]


def test_chat_unavailable_data_failure_is_not_marked_retryable() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FailingAnalysisAgent(
            DataUnavailableError(
                "verified_data_unavailable",
                "No verified data for this asset",
            )
        ),
        use_llm=False,
    )

    result = agent.reply(
        message="分析 600519",
        context=None,
        start=date(2024, 1, 1),
        end=date(2026, 7, 20),
    )

    diagnostic = result["analysis"]["assessment"]["data_quality"][
        "chat_tool_failure"
    ]
    assert diagnostic["category"] == "data_unavailable"
    assert diagnostic["retryable"] is False
    assert "当前不应自动重试" in result["answer"]


def test_chat_internal_tool_failure_hides_exception_details() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FailingAnalysisAgent(
            RuntimeError("secret internal implementation detail")
        ),
        use_llm=False,
    )

    result = agent.reply(
        message="分析 600519",
        context=None,
        start=date(2024, 1, 1),
        end=date(2026, 7, 20),
    )

    diagnostic = result["analysis"]["assessment"]["data_quality"][
        "chat_tool_failure"
    ]
    assert diagnostic["error_code"] == "tool_execution_failed"
    assert diagnostic["category"] == "internal_error"
    assert diagnostic["retryable"] is False
    assert "secret internal" not in str(result)


def test_chat_stream_explains_finance_failure_instead_of_emitting_error() -> None:
    agent = ChatAgent(
        data_client=FakeDataClient(),
        analysis_agent=FailingAnalysisAgent(EastmoneyError("upstream offline")),
        use_llm=False,
    )

    events = list(
        agent.stream_reply(
            message="分析 600519",
            context=None,
            start=date(2024, 1, 1),
            end=date(2026, 7, 20),
        )
    )

    progress = [event for event in events if event["type"] == "progress"]
    meta = next(event for event in events if event["type"] == "meta")
    token = next(event for event in events if event["type"] == "token")
    assert not any(event["type"] == "error" for event in events)
    assert meta["analysis"]["assessment"]["status"] == "unavailable"
    assert "暂时无法完成估值" in token["delta"]
    assert ("analyze_asset", "failed") in [
        (event["id"], event["status"]) for event in progress
    ]
    assert events[-1] == {"type": "done"}
