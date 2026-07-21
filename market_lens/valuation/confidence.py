from __future__ import annotations

from math import prod
from typing import Any

from market_lens.valuation.scoring import clamp


def calculate_confidence(
    components: dict[str, float],
    *,
    caps: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    normalized = {key: clamp(value) for key, value in sorted(components.items())}
    score = prod(normalized.values()) ** (1 / len(normalized)) if normalized else 0.0
    applied_caps: list[dict[str, Any]] = []
    for reason, limit in caps or []:
        normalized_limit = clamp(limit)
        if score > normalized_limit:
            score = normalized_limit
            applied_caps.append({"reason": reason, "limit": normalized_limit})
    return {
        "score": round(score, 4),
        "components": normalized,
        "caps": applied_caps,
        "reasons": confidence_reasons(normalized, applied_caps),
    }


def confidence_reasons(
    components: dict[str, float],
    caps: list[dict[str, Any]],
) -> list[str]:
    reasons = [f"low_{key}:{value:.4f}" for key, value in components.items() if value < 0.75]
    reasons.extend(f"confidence_cap:{item['reason']}" for item in caps)
    return reasons


def conservative_overall_confidence(
    dimensions: dict[str, dict[str, Any] | None],
) -> float:
    values = [
        float(dimension["confidence"])
        for dimension in dimensions.values()
        if dimension is not None and dimension.get("confidence") is not None
    ]
    return round(min(values), 4) if values else 0.0

