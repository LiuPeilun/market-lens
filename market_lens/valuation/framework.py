from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from market_lens.types import FundNavPoint, StockBar, StockValuationPoint
from market_lens.valuation.metrics import percentile_rank

ValuationLevel = Literal[
    "extremely_overvalued",
    "overvalued",
    "slightly_overvalued",
    "fair",
    "slightly_undervalued",
    "undervalued",
    "extremely_undervalued",
    "unknown",
]


LEVEL_LABELS_ZH: dict[ValuationLevel, str] = {
    "extremely_overvalued": "极度高估",
    "overvalued": "高估",
    "slightly_overvalued": "正常估值偏上",
    "fair": "正常估值",
    "slightly_undervalued": "正常估值偏下",
    "undervalued": "低估",
    "extremely_undervalued": "极度低估",
    "unknown": "未知",
}


@dataclass(frozen=True)
class FactorSpec:
    key: str
    name: str
    weight: float
    lower_is_cheaper: bool = True


@dataclass(frozen=True)
class ValuationProfile:
    key: str
    name: str
    method: str
    factors: tuple[FactorSpec, ...]
    required_future_data: tuple[str, ...] = ()


GENERIC_STOCK_PROFILE = ValuationProfile(
    key="generic_stock",
    name="普通股票",
    method="historical_percentile_multi_factor",
    factors=(
        FactorSpec("pe_ttm", "PE TTM", 0.40),
        FactorSpec("pb", "PB", 0.30),
        FactorSpec("ps_ttm", "PS TTM", 0.15),
        FactorSpec("pcf_ocf_ttm", "经营现金流市值比", 0.15),
    ),
    required_future_data=("行业分类", "同行业相对分位", "ROE/利润增速", "股息率"),
)

DIVIDEND_LOW_VOL_FUND_PROFILE = ValuationProfile(
    key="dividend_low_volatility_fund",
    name="红利低波基金",
    method="fund_holdings_or_index_weighted_valuation_pending",
    factors=(
        FactorSpec("dividend_yield", "股息率", 0.40, lower_is_cheaper=False),
        FactorSpec("pb", "PB", 0.25),
        FactorSpec("pe_ttm", "PE TTM", 0.20),
        FactorSpec("volatility", "波动率", 0.15),
    ),
    required_future_data=("基金持仓", "持仓权重", "成分股股息率", "成分股估值", "波动率"),
)

GENERIC_FUND_PROFILE = ValuationProfile(
    key="generic_fund",
    name="基金",
    method="fund_holdings_or_index_weighted_valuation_pending",
    factors=(
        FactorSpec("weighted_pe_ttm", "持仓加权 PE TTM", 0.35),
        FactorSpec("weighted_pb", "持仓加权 PB", 0.30),
        FactorSpec("style_relative_percentile", "同类风格分位", 0.20),
        FactorSpec("premium_discount", "溢价率/折价率", 0.15),
    ),
    required_future_data=("基金持仓", "跟踪指数", "持仓权重", "同类基金分类"),
)

INDEX_ETF_PROFILE = ValuationProfile(
    key="index_etf",
    name="指数/ETF",
    method="index_price_percentile_proxy",
    factors=(
        FactorSpec("index_price_percentile", "跟踪指数价格历史分位", 1.0),
    ),
    required_future_data=("指数 PE/PB 历史分位", "指数股息率", "指数成分股权重"),
)


def valuation_level(score: float | None) -> ValuationLevel:
    if score is None:
        return "unknown"
    if score >= 90:
        return "extremely_overvalued"
    if score >= 75:
        return "overvalued"
    if score >= 60:
        return "slightly_overvalued"
    if score >= 45:
        return "fair"
    if score >= 30:
        return "slightly_undervalued"
    if score >= 15:
        return "undervalued"
    return "extremely_undervalued"


def confidence_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.45:
        return "medium"
    if confidence > 0:
        return "low"
    return "none"


def analyze_stock_valuation(
    valuations: list[StockValuationPoint],
    profile: ValuationProfile = GENERIC_STOCK_PROFILE,
) -> dict[str, Any]:
    latest = valuations[-1] if valuations else None
    factor_results: list[dict[str, Any]] = []
    missing_factors: list[str] = []

    for factor in profile.factors:
        current = getattr(latest, factor.key, None) if latest else None
        percentile = percentile_rank(
            [getattr(item, factor.key, None) for item in valuations],
            current,
        )
        if percentile is None:
            missing_factors.append(factor.key)
            continue
        score = percentile * 100 if factor.lower_is_cheaper else (1 - percentile) * 100
        factor_results.append(
            {
                "key": factor.key,
                "name": factor.name,
                "weight": factor.weight,
                "value": current,
                "percentile": percentile,
                "score": score,
                "level": valuation_level(score),
                "level_zh": LEVEL_LABELS_ZH[valuation_level(score)],
            }
        )

    available_weight = sum(item["weight"] for item in factor_results)
    score = None
    if available_weight > 0:
        score = sum(item["score"] * item["weight"] for item in factor_results) / available_weight

    coverage = available_weight / sum(factor.weight for factor in profile.factors)
    history_confidence = min(len(valuations) / 1200, 1.0)
    confidence = round(coverage * (0.45 + 0.55 * history_confidence), 2)
    level = valuation_level(score)

    return {
        "method": profile.method,
        "profile": profile.key,
        "profile_name": profile.name,
        "score": round(score, 2) if score is not None else None,
        "level": level,
        "level_zh": LEVEL_LABELS_ZH[level],
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "factor_coverage": round(coverage, 2),
        "factors": factor_results,
        "missing_factors": missing_factors,
        "required_future_data": list(profile.required_future_data),
    }


def infer_fund_profile(name: str | None) -> ValuationProfile:
    value = name or ""
    if "红利" in value and ("低波" in value or "低波动" in value):
        return DIVIDEND_LOW_VOL_FUND_PROFILE
    return GENERIC_FUND_PROFILE


def analyze_fund_valuation(
    nav_points: list[FundNavPoint],
    name: str | None = None,
) -> dict[str, Any]:
    profile = infer_fund_profile(name)
    level = valuation_level(None)
    return {
        "method": profile.method,
        "profile": profile.key,
        "profile_name": profile.name,
        "score": None,
        "level": level,
        "level_zh": LEVEL_LABELS_ZH[level],
        "confidence": 0.0,
        "confidence_label": confidence_label(0.0),
        "factor_coverage": 0.0,
        "factors": [],
        "missing_factors": [factor.key for factor in profile.factors],
        "required_future_data": list(profile.required_future_data),
        "status": "valuation_data_pending",
        "nav_sample_size": len(nav_points),
    }


def analyze_index_price_proxy(
    index_bars: list[StockBar],
    index_code: str,
    index_name: str,
    index_quote_id: str | None,
) -> dict[str, Any]:
    latest = index_bars[-1] if index_bars else None
    percentile = percentile_rank(
        [item.close for item in index_bars],
        latest.close if latest else None,
    )
    score = percentile * 100 if percentile is not None else None
    level = valuation_level(score)
    history_confidence = min(len(index_bars) / 1200, 1.0)
    confidence = round(0.25 + 0.35 * history_confidence, 2) if score is not None else 0.0

    return {
        "method": INDEX_ETF_PROFILE.method,
        "profile": INDEX_ETF_PROFILE.key,
        "profile_name": INDEX_ETF_PROFILE.name,
        "score": round(score, 2) if score is not None else None,
        "level": level,
        "level_zh": LEVEL_LABELS_ZH[level],
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "factor_coverage": 1.0 if score is not None else 0.0,
        "factors": [
            {
                "key": "index_price_percentile",
                "name": "跟踪指数价格历史分位",
                "weight": 1.0,
                "value": latest.close if latest else None,
                "percentile": percentile,
                "score": score,
                "level": level,
                "level_zh": LEVEL_LABELS_ZH[level],
            }
        ]
        if score is not None
        else [],
        "missing_factors": [],
        "required_future_data": list(INDEX_ETF_PROFILE.required_future_data),
        "status": "proxy_valuation",
        "index": {
            "code": index_code,
            "name": index_name,
            "quote_id": index_quote_id,
            "sample_size": len(index_bars),
            "as_of": latest.date.isoformat() if latest else None,
        },
    }
