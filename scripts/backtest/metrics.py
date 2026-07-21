"""回测绩效指标（兼容层）。

实际实现已迁移至 ``metrics/`` 共享内核，本模块保留 re-export
确保所有现有 ``from backtest.metrics import xxx`` 路径继续工作。
新代码建议直接 ``from metrics import ...``。
"""

from __future__ import annotations

# re-export 共享内核的全部公开符号（向后兼容）
from metrics import (  # noqa: F401
    ANNUALIZATION,
    compute_metrics,
    format_report,
    max_drawdown,
    max_drawdown_duration,
    omega_ratio,
    periods_per_year,
    relative_metrics,
)
from metrics import _PF_CAP, _trade_returns  # noqa: F401 - 内部使用
