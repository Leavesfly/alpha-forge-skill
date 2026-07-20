"""稳健性验证：过拟合诊断与显著性检验。

回测最大的谎言是「样本内挑出来的漂亮曲线」。本模块提供业界公认的
反过拟合工具（Bailey & López de Prado 等）：

- Probabilistic Sharpe Ratio (PSR)：在给定偏度/峰度与样本量下，
  夏普显著大于某基准的概率；
- Deflated Sharpe Ratio (DSR)：对「试了很多组参数」这一事实做惩罚后的 PSR，
  是判断寻优结果是否为运气的关键指标；
- Probability of Backtest Overfitting (PBO)：用组合对称交叉验证（CSCV）
  估计「样本内最优在样本外沦为下半区」的概率。

为保持零额外依赖，正态分布 CDF/PPF 在本模块内用 erf 与 Acklam 有理逼近实现。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

#: 欧拉-马歇罗尼常数（期望最大夏普推导用）
_EULER_GAMMA = 0.5772156649015329


# ----------------------------------------------------------------- 正态分布


def norm_cdf(x: float) -> float:
    """标准正态累积分布函数（基于 erf，无需 scipy）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """标准正态分位函数（Acklam 有理逼近，精度约 1e-9）。"""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf

    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]

    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ----------------------------------------------------------------- 夏普统计


@dataclass
class SharpeStats:
    """单条收益序列的夏普相关统计（均为逐周期口径）。"""

    sharpe: float  # 逐周期夏普（非年化）
    skew: float
    kurtosis: float  # 非超额峰度（正态=3）
    n: int


def sharpe_stats(returns: pd.Series | np.ndarray) -> SharpeStats:
    """计算逐周期夏普、偏度、峰度与样本量。"""
    r = pd.Series(returns).dropna().to_numpy(dtype=float)
    n = len(r)
    if n < 2 or r.std(ddof=1) == 0:
        return SharpeStats(sharpe=0.0, skew=0.0, kurtosis=3.0, n=n)
    mean = r.mean()
    std = r.std(ddof=1)
    sr = mean / std
    # 偏度与峰度（非超额，正态=3）
    m2 = ((r - mean) ** 2).mean()
    m3 = ((r - mean) ** 3).mean()
    m4 = ((r - mean) ** 4).mean()
    skew = m3 / m2 ** 1.5 if m2 > 0 else 0.0
    kurt = m4 / m2 ** 2 if m2 > 0 else 3.0
    return SharpeStats(sharpe=float(sr), skew=float(skew), kurtosis=float(kurt), n=n)


# --------------------------------------------------------------- PSR / DSR


def probabilistic_sharpe_ratio(
    sr: float,
    sr_benchmark: float,
    n: int,
    skew: float,
    kurtosis: float,
) -> float:
    """PSR：观测夏普显著超过基准夏普的概率。

    Args:
        sr: 估计的逐周期夏普。
        sr_benchmark: 基准夏普（如 0）。
        n: 样本量。
        skew: 收益偏度。
        kurtosis: 收益峰度（非超额，正态=3）。
    """
    if n < 2:
        return 0.0
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / denom
    return norm_cdf(z)


def expected_max_sharpe(sr_std: float, n_trials: int) -> float:
    """在 n_trials 次独立试验下，纯噪声可期望达到的最大逐周期夏普。

    E[max SR] ≈ σ_SR · [(1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))]
    """
    if n_trials < 2 or sr_std <= 0:
        return 0.0
    return sr_std * (
        (1.0 - _EULER_GAMMA) * norm_ppf(1.0 - 1.0 / n_trials)
        + _EULER_GAMMA * norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe_ratio(
    trial_sharpes: np.ndarray | list[float],
    n: int,
    skew: float,
    kurtosis: float,
) -> dict:
    """DSR：对多重检验做惩罚后的 PSR。

    Args:
        trial_sharpes: 所有试验（参数组合）的逐周期夏普数组。
        n: 最优策略的样本量。
        skew/kurtosis: 最优策略收益的偏度/峰度。

    Returns:
        含 dsr、期望最大夏普 sr_star、最优夏普 sr_max、试验数的字典。
    """
    arr = np.asarray(trial_sharpes, dtype=float)
    arr = arr[~np.isnan(arr)]
    n_trials = len(arr)
    if n_trials == 0:
        return {"dsr": 0.0, "sr_star": 0.0, "sr_max": 0.0, "n_trials": 0}
    sr_max = float(arr.max())
    sr_std = float(arr.std(ddof=1)) if n_trials > 1 else 0.0
    sr_star = expected_max_sharpe(sr_std, n_trials)
    dsr = probabilistic_sharpe_ratio(sr_max, sr_star, n, skew, kurtosis)
    return {
        "dsr": dsr,
        "sr_star": sr_star,
        "sr_max": sr_max,
        "n_trials": n_trials,
    }


# ----------------------------------------------------------------- PBO/CSCV


def probability_of_backtest_overfitting(
    returns_matrix: pd.DataFrame,
    n_splits: int = 10,
) -> dict:
    """用组合对称交叉验证（CSCV）估计过拟合概率 PBO。

    Args:
        returns_matrix: 逐周期收益矩阵（行=周期，列=参数组合/策略）。
        n_splits: 将时间轴等分的块数 S（需为偶数）；组合数为 C(S, S/2)。

    Returns:
        含 pbo、logits 列表、样本外表现退化统计的字典。
    """
    R = returns_matrix.dropna(how="any")
    n_configs = R.shape[1]
    if n_configs < 2:
        raise ValueError("PBO 至少需要 2 个参数组合的收益列。")
    if n_splits % 2 != 0:
        n_splits += 1

    T = len(R)
    if T < n_splits:
        raise ValueError(f"周期数 {T} 少于分块数 {n_splits}，无法做 CSCV。")

    # 等分为 S 块（末块并入余数）
    bounds = np.linspace(0, T, n_splits + 1, dtype=int)
    groups = [np.arange(bounds[i], bounds[i + 1]) for i in range(n_splits)]
    arr = R.to_numpy()

    def _sharpe(idx: np.ndarray) -> np.ndarray:
        sub = arr[idx]
        mean = sub.mean(axis=0)
        std = sub.std(axis=0, ddof=1)
        std = np.where(std == 0, np.nan, std)
        return mean / std

    logits: list[float] = []
    half = n_splits // 2
    for combo in combinations(range(n_splits), half):
        is_idx = np.concatenate([groups[g] for g in combo])
        oos_idx = np.concatenate(
            [groups[g] for g in range(n_splits) if g not in combo]
        )
        is_perf = _sharpe(is_idx)
        oos_perf = _sharpe(oos_idx)
        if np.all(np.isnan(is_perf)):
            continue
        best = int(np.nanargmax(is_perf))
        # 最优 IS 组合在 OOS 的相对排名 ω（1=最好）
        oos = np.nan_to_num(oos_perf, nan=-np.inf)
        rank = (oos < oos[best]).sum() + 1  # 有多少个比它差 + 1
        omega = rank / (n_configs + 1)
        omega = min(max(omega, 1e-6), 1 - 1e-6)
        logits.append(math.log(omega / (1.0 - omega)))

    if not logits:
        return {"pbo": 0.0, "n_combinations": 0, "logits": []}

    logits_arr = np.array(logits)
    pbo = float((logits_arr <= 0).mean())  # λ<=0 即 OOS 落入下半区
    return {
        "pbo": pbo,
        "n_combinations": len(logits),
        "median_logit": float(np.median(logits_arr)),
        "logits": logits,
    }
