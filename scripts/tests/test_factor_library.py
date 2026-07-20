"""因子库回归测试：新增价格因子的方向语义与形状。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.library import FACTORS, PRICE_FACTORS, compute_factor


def _prices(n: int = 120) -> pd.DataFrame:
    """三标的确定性行情：A 平稳上行、B 大涨大跌高波动、C 近月超跌。"""
    dates = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(9)
    a = 100.0 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.004, n)))
    b = 100.0 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.04, n)))
    c = np.concatenate([
        100.0 * np.exp(np.cumsum(0.001 + rng.normal(0, 0.004, n - 21))),
        np.linspace(100, 70, 21),  # 近 21 日单边下跌
    ])
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=dates)


def test_new_price_factors_registered():
    """新增因子已注册为 price 类，纳入 PRICE_FACTORS。"""
    for name in ("reversal", "sharpe_mom", "consistency"):
        assert name in FACTORS
        assert FACTORS[name].category == "price"
        assert name in PRICE_FACTORS


def test_factor_shapes_match_prices():
    prices = _prices()
    for name in ("reversal", "sharpe_mom", "consistency"):
        f = compute_factor(name, prices, None, lookback=60)
        assert f.shape == prices.shape


def test_reversal_prefers_oversold():
    """短期反转：近月超跌的 C 得分应最高。"""
    prices = _prices()
    f = compute_factor("reversal", prices, None, lookback=60)
    last = f.iloc[-1]
    assert last["C"] == last.max()


def test_sharpe_mom_prefers_smooth_uptrend():
    """风险调整动量：平稳上行的 A 应高于高波动的 B。"""
    prices = _prices()
    f = compute_factor("sharpe_mom", prices, None, lookback=60)
    last = f.iloc[-1]
    assert last["A"] > last["B"]


def test_consistency_bounded_zero_one():
    """趋势一致性：取值在 [0, 1] 区间。"""
    prices = _prices()
    f = compute_factor("consistency", prices, None, lookback=60).dropna(how="all")
    assert float(f.min().min()) >= 0.0
    assert float(f.max().max()) <= 1.0


def test_unknown_factor_raises():
    with pytest.raises(KeyError):
        compute_factor("no_such_factor", _prices(), None)
