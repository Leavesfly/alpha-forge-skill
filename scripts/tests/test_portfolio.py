"""组合/轮动模块回归测试：权重生成、调仓节奏与暴露约束。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portfolio.rotation import (
    equal_weight,
    get_weights,
    inverse_vol,
    momentum_rotation,
)
from risk.limits import apply_exposure_limits


def _prices(n: int = 120) -> pd.DataFrame:
    """三标的确定性行情：A 上行、B 下行、C 低波动横盘。"""
    dates = pd.bdate_range("2020-01-01", periods=n)
    up = 100.0 * (1.01) ** np.arange(n)
    down = 100.0 * (0.99) ** np.arange(n)
    flat = 100.0 + 0.1 * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    return pd.DataFrame({"A": up, "B": down, "C": flat}, index=dates)


# ------------------------------------------------------------ 轮动权重


def test_momentum_picks_strongest():
    """动量 top-1：应始终持有涨幅最高的 A，且 warmup 前空仓。"""
    prices = _prices()
    w = momentum_rotation(prices, lookback=20, top_k=1, rebalance=10)
    assert w.shape == prices.shape
    assert (w.iloc[:20] == 0).all().all()  # 动量未形成前空仓
    tail = w.iloc[30:]
    assert (tail["A"] == 1.0).all()
    assert (tail[["B", "C"]] == 0.0).all().all()


def test_momentum_long_only_positive_stays_flat():
    """全市场下跌且只做正动量时应保持空仓。"""
    n = 120
    dates = pd.bdate_range("2020-01-01", periods=n)
    prices = pd.DataFrame(
        {
            "A": 100.0 * (0.99) ** np.arange(n),
            "B": 100.0 * (0.98) ** np.arange(n),
        },
        index=dates,
    )
    w = momentum_rotation(prices, lookback=20, top_k=1, rebalance=10)
    assert (w == 0).all().all()


def test_momentum_rebalance_cadence():
    """权重只在调仓日变化：变化行数不超过调仓日数量。"""
    prices = _prices()
    rebalance = 10
    w = momentum_rotation(prices, lookback=20, top_k=2, rebalance=rebalance)
    changes = (w.diff().abs().sum(axis=1) > 1e-12).sum()
    n_rebalances = len(range(20, len(prices), rebalance))
    assert changes <= n_rebalances


def test_equal_weight_sums_to_one():
    prices = _prices()
    w = equal_weight(prices, rebalance=20)
    active = w.iloc[5:]
    assert np.allclose(active.to_numpy(), 1.0 / 3.0)
    assert np.allclose(active.sum(axis=1).to_numpy(), 1.0)


def test_inverse_vol_prefers_low_vol():
    """逆波动率：波动越低权重越高，行和为 1。"""
    n = 120
    dates = pd.bdate_range("2020-01-01", periods=n)

    def _alt(mag: float) -> np.ndarray:
        ret = mag * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        return 100.0 * np.cumprod(1.0 + ret)

    prices = pd.DataFrame(
        {"A": _alt(0.02), "B": _alt(0.01), "C": _alt(0.002)}, index=dates
    )
    w = inverse_vol(prices, lookback=20, rebalance=10)
    tail = w.iloc[30:]
    assert np.allclose(tail.sum(axis=1).to_numpy(), 1.0)
    assert (tail["C"] > tail["B"]).all()
    assert (tail["B"] > tail["A"]).all()


def test_get_weights_unknown_name_raises():
    with pytest.raises(KeyError):
        get_weights("no_such_rotation", _prices())


# ------------------------------------------------------------ HRP / 最小 CVaR


def _noisy_prices(n: int = 160, seed: int = 5) -> pd.DataFrame:
    """四标的随机行情：D 波动明显高于其他。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    data = {
        "A": 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.010, n))),
        "B": 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n))),
        "C": 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.011, n))),
        "D": 100.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.045, n))),
    }
    return pd.DataFrame(data, index=dates)


def test_hrp_weights_valid_and_derisk_high_vol():
    """HRP：权重非负、行和为 1，且高波动标的权重低于等权。"""
    prices = _noisy_prices()
    w = get_weights("hrp", prices, lookback=60, rebalance=20)
    tail = w.iloc[80:]
    assert (tail >= -1e-12).all().all()
    assert np.allclose(tail.sum(axis=1).to_numpy(), 1.0)
    assert (tail["D"] < 0.25).all()  # 高波动标的被降权


def test_min_cvar_weights_valid_and_derisk_high_vol():
    """最小 CVaR：权重非负、行和为 1，高波动标的被降权。"""
    prices = _noisy_prices()
    w = get_weights("min_cvar", prices, lookback=60, rebalance=20, cvar_alpha=0.95)
    tail = w.iloc[80:]
    assert (tail >= -1e-9).all().all()
    assert np.allclose(tail.sum(axis=1).to_numpy(), 1.0)
    assert (tail["D"] < 0.25).all()


def test_hrp_rebalance_cadence():
    """HRP 权重只在调仓日变化。"""
    prices = _noisy_prices()
    rebalance = 20
    w = get_weights("hrp", prices, lookback=60, rebalance=rebalance)
    changes = (w.diff().abs().sum(axis=1) > 1e-12).sum()
    assert changes <= len(range(60, len(prices), rebalance))


# ------------------------------------------------------------ 暴露约束


def _row(**cols) -> pd.DataFrame:
    return pd.DataFrame([cols])


def test_exposure_max_weight_clips():
    w = apply_exposure_limits(_row(A=0.5, B=-0.4), max_weight=0.3)
    assert w.iloc[0]["A"] == pytest.approx(0.3)
    assert w.iloc[0]["B"] == pytest.approx(-0.3)


def test_exposure_max_gross_scales_proportionally():
    w = apply_exposure_limits(_row(A=0.8, B=0.8), max_gross=1.0)
    assert w.iloc[0]["A"] == pytest.approx(0.5)
    assert w.iloc[0]["B"] == pytest.approx(0.5)
    assert w.abs().sum(axis=1).iloc[0] == pytest.approx(1.0)


def test_exposure_max_net_scales_down():
    w = apply_exposure_limits(_row(A=0.8, B=0.4), max_net=1.0)
    assert w.sum(axis=1).iloc[0] == pytest.approx(1.0)
    # 等比缩放保持相对比例
    assert w.iloc[0]["A"] / w.iloc[0]["B"] == pytest.approx(2.0)


def test_exposure_within_limits_unchanged():
    """未超限的权重不应被改动。"""
    original = _row(A=0.3, B=-0.2)
    w = apply_exposure_limits(original, max_weight=0.5, max_gross=1.0, max_net=1.0)
    pd.testing.assert_frame_equal(w, original)
