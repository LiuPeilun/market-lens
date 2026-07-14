from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from market_lens.config import settings


class LLMError(RuntimeError):
    pass


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.timeout = timeout or settings.llm_timeout

    def complete(self, messages: list[dict[str, str]]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM response did not include choices")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM response did not include message content")
        return content.strip()

    def stream_complete(self, messages: list[dict[str, str]]) -> Iterator[str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line.removeprefix("data:").strip()
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM stream request failed: {exc}") from exc


def compact_analysis_for_llm(analysis: dict[str, Any]) -> dict[str, Any]:
    valuation = analysis.get("valuation") or {}
    performance = analysis.get("performance") or {}
    return {
        "asset_type": analysis.get("asset_type"),
        "code": analysis.get("code"),
        "name": analysis.get("name"),
        "as_of": analysis.get("as_of"),
        "data_source": analysis.get("data_source"),
        "valuation": {
            "method": valuation.get("method"),
            "profile_name": valuation.get("profile_name"),
            "score": valuation.get("score"),
            "level_zh": valuation.get("level_zh"),
            "confidence": valuation.get("confidence"),
            "confidence_label": valuation.get("confidence_label"),
            "industry": valuation.get("industry"),
            "fundamentals": valuation.get("fundamentals"),
            "peer_comparison": valuation.get("peer_comparison"),
            "dividend": valuation.get("dividend"),
            "index": valuation.get("index"),
            "portfolio": valuation.get("portfolio"),
            "holdings": valuation.get("holdings"),
            "missing_factors": valuation.get("missing_factors"),
            "required_future_data": valuation.get("required_future_data"),
        },
        "performance": {
            "sample_size": performance.get("sample_size"),
            "total_return_text": performance.get("total_return_text"),
            "annualized_return_text": performance.get("annualized_return_text"),
            "max_drawdown_text": performance.get("max_drawdown_text"),
        },
        "notes": analysis.get("notes", []),
    }


def build_llm_messages(
    user_message: str,
    intent: str,
    template_answer: str,
    analysis: dict[str, Any],
    citations: list[str],
) -> list[dict[str, str]]:
    compact = compact_analysis_for_llm(analysis)
    return [
        {
            "role": "system",
            "content": (
                "你是 Market Lens 的投研问答助手。"
                "只能基于提供的结构化分析数据回答，不能编造数据、不能承诺收益、"
                "不能给出直接买入/卖出指令。"
                "不能补全、改写或扩展资产名称，资产名称必须照抄 analysis.name。"
                "不能引用输入数据中没有的 PE、PB、股息率、指数全称、基金全称或持仓。"
                "如果数据不足，要明确说明缺口。"
                "回答使用中文，简洁但要解释关键依据，不要使用 Markdown 加粗符号。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_question": user_message,
                    "intent": intent,
                    "deterministic_answer": template_answer,
                    "analysis": compact,
                    "citations": citations,
                },
                ensure_ascii=False,
            ),
        },
    ]
