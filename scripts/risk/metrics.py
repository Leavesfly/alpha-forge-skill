"""组合级风险度量：VaR / CVaR / 下行风险 / 尾部与溃疡指数。

这些指标补足原 metrics 只覆盖夏普/回撤的短板，聚焦「亏损的形状」：
- 历史/参数法 VaR：给定置信度下的单周期最大可能亏损；
- CVaR（期望损失/ES）：超过 VaR 的平均亏损，对肥尾更敏感；
- 下行偏差、尾部比率、溃疡指数：从不同角度刻画左尾与回撤痛苦度。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.metrics import periods_per_year
from research.validation import norm_ppf


def _clean(returns: pd.Series | np.ndarray) -> np.ndarray:
    return pd.Series(returns).dropna().to_numpy(dtype=float)


def value_at_risk(returns, level: float = 0.95) -> float:
    """历史模拟法 VaR（正值表示潜在亏损比例）。

    Args:
        returns: 逐周期收益率。
        level: 置信度（如 0.95 表示 95% VaR）。
    """
    r = _clean(returns)
    if len(r) == 0:
        return 0.0
    q = np.quantile(r, 1.0 - level)
    return float(-q)


def conditional_var(returns, level: float = 0.95) -> float:
    """条件 VaR / 期望损失（ES）：尾部亏损的平均值（正值）。"""
    r = _clean(returns)
    if len(r) == 0:
        return 0.0
    threshold = np.quantile(r, 1.0 - level)
    tail = r[r <= threshold]
    if len(tail) == 0:
        return float(-threshold)
    return float(-tail.mean())


def parametric_var(returns, level: float = 0.95) -> float:
    """参数法（高斯）VaR：假设正态分布，用均值/波动推导。"""
    r = _clean(returns)
    if len(r) < 2:
        return 0.0
    mu, sigma = r.mean(), r.std(ddof=1)
    z = norm_ppf(1.0 - level)
    return float(-(mu + z * sigma))


def downside_deviation(returns, mar: float = 0.0) -> float:
    """下行偏差：低于最低可接受收益（MAR）部分的均方根。"""
    r = _clean(returns)
    if len(r) == 0:
        return 0.0
    downside = np.minimum(r - mar, 0.0)
    return float(np.sqrt((downside ** 2).mean()))


def tail_ratio(returns) -> float:
    """尾部比率：右尾(95分位)与左尾(5分位)绝对值之比，>1 偏正。"""
    r = _clean(returns)
    if len(r) == 0:
        return 0.0
    left = abs(np.quantile(r, 0.05))
    right = abs(np.quantile(r, 0.95))
    return float(right / left) if left > 0 else 0.0


def ulcer_index(equity: pd.Series) -> float:
    """溃疡指数：回撤深度的均方根，衡量「亏损的持续痛苦」。"""
    eq = pd.Series(equity).dropna()
    if len(eq) == 0:
        return 0.0
    dd = eq / eq.cummax() - 1.0
    return float(np.sqrt((dd ** 2).mean()))


def annualized_var(returns, level: float = 0.95, period: str = "1d") -> float:
    """把单周期参数法 VaR 按 √T 年化，便于跨周期比较。"""
    v = parametric_var(returns, level)
    return float(v * np.sqrt(periods_per_year(period)))


def risk_report(
    returns: pd.Series,
    equity: pd.Series | None = None,
    level: float = 0.95,
    period: str = "1d",
) -> dict:
    """汇总风险指标为字典。"""
    if equity is None:
        equity = (1.0 + pd.Series(returns).fillna(0.0)).cumprod()
    return {
        "var": value_at_risk(returns, level),
        "cvar": conditional_var(returns, level),
        "parametric_var": parametric_var(returns, level),
        "annualized_var": annualized_var(returns, level, period),
        "downside_deviation": downside_deviation(returns),
        "tail_ratio": tail_ratio(returns),
        "ulcer_index": ulcer_index(equity),
        "level": level,
    }
