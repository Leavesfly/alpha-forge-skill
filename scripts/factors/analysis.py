"""因子研究平台：IC/IR、因子衰减、因子相关性与正交化（中性化）。

多因子选股的关键不是「拍脑袋选因子」，而是量化每个因子的预测力与独立性：
- IC（信息系数）：每期因子横截面值与未来收益横截面的秩相关，度量选股方向性；
- IC IR / t 值：IC 的稳定性与统计显著性；
- 因子衰减：不同预测跨度下 IC 的变化，判断因子有效的时间尺度；
- 因子相关性：识别冗余/拥挤的因子；
- 正交化：把目标因子对其他因子做横截面回归取残差，得到「增量」纯因子。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _forward_returns(prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """未来 horizon 期的前瞻收益（用于 IC；不参与净值，仅评估用）。"""
    return prices.shift(-horizon) / prices - 1.0


def _cross_sectional_corr(f_row: pd.Series, r_row: pd.Series, method: str) -> float:
    """单期横截面相关（spearman 用秩相关，pearson 用线性相关）。"""
    df = pd.DataFrame({"f": f_row, "r": r_row}).dropna()
    if len(df) < 3:
        return np.nan
    a, b = df["f"], df["r"]
    if method == "spearman":
        a, b = a.rank(), b.rank()
    if a.std(ddof=0) == 0 or b.std(ddof=0) == 0:
        return np.nan
    return float(a.corr(b))


def compute_ic(
    factor: pd.DataFrame,
    prices: pd.DataFrame,
    horizon: int = 5,
    method: str = "spearman",
) -> pd.Series:
    """计算逐期信息系数（IC）时间序列。

    Args:
        factor: 因子值矩阵（行=时间，列=标的）。
        prices: 收盘价矩阵（列与 factor 对齐）。
        horizon: 前瞻收益的周期数。
        method: ``spearman``（秩 IC，默认）或 ``pearson``。

    Returns:
        以时间为索引的 IC 序列（已去除无效期）。
    """
    cols = [c for c in factor.columns if c in prices.columns]
    fwd = _forward_returns(prices[cols], horizon)
    f = factor[cols]
    common_idx = f.index.intersection(fwd.index)
    ics = {
        t: _cross_sectional_corr(f.loc[t], fwd.loc[t], method) for t in common_idx
    }
    return pd.Series(ics).dropna().sort_index()


def ic_summary(ic: pd.Series) -> dict:
    """汇总 IC 序列：均值、波动、IR、t 值、胜率。"""
    ic = pd.Series(ic).dropna()
    n = len(ic)
    if n == 0:
        return {"ic_mean": 0.0, "ic_std": 0.0, "ic_ir": 0.0, "t_stat": 0.0,
                "hit_rate": 0.0, "n": 0}
    mean = float(ic.mean())
    std = float(ic.std(ddof=1)) if n > 1 else 0.0
    ir = mean / std if std > 0 else 0.0
    return {
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": ir,
        "t_stat": ir * np.sqrt(n),
        "hit_rate": float((ic > 0).mean()),
        "n": n,
    }


def factor_decay(
    factor: pd.DataFrame,
    prices: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    method: str = "spearman",
) -> pd.DataFrame:
    """因子衰减表：不同前瞻跨度下的 IC 均值与 IR。"""
    rows = []
    for h in horizons:
        summ = ic_summary(compute_ic(factor, prices, horizon=h, method=method))
        rows.append({"horizon": h, "ic_mean": summ["ic_mean"], "ic_ir": summ["ic_ir"]})
    return pd.DataFrame(rows).set_index("horizon")


def factor_correlation(
    factor_frames: dict[str, pd.DataFrame],
    method: str = "spearman",
) -> pd.DataFrame:
    """因子间平均横截面相关矩阵（识别冗余因子）。"""
    names = list(factor_frames)
    mat = pd.DataFrame(np.eye(len(names)), index=names, columns=names)
    for i, ni in enumerate(names):
        for j in range(i + 1, len(names)):
            nj = names[j]
            fi, fj = factor_frames[ni], factor_frames[nj]
            idx = fi.index.intersection(fj.index)
            corrs = [
                _cross_sectional_corr(fi.loc[t], fj.loc[t], method) for t in idx
            ]
            avg = float(np.nanmean(corrs)) if corrs else np.nan
            mat.loc[ni, nj] = mat.loc[nj, ni] = avg
    return mat


def neutralize(
    target: pd.DataFrame,
    others: list[pd.DataFrame],
) -> pd.DataFrame:
    """把目标因子对其他因子做逐期横截面回归，返回残差（正交化后的纯因子）。

    Args:
        target: 待中性化的因子矩阵。
        others: 作为解释变量的其他因子矩阵列表。

    Returns:
        与 target 同形的残差因子矩阵。
    """
    resid = pd.DataFrame(np.nan, index=target.index, columns=target.columns)
    for t in target.index:
        y = target.loc[t]
        cols = [o.loc[t] for o in others if t in o.index]
        stacked = pd.concat([y] + cols, axis=1).dropna()
        if len(stacked) < len(cols) + 2:
            continue
        yv = stacked.iloc[:, 0].to_numpy(dtype=float)
        Xv = stacked.iloc[:, 1:].to_numpy(dtype=float)
        A = np.column_stack([np.ones(len(yv)), Xv])
        coef, _, _, _ = np.linalg.lstsq(A, yv, rcond=None)
        r = yv - A @ coef
        resid.loc[t, stacked.index] = r
    return resid
