from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

FALLBACK_MATRIX_VERSION = "fallback-matrix-v2"
FallbackMatrixKey = Literal["stock", "fund", "index"]
FallbackStepStatus = Literal[
    "not_attempted",
    "available",
    "unavailable",
    "rejected",
    "error",
    "skipped",
]


@dataclass(frozen=True)
class FallbackStep:
    key: str
    purpose: str
    source: str
    timeout_budget_seconds: int
    admission_condition: str
    output_method: str
    stop_condition: str

    @property
    def success_method(self) -> str:
        """Compatibility alias for traces emitted before output_method was explicit."""
        return self.output_method


@dataclass(frozen=True)
class FallbackMatrix:
    key: FallbackMatrixKey
    steps: tuple[FallbackStep, ...]

    def step(self, key: str) -> FallbackStep:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(f"Unknown {self.key} fallback step: {key}")


@dataclass
class FallbackTrace:
    matrix: FallbackMatrix
    _states: dict[str, dict[str, Any]] = field(default_factory=dict)
    selected_step: str | None = None
    selected_method: str | None = None
    terminal_reason: str | None = None

    def record(
        self,
        step_key: str,
        status: FallbackStepStatus,
        *,
        reason: str | None = None,
        selected: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        step = self.matrix.step(step_key)
        state: dict[str, Any] = {
            "status": status,
            "reason": stable_reason_code(reason),
        }
        if details:
            state["details"] = dict(details)
        self._states[step.key] = state
        if selected:
            if status != "available":
                raise ValueError("Only an available fallback step can be selected")
            self.selected_step = step.key
            self.selected_method = step.output_method

    def finish(self, *, terminal_reason: str | None = None) -> None:
        self.terminal_reason = stable_reason_code(terminal_reason)

    def was_recorded(self, step_key: str) -> bool:
        self.matrix.step(step_key)
        return step_key in self._states

    def reason_codes(self) -> list[str]:
        return list(
            dict.fromkeys(
                state["reason"]
                for state in self._states.values()
                if state.get("reason")
            )
        )

    def serialize(self) -> dict[str, Any]:
        return {
            "version": FALLBACK_MATRIX_VERSION,
            "timeout_policy": "hard_stage_deadline",
            "asset_scope": self.matrix.key,
            "selected_step": self.selected_step,
            "selected_method": self.selected_method,
            "terminal_reason": self.terminal_reason,
            "steps": [
                {
                    "key": step.key,
                    "purpose": step.purpose,
                    "source": step.source,
                    "timeout_budget_seconds": step.timeout_budget_seconds,
                    "admission_condition": step.admission_condition,
                    "output_method": step.output_method,
                    "success_method": step.success_method,
                    "stop_condition": step.stop_condition,
                    **self._states.get(
                        step.key,
                        {
                            "status": "not_attempted",
                            "reason": None,
                        },
                    ),
                }
                for step in self.matrix.steps
            ],
        }


STOCK_FALLBACK_MATRIX = FallbackMatrix(
    key="stock",
    steps=(
        FallbackStep(
            key="stock_valuation_history",
            purpose="valuation",
            source="eastmoney_datacenter_or_validated_lkg",
            timeout_budget_seconds=45,
            admission_condition="normalized valuation rows pass dataset validation",
            output_method="fundamental_valuation",
            stop_condition="verified valuation factors pass scoring gates",
        ),
        FallbackStep(
            key="stock_price_history",
            purpose="performance",
            source="eastmoney_push2his_or_validated_lkg",
            timeout_budget_seconds=45,
            admission_condition="normalized price rows pass dataset validation",
            output_method="stock_price_history",
            stop_condition="verified price rows cover the requested interval",
        ),
        FallbackStep(
            key="valuation_price_projection",
            purpose="performance_fallback",
            source="verified_stock_valuation_closes",
            timeout_budget_seconds=1,
            admission_condition="primary price history is unavailable",
            output_method="valuation_close_history",
            stop_condition="valuation rows contain positive dated closes",
        ),
        FallbackStep(
            key="stock_terminal",
            purpose="terminal",
            source="deterministic_assessment_contract",
            timeout_budget_seconds=1,
            admission_condition="no verified stock price series remains",
            output_method="unavailable",
            stop_condition="no verified price series can identify an analysis date",
        ),
    ),
)


FUND_FALLBACK_MATRIX = FallbackMatrix(
    key="fund",
    steps=(
        FallbackStep(
            key="exchange_fund_price_history",
            purpose="performance",
            source="eastmoney_push2his_or_validated_lkg",
            timeout_budget_seconds=45,
            admission_condition="normalized exchange price rows pass dataset validation",
            output_method="exchange_price_history",
            stop_condition="verified exchange-traded fund price rows are available",
        ),
        FallbackStep(
            key="fund_nav_history",
            purpose="performance_fallback",
            source="eastmoney_pingzhongdata_f10_or_validated_lkg",
            timeout_budget_seconds=60,
            admission_condition="exchange price is unavailable or not applicable",
            output_method="fund_nav_history",
            stop_condition="verified fund NAV rows are available",
        ),
        FallbackStep(
            key="fund_holdings_valuation",
            purpose="valuation",
            source="verified_fund_or_index_holdings",
            timeout_budget_seconds=60,
            admission_condition="holdings identity, date, and coverage route is verified",
            output_method="holdings_valuation",
            stop_condition="weighted holdings factors pass scoring gates",
        ),
        FallbackStep(
            key="fund_index_matrix",
            purpose="valuation_fallback",
            source="deterministic_index_fallback_matrix",
            timeout_budget_seconds=90,
            admission_condition="a tracked-index relationship is verified",
            output_method="index_fallback",
            stop_condition="an index fundamental or price proxy produces a score",
        ),
        FallbackStep(
            key="fund_terminal",
            purpose="terminal",
            source="deterministic_assessment_contract",
            timeout_budget_seconds=1,
            admission_condition="no verified fund valuation method remains",
            output_method="unavailable",
            stop_condition="no verified valuation method produces a score",
        ),
    ),
)


INDEX_FALLBACK_MATRIX = FallbackMatrix(
    key="index",
    steps=(
        FallbackStep(
            key="official_index_fundamentals",
            purpose="valuation",
            source="csindex_official",
            timeout_budget_seconds=60,
            admission_condition="tracked-index code and name are resolved",
            output_method="index_fundamental_valuation",
            stop_condition="identity, date, history, and complete-weight gates pass",
        ),
        FallbackStep(
            key="target_etf_nav_history",
            purpose="price_proxy_input",
            source="eastmoney_fund_nav_or_validated_lkg",
            timeout_budget_seconds=60,
            admission_condition="a target ETF relationship is verified",
            output_method="target_etf_nav_history",
            stop_condition="verified target ETF NAV rows are available",
        ),
        FallbackStep(
            key="sina_index_price_history",
            purpose="price_proxy_input",
            source="sina_index_history",
            timeout_budget_seconds=45,
            admission_condition="tracked-index identity and quote are resolved",
            output_method="sina_index_price_history",
            stop_condition="verified tracked-index price rows are available",
        ),
        FallbackStep(
            key="eastmoney_index_price_history",
            purpose="price_proxy_input",
            source="eastmoney_index_history",
            timeout_budget_seconds=45,
            admission_condition="tracked-index identity and quote are resolved",
            output_method="tracked_index_price_history",
            stop_condition="verified tracked-index price rows are available",
        ),
        FallbackStep(
            key="index_price_position_proxy",
            purpose="valuation_fallback",
            source="verified_index_price_history",
            timeout_budget_seconds=1,
            admission_condition="at least one verified proxy price series is available",
            output_method="price_position_proxy",
            stop_condition="price history passes proxy sample gates",
        ),
        FallbackStep(
            key="index_terminal",
            purpose="terminal",
            source="deterministic_assessment_contract",
            timeout_budget_seconds=1,
            admission_condition="no verified index valuation method remains",
            output_method="unavailable",
            stop_condition="no verified index valuation or price proxy produces a score",
        ),
    ),
)

FALLBACK_MATRICES: dict[FallbackMatrixKey, FallbackMatrix] = {
    "stock": STOCK_FALLBACK_MATRIX,
    "fund": FUND_FALLBACK_MATRIX,
    "index": INDEX_FALLBACK_MATRIX,
}


def new_fallback_trace(key: FallbackMatrixKey) -> FallbackTrace:
    return FallbackTrace(FALLBACK_MATRICES[key])


def attach_fallback_traces(
    result: dict[str, Any],
    *traces: FallbackTrace,
) -> None:
    serialized = {trace.matrix.key: trace.serialize() for trace in traces}
    result["fallback_matrices"] = serialized
    assessment = result.get("assessment")
    if not isinstance(assessment, dict):
        return
    data_quality = assessment.setdefault("data_quality", {})
    data_quality["fallback_matrices"] = serialized


def stable_reason_code(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.split(":", 1)[0].strip()
    if not normalized:
        return None
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized.casefold()).strip("_")
    return normalized or None
