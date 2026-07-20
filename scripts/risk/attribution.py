"""业绩归因：收益贡献分解与因子回归归因。

- ``return_contribution``：把组合收益拆到各标的（权重 × 标的收益的累计贡献），
  回答「赚/亏的钱主要来自谁」。
- ``factor_attribution``：把组合收益对一组因子/基准收益做 OLS 回归，得到
  alpha、各因子 beta 与 R²，回答「收益里有多少是暴露于已知因子换来的」。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def return_contribution(
    weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
) -> pd.Series:
    """各标的对组合总收益的贡献（近似，按逐周期加权收益累加）。

    Args:
        weights: 持仓权重矩阵（应为已生效的持仓，行=时间，列=标的）。
        asset_returns: 标的逐周期收益矩阵（与 weights 同形/可对齐）。

    Returns:
        每个标的的累计贡献 Series（近似满足 Σ贡献 ≈ 组合累计收益）。
    """
    w = weights.fillna(0.0)
    r = asset_returns.reindex(index=w.index, columns=w.columns).fillna(0.0)
    contrib = (w * r).sum(axis=0)
    return contrib.sort_values(ascending=False)


def factor_attribution(
    portfolio_returns: pd.Series,
    factor_returns: pd.DataFrame,
) -> dict:
    """对组合收益做多因子 OLS 回归归因。

    模型：r_p = alpha + Σ beta_i · f_i + ε（用 numpy 最小二乘，无需 statsmodels）。

    Args:
        portfolio_returns: 组合逐周期收益。
        factor_returns: 因子逐周期收益矩阵（列=因子名）。

    Returns:
        含 alpha、各因子 beta、r_squared 的字典。
    """
    y = pd.Series(portfolio_returns).dropna()
    X = factor_returns.reindex(y.index).dropna()
    y = y.reindex(X.index)
    if len(y) < X.shape[1] + 2:
        raise ValueError("样本量不足以稳定回归，请提供更长的收益序列。")

    factor_names = list(X.columns)
    A = np.column_stack([np.ones(len(X)), X.to_numpy(dtype=float)])
    yv = y.to_numpy(dtype=float)

    coef, _, _, _ = np.linalg.lstsq(A, yv, rcond=None)
    resid = yv - A @ coef
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "alpha": float(coef[0]),
        "betas": {name: float(b) for name, b in zip(factor_names, coef[1:])},
        "r_squared": float(r2),
        "n": int(len(y)),
    }
