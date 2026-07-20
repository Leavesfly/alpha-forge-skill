"""组合优化：最小方差 / 最大夏普 / HRP / 最小 CVaR 权重。

在每个调仓日用过去 lookback 窗口的收益估计协方差/均值，求解目标权重，
非调仓日前向填充。仅做多约束通过「负权重截断后归一化」近似（非严格 QP）。
HRP（层次风险平价）不求逆协方差矩阵，对估计误差更稳健；最小 CVaR 直接
优化历史尾部均值亏损（Rockafellar-Uryasev 经验估计，SLSQP 求解）。
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


def _cluster_var(cov: np.ndarray, idx: list[int]) -> float:
    """簇内逆方差权重下的簇方差。"""
    sub = cov[np.ix_(idx, idx)]
    diag = np.diag(sub).copy()
    diag[diag <= 0] = diag[diag > 0].min() if (diag > 0).any() else 1.0
    ivp = 1.0 / diag
    ivp /= ivp.sum()
    return float(ivp @ sub @ ivp)


def _hrp_w(window: pd.DataFrame) -> np.ndarray:
    """HRP（层次风险平价）：相关距离聚类 + 准对角化 + 递归二分。

    不求逆协方差矩阵，对估计误差比 min_variance/max_sharpe 更稳健
    （López de Prado, 2016）。聚类失败时退化为逆方差权重。
    """
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform

    cov = window.cov().values
    corr = window.corr().fillna(0.0).values
    n = cov.shape[0]
    if n == 1:
        return np.ones(1)
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
    np.fill_diagonal(dist, 0.0)
    try:
        link = linkage(squareform(dist, checks=False), method="single")
        order = [int(i) for i in leaves_list(link)]
    except Exception:
        diag = np.diag(cov).copy()
        diag[diag <= 0] = 1.0
        ivp = 1.0 / diag
        return ivp / ivp.sum()

    w = np.ones(n)
    clusters: list[list[int]] = [order]
    while clusters:
        # 每个长度 > 1 的簇对半切分，成对处理
        clusters = [
            half
            for c in clusters
            if len(c) > 1
            for half in (c[: len(c) // 2], c[len(c) // 2 :])
        ]
        for i in range(0, len(clusters), 2):
            left, right = clusters[i], clusters[i + 1]
            var_l, var_r = _cluster_var(cov, left), _cluster_var(cov, right)
            alpha = 1.0 - var_l / (var_l + var_r) if var_l + var_r > 0 else 0.5
            w[left] *= alpha
            w[right] *= 1.0 - alpha
    return _long_only(w)


def _min_cvar_w(window: pd.DataFrame, alpha: float) -> np.ndarray:
    """最小 CVaR：SLSQP 最小化历史尾部（1-alpha 分位以下）均值亏损。

    优化失败时退化为等权。
    """
    from scipy.optimize import minimize

    rets = window.values
    n = rets.shape[1]
    if n == 1:
        return np.ones(1)

    def cvar(w: np.ndarray) -> float:
        pr = rets @ w
        q = np.quantile(pr, 1.0 - alpha)
        tail = pr[pr <= q]
        return float(-tail.mean()) if len(tail) else 0.0

    res = minimize(
        cvar,
        np.ones(n) / n,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * n,
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1.0},
        options={"maxiter": 200, "ftol": 1e-9},
    )
    w = res.x if res.success and np.isfinite(res.x).all() else np.ones(n) / n
    return _long_only(w)


def _optimize_weights_window(prices, lookback, rebalance, solver) -> pd.DataFrame:
    """同 _optimize_weights，但 solver 直接接收收益窗口 DataFrame。"""
    ret = prices.pct_change()
    n = len(prices)
    cols = list(prices.columns)
    target = pd.DataFrame(np.nan, index=prices.index, columns=cols)
    for i in range(lookback, n, rebalance):
        window = ret.iloc[i - lookback + 1 : i + 1].dropna(how="any")
        if len(window) < max(5, len(cols)):
            continue
        target.iloc[i] = pd.Series(solver(window), index=cols)
    return target.ffill().fillna(0.0)


def hrp(prices: pd.DataFrame, lookback: int = 60, rebalance: int = 20, **_ignored):
    """HRP（层次风险平价）组合权重矩阵。"""
    return _optimize_weights_window(prices, lookback, rebalance, _hrp_w)


def min_cvar(
    prices: pd.DataFrame,
    lookback: int = 60,
    rebalance: int = 20,
    cvar_alpha: float = 0.95,
    **_ignored,
):
    """最小 CVaR（尾部风险最小）组合权重矩阵。"""
    return _optimize_weights_window(
        prices, lookback, rebalance, lambda w: _min_cvar_w(w, cvar_alpha)
    )
