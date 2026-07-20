from __future__ import annotations

import pytest

from market_lens.agent.llm_client import LLMError, OpenAICompatibleLLMClient


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
