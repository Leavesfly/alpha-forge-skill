"""CAN SLIM 成长股检查清单（欧奈尔法则）：七项纪律检查 + 横截面 RS 排名。

与 scoring 四层评分同属纪律过滤层：回答「这只票是否符合 CAN SLIM 画像」，
不是收益预测。C/A 基本面缺失时诚实降级（结论封顶「观察」）。
"""

from .engine import (
    LETTERS_CN,
    MIN_BARS,
    VERDICT_CN,
    CanSlimResult,
    canslim_check,
    rs_weighted_return,
)
from .fundamentals import fetch_fundamentals, is_a_share, load_fundamentals_csv

__all__ = [
    "LETTERS_CN",
    "MIN_BARS",
    "VERDICT_CN",
    "CanSlimResult",
    "canslim_check",
    "rs_weighted_return",
    "fetch_fundamentals",
    "is_a_share",
    "load_fundamentals_csv",
]
