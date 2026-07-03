from __future__ import annotations

from datetime import date

from market_lens.agent.chat_agent import ChatAgent, ChatAssetContext, extract_asset_keyword
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


class FakeLLMClient:
    def complete(self, messages: list[dict[str, str]]) -> str:
        assert messages
        return "LLM 生成的自然语言回答"


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
