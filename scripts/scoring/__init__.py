"""纪律评分决策层：四层否决式评分、交易计划、历史回放与市场扫描。

设计源自「分层否决」架构（借鉴 worth-buy-stocks）：
ALPHA 加权只管排名，风险否决只封顶/否决，技术确认只拦截，
事件风险只降级不加分，持仓状态只改操作建议。

增强能力：
- 动态自适应阈值：波动率缩放因子 vol_k 调整 RSI/MA 偏离/量能阈值；
- ADX 趋势强度感知：强趋势放宽 RSI 拦截并豁免 KDJ 死叉；
- 基本面否决层（可选）：ST/连续亏损/资不抵债一票否决。
"""

from .engine import (
    ADX_MODERATE,
    ADX_STRONG,
    DEFAULT_BENCHMARKS,
    MIN_BARS,
    VERDICT_CN,
    VERDICTS,
    VOL_K_MAX,
    VOL_K_MIN,
    ScoreResult,
    default_benchmark,
    score_symbol,
)
from .plan import attach_position_sizing, build_trade_plan, format_plan
from .present import DISCLAIMER, LAYER_CN, print_score_report
from .replay import format_replay_report, replay_study, replay_verdicts
from .scan import scan_symbols

__all__ = [
    "ADX_MODERATE",
    "ADX_STRONG",
    "DEFAULT_BENCHMARKS",
    "MIN_BARS",
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
    "LAYER_CN",
    "print_score_report",
    "format_replay_report",
    "replay_study",
    "replay_verdicts",
    "scan_symbols",
]
