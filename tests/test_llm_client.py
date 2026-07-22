from __future__ import annotations

import pytest

from market_lens.agent.llm_client import (
    LLMError,
    OpenAICompatibleLLMClient,
    compact_analysis_for_llm,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeStreamingResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"content":"STREAM"}}]}'
        yield b'data: {"choices":[{"delta":{"content":"_OK"}}]}'
        yield b"data: [DONE]"


def test_complete_turn_parses_openai_tool_calls(monkeypatch) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "research__lookup",
                                "arguments": '{"query":"MCP"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse(payload))

    turn = OpenAICompatibleLLMClient(base_url="https://llm.example/v1").complete_turn(
        [{"role": "user", "content": "Explain MCP"}],
        [
            {
                "type": "function",
                "function": {
                    "name": "research__lookup",
                    "description": "lookup",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert turn.content is None
    assert turn.tool_calls[0].id == "call-1"
    assert turn.tool_calls[0].arguments == {"query": "MCP"}


def test_complete_turn_rejects_invalid_tool_arguments(monkeypatch) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "research__lookup",
                                "arguments": "not-json",
                            },
                        }
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse(payload))

    with pytest.raises(LLMError, match="not valid JSON"):
        OpenAICompatibleLLMClient(base_url="https://llm.example/v1").complete_turn(
            [{"role": "user", "content": "Explain MCP"}],
            [{"type": "function", "function": {"name": "research__lookup"}}],
        )


def test_stream_complete_parses_sse_bytes(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeStreamingResponse()

    monkeypatch.setattr("requests.post", fake_post)

    chunks = list(
        OpenAICompatibleLLMClient(base_url="https://llm.example/v1").stream_complete(
            [{"role": "user", "content": "Stream a response"}]
        )
    )

    assert chunks == ["STREAM", "_OK"]
    assert captured["stream"] is True


def test_compact_analysis_keeps_factor_diagnostics_without_history() -> None:
    compact = compact_analysis_for_llm(
        {
            "asset_type": "stock",
            "code": "600519",
            "valuation": {
                "factor_data": {
                    "model_scope": "general_non_financial",
                    "diagnostic": {"status": "available"},
                    "latest": {"industry_specific": {"roic_pct": 31.42}},
                    "history": [{"report_date": "2025-12-31"}],
                    "scoring_eligible": False,
                },
                "product_data": {"diagnostic": {"status": "unavailable"}},
            },
            "assessment": {
                "schema_version": "2",
                "model_version": "valuation-v2.2.0-fund-product-models",
                "dimensions": {
                    "valuation": {
                        "score": 42.0,
                        "factors": [
                            {
                                "key": "pe_ttm_percentile",
                                "source_as_of": "2026-07-20",
                                "source": "eastmoney",
                            }
                        ],
                    },
                    "quality": {"score": 72.0, "factors": []},
                    "product": None,
                },
                "overall_confidence": 0.55,
                "attractiveness": None,
                "data_quality": {
                    "sources": [{"key": "stock_valuation", "status": "available"}],
                    "warnings": [],
                },
            },
        }
    )

    factor_data = compact["valuation"]["factor_data"]
    assert factor_data["model_scope"] == "general_non_financial"
    assert factor_data["latest"]["industry_specific"]["roic_pct"] == 31.42
    assert "history" not in factor_data
    assert compact["valuation"]["product_data"]["diagnostic"]["status"] == "unavailable"
    assert compact["assessment"]["dimensions"]["quality"]["score"] == 72.0
    assert compact["assessment"]["dimensions"]["valuation"]["factors"][0][
        "source_as_of"
    ] == "2026-07-20"
    assert compact["assessment"]["data_quality"]["sources"][0]["status"] == "available"
    assert compact["assessment"]["attractiveness"] is None


def test_compact_analysis_keeps_research_diagnostics_and_trims_items() -> None:
    compact = compact_analysis_for_llm(
        {
            "asset_type": "stock",
            "code": "600000",
            "valuation": {},
            "research": {
                "route": {
                    "main_model": "technology_rd",
                    "warnings": ["one", "two", "three", "four"],
                    "scoring_eligible": False,
                },
                "datasets": {
                    "income_statement": {
                        "status": "partial",
                        "source": "eastmoney_f10_detailed_financial_statement",
                        "source_as_of": "2025-12-31",
                        "available_at": "2026-03-20",
                        "unit": "CNY unless field suffix is _pct",
                        "coverage": 0.75,
                        "limitations": ["candidate only"],
                        "error": None,
                        "items": [
                            {"report_date": f"202{year}-12-31", "raw": {"x": 1}}
                            for year in range(1, 6)
                        ],
                        "scoring_eligible": False,
                    }
                },
                "scoring_eligible": False,
            },
        }
    )

    research = compact["research"]
    assert research["route"]["warnings"] == ["one", "two", "three", "four"]
    dataset = research["datasets"]["income_statement"]
    assert [item["report_date"] for item in dataset["items"]] == [
        "2023-12-31",
        "2024-12-31",
        "2025-12-31",
    ]
    assert all("raw" not in item for item in dataset["items"])
    assert dataset["scoring_eligible"] is False
