from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import pstdev
from typing import Any, Literal

from market_lens.types import FundHolding, FundNavPoint, StockBar, StockValuationPoint
from market_lens.valuation.metrics import fund_performance_index, percentile_rank

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
    method="holdings_weighted_multi_factor",
    factors=(
        FactorSpec("dividend_yield", "股息率", 0.40, lower_is_cheaper=False),
        FactorSpec("pb_historical_percentile", "持仓加权 PB 历史分位", 0.25),
        FactorSpec("pe_historical_percentile", "持仓加权 PE 历史分位", 0.20),
        FactorSpec("peer_pe_percentile", "同行业 PE 相对分位", 0.15),
    ),
    required_future_data=("完整持仓", "指数估值历史", "基金实时申赎与折溢价"),
)

GENERIC_FUND_PROFILE = ValuationProfile(
    key="generic_fund",
    name="基金",
    method="holdings_weighted_multi_factor",
    factors=(
        FactorSpec("pe_historical_percentile", "持仓加权 PE 历史分位", 0.35),
        FactorSpec("pb_historical_percentile", "持仓加权 PB 历史分位", 0.30),
        FactorSpec("peer_pe_percentile", "同行业 PE 相对分位", 0.20),
        FactorSpec("dividend_yield", "股息率", 0.15, lower_is_cheaper=False),
    ),
    required_future_data=("完整持仓", "同类基金风格分位", "基金实时申赎与折溢价"),
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
    holdings: list[FundHolding] | None = None,
    holding_analyses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = infer_fund_profile(name)
    holdings = holdings or []
    holding_analyses = holding_analyses or {}
    if not holdings:
        return pending_fund_valuation(profile, nav_points)

    latest_nav_date = nav_points[-1].date if nav_points else date.today()
    report_date = next((item.report_date for item in holdings if item.report_date), None)
    if report_date and report_date > latest_nav_date:
        result = pending_fund_valuation(profile, nav_points)
        result.update(
            {
                "status": "holdings_after_analysis_date",
                "holdings": summarize_holdings(holdings, holding_analyses, latest_nav_date),
            }
        )
        return result

    portfolio = build_holdings_portfolio(holdings, holding_analyses, nav_points)
    factor_results: list[dict[str, Any]] = []
    missing_factors: list[str] = []
    total_model_weight = sum(factor.weight for factor in profile.factors)
    available_model_weight = 0.0
    weighted_data_coverage = 0.0

    for factor in profile.factors:
        metric = portfolio["metrics"].get(factor.key) or {}
        value = metric.get("value")
        if value is None:
            missing_factors.append(factor.key)
            continue
        score = fund_factor_score(factor.key, value)
        available_model_weight += factor.weight
        weighted_data_coverage += factor.weight * float(metric.get("coverage") or 0)
        level = valuation_level(score)
        factor_results.append(
            {
                "key": factor.key,
                "name": factor.name,
                "weight": factor.weight,
                "value": value,
                "coverage": metric.get("coverage"),
                "score": round(score, 2),
                "level": level,
                "level_zh": LEVEL_LABELS_ZH[level],
            }
        )

    score = None
    if available_model_weight:
        score = sum(item["score"] * item["weight"] for item in factor_results)
        score /= available_model_weight
    factor_coverage = available_model_weight / total_model_weight if total_model_weight else 0
    data_coverage = weighted_data_coverage / total_model_weight if total_model_weight else 0
    report_recency = holdings_recency_factor(report_date, latest_nav_date)
    confidence = round(data_coverage * report_recency, 2)
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
        "factor_coverage": round(factor_coverage, 2),
        "holding_factor_coverage": round(data_coverage, 2),
        "factors": factor_results,
        "missing_factors": missing_factors,
        "required_future_data": list(profile.required_future_data),
        "status": "holdings_valuation" if score is not None else "valuation_data_pending",
        "nav_sample_size": len(nav_points),
        "portfolio": portfolio,
        "holdings": summarize_holdings(holdings, holding_analyses, latest_nav_date),
    }


def pending_fund_valuation(
    profile: ValuationProfile,
    nav_points: list[FundNavPoint],
) -> dict[str, Any]:
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


def fund_factor_score(key: str, value: float) -> float:
    if key == "dividend_yield":
        # 6% is used as a transparent high-yield anchor; higher yield means cheaper.
        return max(0.0, min(100.0, 100.0 - value / 0.06 * 100.0))
    return max(0.0, min(100.0, value * 100.0))


def build_holdings_portfolio(
    holdings: list[FundHolding],
    analyses: dict[str, dict[str, Any]],
    nav_points: list[FundNavPoint],
) -> dict[str, Any]:
    metric_paths = {
        "weighted_pe_ttm": ("valuation", "pe_ttm"),
        "weighted_pb": ("valuation", "pb"),
        "pe_historical_percentile": ("valuation", "pe_ttm_percentile"),
        "pb_historical_percentile": ("valuation", "pb_percentile"),
        "peer_pe_percentile": (
            "valuation",
            "peer_comparison",
            "valuation",
            "percentiles",
            "pe_ttm",
        ),
        "roe_weighted": ("valuation", "fundamentals", "roe_weighted"),
        "parent_netprofit_growth_pct": (
            "valuation",
            "fundamentals",
            "parent_netprofit_growth_pct",
        ),
        "revenue_growth_pct": (
            "valuation",
            "fundamentals",
            "revenue_growth_pct",
        ),
        "dividend_yield": ("valuation", "dividend", "dividend_yield"),
        "underlying_quality_score": (
            "assessment",
            "dimensions",
            "quality",
            "score",
        ),
    }
    metrics = {
        key: weighted_holding_metric(
            holdings,
            analyses,
            path,
            positive=key in {"weighted_pe_ttm", "weighted_pb"},
        )
        for key, path in metric_paths.items()
    }
    metrics["annualized_volatility"] = {
        "value": annualized_nav_volatility(nav_points),
        "coverage": 1.0 if len(nav_points) >= 20 else 0.0,
    }
    return {
        "metrics": metrics,
        "industry_weights": aggregate_industry_weights(holdings, analyses),
    }


def weighted_holding_metric(
    holdings: list[FundHolding],
    analyses: dict[str, dict[str, Any]],
    path: tuple[str, ...],
    positive: bool = False,
) -> dict[str, float | None]:
    weighted_sum = 0.0
    available_weight = 0.0
    available_count = 0
    for holding in holdings:
        weight = (holding.weight_pct or 0.0) / 100.0
        value = nested_value(analyses.get(holding.code), path)
        if not isinstance(value, (int, float)) or (positive and value <= 0) or weight <= 0:
            continue
        weighted_sum += float(value) * weight
        available_weight += weight
        available_count += 1
    return {
        "value": round(weighted_sum / available_weight, 6) if available_weight else None,
        "coverage": round(available_weight, 4),
        "sample_size": available_count,
    }


def nested_value(value: dict[str, Any] | None, path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def aggregate_industry_weights(
    holdings: list[FundHolding],
    analyses: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    weights: dict[str, float] = {}
    for holding in holdings:
        industry = nested_value(
            analyses.get(holding.code),
            ("valuation", "industry", "em_industry"),
        )
        if not industry or holding.weight_pct is None:
            continue
        weights[str(industry)] = weights.get(str(industry), 0.0) + holding.weight_pct
    return [
        {"industry": industry, "weight_pct": round(weight, 2)}
        for industry, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True)
    ]


def summarize_holdings(
    holdings: list[FundHolding],
    analyses: dict[str, dict[str, Any]],
    as_of: date,
) -> dict[str, Any]:
    report_date = next((item.report_date for item in holdings if item.report_date), None)
    total_weight = sum(item.weight_pct or 0.0 for item in holdings)
    analyzed_weight = sum(
        item.weight_pct or 0.0 for item in holdings if analyses.get(item.code) is not None
    )
    return {
        "report_date": report_date.isoformat() if report_date else None,
        "report_age_days": max((as_of - report_date).days, 0) if report_date else None,
        "count": len(holdings),
        "analyzed_count": sum(1 for item in holdings if analyses.get(item.code) is not None),
        "top_holdings_weight": round(total_weight / 100.0, 4),
        "analyzed_holdings_weight": round(analyzed_weight / 100.0, 4),
        "items": [serialize_holding(item, analyses.get(item.code)) for item in holdings],
    }


def serialize_holding(
    holding: FundHolding,
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "rank": holding.rank,
        "code": holding.code,
        "name": holding.name,
        "weight_pct": holding.weight_pct,
        "shares_10k": holding.shares_10k,
        "market_value_10k": holding.market_value_10k,
        "analysis_available": analysis is not None,
        "industry": nested_value(analysis, ("valuation", "industry", "em_industry")),
        "pe_ttm": nested_value(analysis, ("valuation", "pe_ttm")),
        "pb": nested_value(analysis, ("valuation", "pb")),
        "roe_weighted": nested_value(analysis, ("valuation", "fundamentals", "roe_weighted")),
        "parent_netprofit_growth_pct": nested_value(
            analysis,
            ("valuation", "fundamentals", "parent_netprofit_growth_pct"),
        ),
        "dividend_yield": nested_value(analysis, ("valuation", "dividend", "dividend_yield")),
    }


def annualized_nav_volatility(nav_points: list[FundNavPoint]) -> float | None:
    values = [value for _, value in fund_performance_index(nav_points)]
    returns = [
        current / previous - 1
        for previous, current in zip(values, values[1:], strict=False)
        if previous
    ]
    if len(returns) < 20:
        return None
    return round(pstdev(returns) * sqrt(252), 6)


def holdings_recency_factor(report_date: date | None, as_of: date) -> float:
    if report_date is None:
        return 0.5
    age = max((as_of - report_date).days, 0)
    if age <= 120:
        return 1.0
    if age <= 240:
        return 0.8
    if age <= 365:
        return 0.6
    return 0.4


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
