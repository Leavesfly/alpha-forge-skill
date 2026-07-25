"""配对交易（市场中性统计套利）模块。"""

from __future__ import annotations

from .select import Pair, half_life, hedge_ratio, select_pairs
from .strategy import pair_signals, pair_spread, pair_weights, zscore

__all__ = [
    "Pair",
    "select_pairs",
    "hedge_ratio",
    "half_life",
    "pair_signals",
    "pair_spread",
    "pair_weights",
    "zscore",
]
