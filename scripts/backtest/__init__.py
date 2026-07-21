"""回测引擎包。"""

from __future__ import annotations

from .costs import CostModel
from .engine import BacktestConfig, BacktestResult, run_backtest
from .ledger import run_backtest_ledger
from .metrics import compute_metrics, format_report, max_drawdown
from .optimize import grid_search
from .rules import TradingRules

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "run_backtest",
    "run_backtest_ledger",
    "compute_metrics",
    "format_report",
    "max_drawdown",
    "grid_search",
    "CostModel",
    "TradingRules",
]
