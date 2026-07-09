"""组合优化：最小方差 / 最大夏普权重（numpy 解析解）。

在每个调仓日用过去 lookback 窗口的收益估计协方差/均值，求解目标权重，
非调仓日前向填充。仅做多约束通过「负权重截断后归一化」近似（非严格 QP）。
自包含实现，避免与 rotation 循环依赖。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.metrics import periods_per_year


def _long_only(w: np.ndarray) -> np.ndarray:
    """负权重截断为 0 后归一化；全部非正则退化为等权。"""
    w = np.clip(w, 0.0, None)
    s = w.sum()
    if s <= 0:
        return np.ones_like(w) / len(w)
    return w / s


def _min_variance_w(mean: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """最小方差：w ∝ Σ⁻¹·1。"""
    inv = np.linalg.pinv(cov)
    ones = np.ones(cov.shape[0])
    return _long_only(inv @ ones)


def _max_sharpe_w(mean: np.ndarray, cov: np.ndarray, rf_per: float) -> np.ndarray:
    """最大夏普：w ∝ Σ⁻¹·(μ-rf)。"""
    inv = np.linalg.pinv(cov)
    excess = mean - rf_per
    w = inv @ excess
    if not np.isfinite(w).all() or np.allclose(w, 0):
        return np.ones(cov.shape[0]) / cov.shape[0]
    return _long_only(w)


def _optimize_weights(prices, lookback, rebalance, solver) -> pd.DataFrame:
    """在调仓日用滚动窗口求解权重，产出目标权重矩阵。"""
    ret = prices.pct_change()
    n = len(prices)
    cols = list(prices.columns)
    target = pd.DataFrame(np.nan, index=prices.index, columns=cols)
    for i in range(lookback, n, rebalance):
        window = ret.iloc[i - lookback + 1 : i + 1].dropna(how="any")
        if len(window) < max(5, len(cols)):  # 样本不足以稳定估计协方差
            continue
        cov = np.cov(window.values, rowvar=False)
        mean = window.mean().values
        w = solver(mean, np.atleast_2d(cov))
        target.iloc[i] = pd.Series(w, index=cols)
    return target.ffill().fillna(0.0)


def min_variance(prices: pd.DataFrame, lookback: int = 60, rebalance: int = 20, **_ignored):
    """最小方差组合权重矩阵。"""
    return _optimize_weights(prices, lookback, rebalance, _min_variance_w)


def max_sharpe(
    prices: pd.DataFrame,
    lookback: int = 60,
    rebalance: int = 20,
    risk_free: float = 0.0,
    period: str = "1d",
    **_ignored,
):
    """最大夏普组合权重矩阵。"""
    rf_per = risk_free / periods_per_year(period)
    return _optimize_weights(
        prices, lookback, rebalance,
        lambda mean, cov: _max_sharpe_w(mean, cov, rf_per),
    )
