"""价值筛选模块：基于基本面硬阈值的全市场低估/潜力机会筛选。

定位与互补：
- run_scan.py：趋势动量纪律过滤（现在能不能买）
- run_factor.py：多因子截面排名（相对好坏）
- run_screener.py（本模块）：基本面价值发现（哪些被低估）

两阶段漏斗：批量快照过滤（PE/PB/市值）→ 逐只深度过滤（ROE/负债/分红/增速/现金流）；
启用 52 周位置维度时追加 Phase 3 位置过滤。内置 multibagger（十倍股特征）预设。
"""

from .engine import (
    PRESETS,
    ScreenCriteria,
    ScreenResult,
    composite_score,
    run_screen,
    screen_astock_phase1,
    screen_astock_phase2,
    screen_yfinance,
)

__all__ = [
    "PRESETS",
    "ScreenCriteria",
    "ScreenResult",
    "composite_score",
    "run_screen",
    "screen_astock_phase1",
    "screen_astock_phase2",
    "screen_yfinance",
]
