"""定投（定期定额 / DCA）回测模块。

按固定周期注入现金、累积份额，用资金加权收益率（XIRR）计量，
并与一次性投入基准对比。可选择时叠加（低于均线加倍投入）。
"""

from __future__ import annotations

from .engine import DCAResult, run_dca_backtest
from .metrics import (
    compute_dca_metrics,
    compute_lumpsum_metrics,
    format_dca_report,
    format_lumpsum_report,
    xirr,
)

__all__ = [
    "DCAResult",
    "run_dca_backtest",
    "compute_dca_metrics",
    "compute_lumpsum_metrics",
    "format_dca_report",
    "format_lumpsum_report",
    "xirr",
]
