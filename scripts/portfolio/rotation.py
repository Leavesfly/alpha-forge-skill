"""截面轮动策略：为多标的组合生成目标权重矩阵。

所有策略接收收盘价矩阵（索引为日期，列为标的），按 rebalance 周期在
调仓日计算目标权重，非调仓日前向填充（保持上次权重）。权重矩阵未做
shift，前视规避由组合引擎统一处理（held = weights.shift(1)）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .optimize import hrp, max_sharpe, min_cvar, min_variance


def _rebalance_dates(n: int, warmup: int, rebalance: int) -> list[int]:
    """从 warmup 开始，每隔 rebalance 个周期取一个调仓日下标。"""
    return list(range(warmup, n, rebalance))


def _expand(weights_at: dict[int, pd.Series], index, columns) -> pd.DataFrame:
    """将调仓日权重展开为完整矩阵并前向填充。"""
    target = pd.DataFrame(np.nan, index=index, columns=columns)
    for i, w in weights_at.items():
        target.iloc[i] = w
    return target.ffill().fillna(0.0)


def momentum_rotation(
    prices: pd.DataFrame,
    lookback: int = 60,
    top_k: int = 2,
    rebalance: int = 20,
    long_only_positive: bool = True,
) -> pd.DataFrame:
    """截面动量轮动：持有过去 lookback 期涨幅最高的 top_k 只，等权。

    Args:
        prices: 收盘价矩阵。
        lookback: 动量回看周期。
        top_k: 持有标的数量。
        rebalance: 调仓周期。
        long_only_positive: 仅在动量为正时持有（否则空仓该名额）。
    """
    mom = prices.pct_change(lookback)
    n = len(prices)
    top_k = min(top_k, prices.shape[1])
    weights_at: dict[int, pd.Series] = {}
    for i in _rebalance_dates(n, lookback, rebalance):
        row = mom.iloc[i].dropna()
        if long_only_positive:
            row = row[row > 0]
        w = pd.Series(0.0, index=prices.columns)
        if len(row) > 0:
            chosen = row.nlargest(top_k).index
            w[chosen] = 1.0 / len(chosen)
        weights_at[i] = w
    return _expand(weights_at, prices.index, prices.columns)


def equal_weight(
    prices: pd.DataFrame,
    rebalance: int = 20,
    **_ignored,
) -> pd.DataFrame:
    """等权组合：所有标的 1/N，按 rebalance 周期再平衡。"""
    n = len(prices)
    m = prices.shape[1]
    weights_at = {
        i: pd.Series(1.0 / m, index=prices.columns)
        for i in _rebalance_dates(n, 1, rebalance)
    }
    return _expand(weights_at, prices.index, prices.columns)


def inverse_vol(
    prices: pd.DataFrame,
    lookback: int = 60,
    rebalance: int = 20,
    **_ignored,
) -> pd.DataFrame:
    """风险平价（逆波动率）：权重与各标的波动率成反比。"""
    ret = prices.pct_change()
    vol = ret.rolling(lookback).std()
    n = len(prices)
    weights_at: dict[int, pd.Series] = {}
    for i in _rebalance_dates(n, lookback, rebalance):
        v = vol.iloc[i].dropna()
        w = pd.Series(0.0, index=prices.columns)
        v = v[v > 0]
        if len(v) > 0:
            inv = 1.0 / v
            w[inv.index] = inv / inv.sum()
        weights_at[i] = w
    return _expand(weights_at, prices.index, prices.columns)


#: 轮动策略注册表：名称 -> 函数
ROTATIONS = {
    "momentum": momentum_rotation,
    "equal_weight": equal_weight,
    "inverse_vol": inverse_vol,
    "min_variance": min_variance,
    "max_sharpe": max_sharpe,
    "hrp": hrp,
    "min_cvar": min_cvar,
}


def get_weights(name: str, prices: pd.DataFrame, **params) -> pd.DataFrame:
    """按名称计算目标权重矩阵。"""
    if name not in ROTATIONS:
        available = ", ".join(ROTATIONS)
        raise KeyError(f"未知轮动策略 '{name}'，可选：{available}")
    return ROTATIONS[name](prices, **params)
