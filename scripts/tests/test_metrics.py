"""绩效指标回归测试。

用手工可验证的构造数据，锁定指标计算的正确性。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import (
    compute_metrics,
    max_drawdown,
    max_drawdown_duration,
    omega_ratio,
    periods_per_year,
    relative_metrics,
)


def test_max_drawdown_known_series():
    """净值 1 -> 1.5 -> 0.75 的最大回撤应为 50%。"""
    equity = pd.Series([1.0, 1.5, 0.75, 1.2])
    assert max_drawdown(equity) == pytest.approx(0.5)


def test_max_drawdown_monotonic_up_is_zero():
    equity = pd.Series([1.0, 1.1, 1.2, 1.3])
    assert max_drawdown(equity) == pytest.approx(0.0)


def test_total_return_matches_equity_endpoint():
    returns = pd.Series([0.10, -0.05, 0.02])
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d")
    assert m["total_return"] == pytest.approx(equity.iloc[-1] - 1.0)


def test_sharpe_zero_for_constant_returns():
    """收益率无波动时夏普应为 0（避免除零）。"""
    returns = pd.Series([0.001] * 50)
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d")
    assert m["sharpe"] == 0.0


def test_annualization_factor_lookup():
    assert periods_per_year("1d") == 240
    assert periods_per_year("1w") == 52
    assert periods_per_year("1M") == 12
    # 未知周期退化为 240
    assert periods_per_year("unknown") == 240


def test_annual_volatility_scales_with_sqrt_periods():
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0, 0.01, 240))
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d")
    expected = returns.std(ddof=0) * np.sqrt(240)
    assert m["annual_volatility"] == pytest.approx(expected)


def test_trade_stats_counts_and_win_rate():
    """两笔交易：一盈一亏，胜率应为 0.5。"""
    # 持仓区间 1：index 1-2；区间 2：index 4-5
    positions = pd.Series([0, 1, 1, 0, 1, 1, 0], dtype=float)
    returns = pd.Series([0, 0.05, 0.05, 0, -0.03, -0.02, 0], dtype=float)
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d", positions=positions)
    assert m["num_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)


def test_calmar_zero_when_no_drawdown():
    returns = pd.Series([0.001] * 30)
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d")
    # 无回撤 -> 卡玛比率安全置 0（避免除零）
    assert m["calmar"] == 0.0


def test_omega_ratio_known_values():
    """收益 [0.02, -0.01]：欧米茄 = 0.02 / 0.01 = 2。"""
    returns = pd.Series([0.02, -0.01])
    assert omega_ratio(returns) == pytest.approx(2.0)
    # 全盈利时封顶而非 inf
    assert omega_ratio(pd.Series([0.01, 0.02])) == pytest.approx(999.0)


def test_max_drawdown_duration_known_series():
    """净值低于前高的最长连续段：index 1-2 共 2 个周期。"""
    equity = pd.Series([1.0, 0.9, 0.95, 1.1, 1.0])
    assert max_drawdown_duration(equity) == 2
    assert max_drawdown_duration(pd.Series([1.0, 1.1, 1.2])) == 0


def test_profit_factor_from_trades():
    """一盈一亏：盈亏比 = 毛利 / 毛损。"""
    positions = pd.Series([0, 1, 1, 0, 1, 1, 0], dtype=float)
    returns = pd.Series([0, 0.05, 0.05, 0, -0.03, -0.02, 0], dtype=float)
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d", positions=positions)
    win = 1.05 * 1.05 - 1.0
    loss = 1.0 - 0.97 * 0.98
    assert m["profit_factor"] == pytest.approx(win / loss)


def test_compute_metrics_contains_new_keys():
    """新增指标键存在且为原生 float/int（JSON 友好）。"""
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0, 0.01, 100))
    equity = (1.0 + returns).cumprod()
    m = compute_metrics(returns, equity, period="1d")
    for key in ("omega", "max_dd_duration", "profit_factor", "skew", "kurtosis"):
        assert key in m


def test_relative_metrics_identical_series():
    """策略与基准完全相同：IR/TE 为 0，beta 为 1，alpha ≈ 0。"""
    rng = np.random.default_rng(2)
    b = pd.Series(rng.normal(0.0005, 0.01, 200))
    rel = relative_metrics(b, b, period="1d")
    assert rel["information_ratio"] == 0.0
    assert rel["tracking_error"] == 0.0
    assert rel["beta"] == pytest.approx(1.0)
    assert rel["alpha"] == pytest.approx(0.0, abs=1e-9)


def test_relative_metrics_scaled_beta():
    """r = 2b 时 beta 应为 2。"""
    rng = np.random.default_rng(3)
    b = pd.Series(rng.normal(0.0, 0.01, 300))
    rel = relative_metrics(2.0 * b, b, period="1d")
    assert rel["beta"] == pytest.approx(2.0)


def test_relative_metrics_insufficient_sample():
    rel = relative_metrics(pd.Series([0.01]), pd.Series([0.02]))
    assert rel == {
        "information_ratio": 0.0, "tracking_error": 0.0, "beta": 0.0, "alpha": 0.0,
    }
