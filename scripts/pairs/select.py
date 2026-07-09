"""配对筛选：从收盘价矩阵中挑选适合做配对交易的标的对。

纯 numpy 实现，依据：
- 对数收益相关性（越高越可能同步）
- 对数价差的对冲比率 beta（OLS 斜率）
- 价差均值回复速度（AR(1) 半衰期，越短回复越快）
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd


@dataclass
class Pair:
    """一个候选配对及其统计量。"""

    a: str
    b: str
    beta: float
    half_life: float
    corr: float


def hedge_ratio(log_a: pd.Series, log_b: pd.Series) -> float:
    """对数价格 OLS 斜率作为对冲比率 beta：log_a ≈ alpha + beta·log_b。"""
    slope, _intercept = np.polyfit(log_b.values, log_a.values, 1)
    return float(slope)


def half_life(spread: pd.Series) -> float:
    """由 AR(1) 估计价差的均值回复半衰期（交易日）。

    拟合 Δspread_t = a + rho·spread_{t-1}，半衰期 = -ln(2)/ln(1+rho)。
    无法回复（rho>=0）时返回 inf。
    """
    lag = spread.shift(1)
    delta = spread - lag
    valid = pd.concat([lag, delta], axis=1).dropna()
    if len(valid) < 3:
        return float("inf")
    rho, _intercept = np.polyfit(valid.iloc[:, 0].values, valid.iloc[:, 1].values, 1)
    denom = np.log1p(rho)
    if rho >= 0 or not np.isfinite(denom) or denom == 0:
        return float("inf")
    return float(-np.log(2) / denom)


def select_pairs(
    prices: pd.DataFrame,
    top_n: int = 3,
    min_corr: float = 0.7,
) -> list[Pair]:
    """从价格矩阵筛选候选配对。

    Args:
        prices: 收盘价矩阵（日期 × 标的）。
        top_n: 返回的配对数量。
        min_corr: 对数收益相关性阈值。

    Returns:
        按半衰期升序（回复快优先）排序的 Pair 列表。
    """
    log_px = np.log(prices.where(prices > 0))
    log_ret = log_px.diff()
    corr = log_ret.corr()

    candidates: list[Pair] = []
    for a, b in combinations(prices.columns, 2):
        c = corr.loc[a, b]
        if not np.isfinite(c) or c < min_corr:
            continue
        la, lb = log_px[a].dropna(), log_px[b].dropna()
        common = la.index.intersection(lb.index)
        if len(common) < 30:
            continue
        beta = hedge_ratio(la.loc[common], lb.loc[common])
        spread = la.loc[common] - beta * lb.loc[common]
        hl = half_life(spread)
        if not np.isfinite(hl) or hl <= 0:
            continue
        candidates.append(Pair(a=a, b=b, beta=beta, half_life=hl, corr=float(c)))

    candidates.sort(key=lambda p: p.half_life)
    return candidates[:top_n]
