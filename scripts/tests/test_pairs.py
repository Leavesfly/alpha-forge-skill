"""配对交易回归测试：对冲比率、价差 z-score 状态机与市场中性权重。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pairs.select import half_life, hedge_ratio
from pairs.strategy import pair_signals, pair_spread, pair_weights


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2020-01-01", periods=n)


def test_hedge_ratio_recovers_slope():
    """log_a = 0.3 + 2·log_b 的确定性关系应解出 beta=2。"""
    log_b = pd.Series(np.linspace(1.0, 2.0, 100))
    log_a = 0.3 + 2.0 * log_b
    assert hedge_ratio(log_a, log_b) == pytest.approx(2.0, abs=1e-9)


def test_pair_spread_zero_for_exact_relation():
    """A = B^2（即 log A = 2 log B）时，beta=2 的价差应恒为 0。"""
    b = np.linspace(10.0, 20.0, 50)
    prices = pd.DataFrame({"A": b**2, "B": b}, index=_dates(50))
    spread = pair_spread(prices, "A", "B", beta=2.0)
    assert np.allclose(spread.to_numpy(), 0.0, atol=1e-12)


def _noise_spread(n: int, spikes: dict[int, float]) -> pd.Series:
    """交替 ±0.01 的基准价差，指定位置替换为尖峰值。"""
    base = np.where(np.arange(n) % 2 == 0, 0.01, -0.01).astype(float)
    for pos, v in spikes.items():
        base[pos] = v
    return pd.Series(base, index=_dates(n))


def test_pair_signals_open_close_state_machine():
    """z-score 深度负偏开多价差、回归均值后平仓；深度正偏开空。"""
    lookback = 20
    spread = _noise_spread(120, {60: -1.0, 90: 1.0})
    pos = pair_signals(spread, lookback=lookback, entry=2.0, exit=0.5, stop=1000.0)

    # z-score 未形成前空仓
    assert (pos.iloc[: lookback - 1] == 0).all()
    # 负尖峰（z << -entry）开多价差
    assert pos.iloc[60] == 1.0
    # 价差回归后 |z| <= exit，平仓
    assert pos.iloc[70] == 0.0
    # 正尖峰（z >= entry）开空价差
    assert pos.iloc[90] == -1.0
    assert set(np.unique(pos.to_numpy())).issubset({-1.0, 0.0, 1.0})


def test_pair_signals_stop_loss_closes_position():
    """持仓后 |z| 突破 stop 应止损平仓。"""
    # 60 处开多价差；62 处出现更极端的负值触发止损
    spread = _noise_spread(120, {60: -0.5, 62: -5.0})
    pos = pair_signals(spread, lookback=20, entry=2.0, exit=0.1, stop=4.0)
    assert pos.iloc[60] == 1.0  # 开仓
    assert pos.iloc[62] == 0.0  # |z| >= stop 止损


def test_pair_weights_market_neutral():
    """两腿权重：A=+pos/2、B=-pos/2，逐行净暴露为 0。"""
    n = 30
    prices = pd.DataFrame(
        {"A": np.full(n, 10.0), "B": np.full(n, 20.0)}, index=_dates(n)
    )
    position = pd.Series(0.0, index=prices.index)
    position.iloc[5:10] = 1.0
    position.iloc[15:20] = -1.0

    weights = pair_weights(prices, "A", "B", position)
    pd.testing.assert_series_equal(
        weights["A"], position * 0.5, check_names=False
    )
    pd.testing.assert_series_equal(
        weights["B"], -position * 0.5, check_names=False
    )
    assert np.allclose(weights.sum(axis=1).to_numpy(), 0.0)
    # 总杠杆：持仓期 1.0、空仓期 0
    gross = weights.abs().sum(axis=1)
    assert gross.iloc[7] == pytest.approx(1.0)
    assert gross.iloc[0] == 0.0


def test_half_life_mean_reverting_series():
    """强均值回复的 AR(1) 价差应给出有限且较短的半衰期。"""
    rng = np.random.default_rng(7)
    n = 500
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = 0.5 * spread[t - 1] + rng.normal(0, 0.01)
    hl = half_life(pd.Series(spread))
    assert np.isfinite(hl)
    assert 0 < hl < 5  # rho=-0.5 理论半衰期 = ln2/ln2 = 1

    # 单调发散（无回复，rho >= 0）应返回 inf
    diverging = pd.Series(np.linspace(0.0, 1.0, 100))
    assert not np.isfinite(half_life(diverging))
