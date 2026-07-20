"""A 股交易规则：涨跌停与停牌导致的「不可成交」建模。

在日频收盘价回测中，最影响可信度的规则是：
- 涨停日无法买入（想加仓被挡）；
- 跌停日无法卖出（想减仓被挡）；
- 停牌日（成交量为 0）双向都无法成交。

本模块从 OHLCV 推断每根 K 线的「可买/可卖」状态，并提供一个有状态的
`apply_tradability`，把「目标持仓」修正为「实际能达成的持仓」——无法成交时
维持上一期持仓，直到出现可成交的 K 线。

注意：日频、次日成交的引擎天然满足 A 股 T+1（同一 bar 内不会又买又卖），
因此这里聚焦涨跌停与停牌；T+1 的显式约束留待未来事件驱动引擎。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: 主板/沪深 A 股涨跌停幅度
LIMIT_MAIN = 0.10
#: 科创板/创业板涨跌停幅度
LIMIT_STAR_CHINEXT = 0.20
#: ST 股涨跌停幅度
LIMIT_ST = 0.05

_EPS = 1e-4  # 判定触及涨跌停的容差（价格四舍五入误差）


@dataclass
class TradingRules:
    """交易规则配置。

    Attributes:
        limit_pct: 涨跌停幅度（如 0.10）；None 表示不启用涨跌停约束。
        check_suspension: 是否将成交量为 0 视为停牌（双向不可成交）。
    """

    limit_pct: float | None = None
    check_suspension: bool = True

    @classmethod
    def astock(cls, board: str = "main") -> "TradingRules":
        """A 股交易规则预设。

        Args:
            board: ``main``（主板 10%）/ ``star`` / ``chinext``（20%）/ ``st``（5%）。
        """
        board = (board or "main").lower()
        limit = {
            "main": LIMIT_MAIN,
            "star": LIMIT_STAR_CHINEXT,
            "chinext": LIMIT_STAR_CHINEXT,
            "st": LIMIT_ST,
        }.get(board)
        if limit is None:
            raise ValueError(
                f"未知板块 '{board}'，可选：main / star / chinext / st"
            )
        return cls(limit_pct=limit, check_suspension=True)


def tradable_masks(
    df: pd.DataFrame, rules: TradingRules
) -> tuple[np.ndarray, np.ndarray]:
    """从 OHLCV 推断每根 K 线的买入/卖出受阻状态。

    Args:
        df: 至少含 close 的 DataFrame；有 volume 时可判定停牌。
        rules: 交易规则配置。

    Returns:
        (buy_blocked, sell_blocked) 两个布尔数组（True 表示该方向不可成交）。
    """
    n = len(df)
    close = df["close"].astype(float)
    buy_blocked = np.zeros(n, dtype=bool)
    sell_blocked = np.zeros(n, dtype=bool)

    if rules.limit_pct is not None:
        ret = close.pct_change().to_numpy()
        # 涨停：涨幅达到上限 -> 不可买入
        buy_blocked |= ret >= (rules.limit_pct - _EPS)
        # 跌停：跌幅达到下限 -> 不可卖出
        sell_blocked |= ret <= -(rules.limit_pct - _EPS)

    if rules.check_suspension and "volume" in df.columns:
        halted = df["volume"].fillna(0.0).to_numpy() <= 0.0
        buy_blocked |= halted
        sell_blocked |= halted

    return buy_blocked, sell_blocked


def apply_tradability(
    target: pd.Series,
    buy_blocked: np.ndarray,
    sell_blocked: np.ndarray,
) -> pd.Series:
    """将目标持仓修正为受交易规则约束后的实际持仓。

    想加仓但当根买入受阻、或想减仓但卖出受阻时，维持上一期持仓，
    待后续可成交的 K 线再调整。

    Args:
        target: 目标持仓序列（已 shift(1) 到成交时间线）。
        buy_blocked: 买入受阻布尔数组。
        sell_blocked: 卖出受阻布尔数组。

    Returns:
        实际持仓 Series（与 target 对齐）。
    """
    tgt = target.to_numpy(dtype=float)
    n = len(tgt)
    actual = np.zeros(n, dtype=float)
    prev = 0.0
    for t in range(n):
        want = tgt[t]
        if want > prev and buy_blocked[t]:
            actual[t] = prev  # 想加仓却涨停/停牌，维持
        elif want < prev and sell_blocked[t]:
            actual[t] = prev  # 想减仓却跌停/停牌，维持
        else:
            actual[t] = want
        prev = actual[t]
    return pd.Series(actual, index=target.index)
