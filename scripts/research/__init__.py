"""研究工具：稳健性验证、走步样本外检验、事件研究与市场状态识别。"""

from __future__ import annotations

from .event_study import event_study
from .regime import detect_regime, format_regime
from .validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_stats,
)
from .walk_forward import WalkForwardResult, walk_forward

__all__ = [
    "deflated_sharpe_ratio",
    "detect_regime",
    "expected_max_sharpe",
    "format_regime",
    "probabilistic_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "sharpe_stats",
    "walk_forward",
    "WalkForwardResult",
    "event_study",
]
