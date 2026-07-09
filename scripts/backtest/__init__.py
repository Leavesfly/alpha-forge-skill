"""回测引擎包。"""

from __future__ import annotations

from .engine import BacktestResult, run_backtest
from .metrics import compute_metrics, format_report, max_drawdown
from .optimize import grid_search

__all__ = [
    "BacktestResult",
    "run_backtest",
    "compute_metrics",
    "format_report",
    "max_drawdown",
    "grid_search",
]
