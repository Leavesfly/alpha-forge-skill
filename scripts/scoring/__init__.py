"""买点三灯决策层：价/势/时三维评估、交易计划、历史回放与市场扫描。

不把指标混成一个黑箱总分，而是三个**正交**维度各自亮灯（绿/黄/红/灰）：

- 价（值不值得拥有）：基本面硬伤否决 + 估值历史分位；
- 势（市场是否认同）：趋势分（动量/相对强度/趋势效率）+ 均线周线结构；
- 时（是不是好买点）：过热偏离、回调状态、RSI、量价、事件风险。

三灯经决策矩阵输出行动结论（趋势买点/纯趋势仓/等回踩/左侧观察/回避/
持仓需减风险/无法评分），灰灯诚实标注数据缺失，不猜测补齐。
时灯阈值受波动率缩放因子 vol_k 动态调整。
"""

from .engine import (
    ACTIONABLE_VERDICTS,
    COLOR_CN,
    DEFAULT_BENCHMARKS,
    LIGHT_CN,
    LIGHTS,
    MIN_BARS,
    TREND_GREEN,
    TREND_RED,
    VAL_GREEN,
    VAL_RED,
    VERDICT_CN,
    VERDICTS,
    VOL_K_MAX,
    VOL_K_MIN,
    ScoreResult,
    default_benchmark,
    score_symbol,
)
from .plan import attach_position_sizing, build_trade_plan, format_plan
from .present import DISCLAIMER, print_score_report
from .replay import format_replay_report, replay_study, replay_verdicts
from .scan import scan_symbols

__all__ = [
    "ACTIONABLE_VERDICTS",
    "COLOR_CN",
    "DEFAULT_BENCHMARKS",
    "LIGHT_CN",
    "LIGHTS",
    "MIN_BARS",
    "TREND_GREEN",
    "TREND_RED",
    "VAL_GREEN",
    "VAL_RED",
    "VERDICT_CN",
    "VERDICTS",
    "VOL_K_MAX",
    "VOL_K_MIN",
    "ScoreResult",
    "default_benchmark",
    "score_symbol",
    "build_trade_plan",
    "attach_position_sizing",
    "format_plan",
    "DISCLAIMER",
    "print_score_report",
    "format_replay_report",
    "replay_study",
    "replay_verdicts",
    "scan_symbols",
]
