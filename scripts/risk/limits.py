"""风险约束：仓位暴露限制与回撤熔断。

- ``apply_exposure_limits``：对目标权重矩阵施加单票上限、总暴露(gross)与
  净暴露(net)上限，超限则等比缩放；用于组合/多因子的事前风控。
- ``drawdown_circuit_breaker``：路径依赖的回撤熔断——当策略净值自峰值回撤
  超过阈值时降杠杆（默认清仓），待创新高后恢复；用于单标的仓位的事中风控。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def apply_exposure_limits(
    weights: pd.DataFrame,
    max_weight: float | None = None,
    max_gross: float | None = None,
    max_net: float | None = None,
) -> pd.DataFrame:
    """对权重矩阵逐行施加暴露约束。

    Args:
        weights: 目标权重矩阵（行=时间，列=标的）。
        max_weight: 单标的权重绝对值上限（如 0.2）。
        max_gross: 总暴露上限 Σ|w|（如 1.0）。
        max_net: 净暴露上限 |Σw|（如 1.0）。

    Returns:
        约束后的权重矩阵（同形）。
    """
    w = weights.copy().fillna(0.0)

    if max_weight is not None:
        w = w.clip(lower=-max_weight, upper=max_weight)

    if max_gross is not None:
        gross = w.abs().sum(axis=1)
        scale = np.where(gross > max_gross, max_gross / gross.replace(0, np.nan), 1.0)
        w = w.mul(pd.Series(scale, index=w.index).fillna(1.0), axis=0)

    if max_net is not None:
        net = w.sum(axis=1)
        over = net.abs() > max_net
        if over.any():
            scale = np.where(
                over, max_net / net.abs().replace(0, np.nan), 1.0
            )
            w = w.mul(pd.Series(scale, index=w.index).fillna(1.0), axis=0)

    return w


def drawdown_circuit_breaker(
    positions: pd.Series,
    price_ret: pd.Series,
    threshold: float,
    deleverage: float = 0.0,
) -> pd.Series:
    """回撤熔断：净值回撤越限则降杠杆，创新高后恢复。

    以「毛收益」净值（仓位 × 价格收益，不含成本）作为熔断决策依据，
    路径依赖地逐 bar 计算实际仓位。

    Args:
        positions: 目标仓位序列（已 shift 到成交时间线）。
        price_ret: 逐周期价格收益率（与 positions 对齐）。
        threshold: 触发熔断的回撤阈值（正数，如 0.20 表示回撤 20%）。
        deleverage: 熔断后仓位缩放系数（0=清仓，0.5=减半）。

    Returns:
        熔断调整后的仓位 Series。
    """
    pos = positions.to_numpy(dtype=float)
    ret = price_ret.reindex(positions.index).fillna(0.0).to_numpy(dtype=float)
    n = len(pos)
    out = np.zeros(n)

    equity = 1.0
    peak = 1.0
    halted = False
    for t in range(n):
        scaled = pos[t] * (deleverage if halted else 1.0)
        out[t] = scaled
        equity *= 1.0 + scaled * ret[t]
        peak = max(peak, equity)
        dd = equity / peak - 1.0
        if dd <= -threshold:
            halted = True
        elif equity >= peak:  # 创新高，解除熔断
            halted = False
    return pd.Series(out, index=positions.index)
