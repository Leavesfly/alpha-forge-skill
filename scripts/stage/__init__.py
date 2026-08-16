"""阶段定位（Stage Analysis）：个股当前走到「台阶式循环」的哪一段。

只吃日线价量，用箱体（平台）识别 + 位置分位 + 均线结构，把标的定位到七态：

    低位筑底 base → 突破确认 breakout → 上升推进 advance
    → 高位派发 top → 破位下行 breakdown → 下降趋势 decline → （重新筑底）
    数据不足时 unknown（诚实标注，不猜测）

与既有模块的分工：``research.regime`` 给统计属性（缺「位置」维度，低位平台与
高位平台同名 range）；``scoring`` 三灯回答「能不能买」（需基本面/估值/基准）；
本模块只回答「现在在哪」。两者是互不干扰的独立能力，不相互依赖、不链式串联。

阶段判定是描述性统计而非预测，存在滞后；阈值为纪律预设值，未经样本外验证。
"""

from .box import (
    BREAK_BUFFER,
    MAX_BOX_ER,
    MAX_BOX_HEIGHT,
    MIN_TOUCHES,
    TOUCH_TOLERANCE,
    Box,
    crossed_above,
    crossed_below,
    find_box,
)
from .engine import (
    DEFAULT_CONFIRM_DAYS,
    DEFAULT_WINDOW,
    ER_TREND,
    MIN_BARS,
    POSITION_HIGH,
    POSITION_LOW,
    POSITION_WINDOW,
    PRIOR_GAIN,
    PRIOR_WINDOW,
    STAGE_CN,
    STAGES,
    VOL_CONFIRM,
    StageResult,
    detect_stage,
    stage_history,
)
from .present import STAGE_DISCLAIMER, print_stage_report

__all__ = [
    "Box",
    "find_box",
    "crossed_above",
    "crossed_below",
    "MAX_BOX_HEIGHT",
    "MAX_BOX_ER",
    "MIN_TOUCHES",
    "TOUCH_TOLERANCE",
    "BREAK_BUFFER",
    "StageResult",
    "detect_stage",
    "stage_history",
    "STAGE_CN",
    "STAGES",
    "MIN_BARS",
    "POSITION_WINDOW",
    "POSITION_LOW",
    "POSITION_HIGH",
    "PRIOR_WINDOW",
    "PRIOR_GAIN",
    "VOL_CONFIRM",
    "ER_TREND",
    "DEFAULT_WINDOW",
    "DEFAULT_CONFIRM_DAYS",
    "STAGE_DISCLAIMER",
    "print_stage_report",
]
