"""价值筛选模块：基于基本面硬阈值的全市场低估/潜力机会筛选。

定位与互补：
- run_scan.py：趋势动量纪律过滤（现在能不能买）
- run_factor.py：多因子截面排名（相对好坏）
- run_screener.py（本模块）：基本面价值发现（哪些被低估）

两阶段漏斗：批量快照过滤（PE/PB/市值）→ 逐只深度过滤（ROE/负债/分红/增速/现金流/毛利）；
启用 52 周位置/回撤维度时追加位置或折扣过滤；启用猛兽股技术维度（高位/均线多头/
RS 线/量价/大势）时追加技术面过滤；启用连续分红/估值分位上限维度（红利股）时
追加分红纪律与分位过滤。扫描范围：A 股全市场（默认）、美股全市场
（universe="us"，东财快照，降级 S&P 500 名单）、手动标的列表。内置预设：
multibagger（十倍股统计特征）、hundredbagger（百倍股质量成长）、monster（猛兽股
右侧强势，取自波伊克《猛兽股》）、dhq（打折的高质量股，取自马哈尼《高增长
科技股投资法》）、superstock（超级强势股，取自斯泰恩《100倍超级强势股》）、
fisher（成长质量，取自费雪《怎样选择成长股》15 要点：营收+研发驱动的真成长，
利润率趋势不恶化且无增发稀释）、navellier（八大指标成长股，取自纳维里尔
《怎样选择成长股：持续获利选股8大指标》：双高增+高 ROE+现金流+利润率趋势
+机构预期+盈利动能）、
dividend（红利股左侧：高股息+低估值+低分位+连续分红，候选接 run_dca 分批）。
"""

from .engine import (
    PRESETS,
    ScreenCriteria,
    ScreenResult,
    composite_score,
    market_regime,
    run_screen,
    screen_astock_phase1,
    screen_astock_phase2,
    screen_us_phase1,
    screen_yfinance,
)

__all__ = [
    "PRESETS",
    "ScreenCriteria",
    "ScreenResult",
    "composite_score",
    "market_regime",
    "run_screen",
    "screen_astock_phase1",
    "screen_astock_phase2",
    "screen_us_phase1",
    "screen_yfinance",
]
