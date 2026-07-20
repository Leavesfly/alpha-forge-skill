"""风险管理：风险度量、暴露约束、回撤熔断、业绩归因与压力测试。"""

from __future__ import annotations

from .attribution import factor_attribution, return_contribution
from .limits import apply_exposure_limits, drawdown_circuit_breaker
from .metrics import (
    conditional_var,
    downside_deviation,
    parametric_var,
    risk_report,
    tail_ratio,
    ulcer_index,
    value_at_risk,
)
from .stress import HISTORICAL_SCENARIOS, historical_scenarios, monte_carlo_stress

__all__ = [
    "value_at_risk",
    "conditional_var",
    "parametric_var",
    "downside_deviation",
    "tail_ratio",
    "ulcer_index",
    "risk_report",
    "apply_exposure_limits",
    "drawdown_circuit_breaker",
    "return_contribution",
    "factor_attribution",
    "HISTORICAL_SCENARIOS",
    "historical_scenarios",
    "monte_carlo_stress",
]
