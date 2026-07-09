"""配对交易信号与权重构造。

价差 = log(A) - beta·log(B)，用滚动 z-score 做均值回复开平仓，
生成价差持仓 {-1,0,1}，再转换为 A/B 两腿的目标权重（市场中性）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def pair_spread(prices: pd.DataFrame, a: str, b: str, beta: float) -> pd.Series:
    """构造对数价差序列 spread = log(A) - beta·log(B)。"""
    log_a = np.log(prices[a].where(prices[a] > 0))
    log_b = np.log(prices[b].where(prices[b] > 0))
    return (log_a - beta * log_b).rename("spread")


def zscore(spread: pd.Series, lookback: int) -> pd.Series:
    """滚动 z-score（统计量截至当日，避免前视）。"""
    mean = spread.rolling(lookback).mean()
    std = spread.rolling(lookback).std().replace(0.0, np.nan)
    return (spread - mean) / std


def pair_signals(
    spread: pd.Series,
    lookback: int = 60,
    entry: float = 2.0,
    exit: float = 0.5,
    stop: float = 3.5,
) -> pd.Series:
    """由价差 z-score 生成价差持仓状态机。

    约定持仓方向针对「价差」：
    - z <= -entry：价差偏低 -> 做多价差（多 A 空 B），position=+1
    - z >= entry ：价差偏高 -> 做空价差（空 A 多 B），position=-1
    - |z| <= exit：均值回复，平仓 position=0
    - |z| >= stop：反向过大，止损平仓 position=0

    Returns:
        与 spread 同索引的持仓序列（-1/0/1）。
    """
    z = zscore(spread, lookback)
    position = pd.Series(0.0, index=spread.index)
    pos = 0.0
    for i in range(len(z)):
        zi = z.iloc[i]
        if np.isnan(zi):
            position.iloc[i] = 0.0
            continue
        if pos == 0.0:
            if zi <= -entry:
                pos = 1.0
            elif zi >= entry:
                pos = -1.0
        else:
            if abs(zi) <= exit or abs(zi) >= stop:
                pos = 0.0
        position.iloc[i] = pos
    return position


def pair_weights(
    prices: pd.DataFrame,
    a: str,
    b: str,
    position: pd.Series,
) -> pd.DataFrame:
    """将价差持仓转为 A/B 两腿目标权重（多空各半，总杠杆 1）。

    做多价差(+1)：A +0.5、B -0.5；做空价差(-1)：A -0.5、B +0.5。
    返回日期 × 标的权重矩阵，交由组合引擎 shift(1) 执行。
    """
    weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    pos = position.reindex(prices.index).fillna(0.0)
    weights[a] = pos * 0.5
    weights[b] = -pos * 0.5
    return weights
