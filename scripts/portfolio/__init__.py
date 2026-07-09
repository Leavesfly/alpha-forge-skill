"""多标的组合回测包。"""

from __future__ import annotations

from .engine import PortfolioResult, run_portfolio_backtest
from .rotation import ROTATIONS, get_weights

__all__ = [
    "PortfolioResult",
    "run_portfolio_backtest",
    "ROTATIONS",
    "get_weights",
]
