"""稳健性验证测试：正态分布工具、PSR/DSR、PBO、走步。"""

from __future__ import annotations

import numpy as np
import pytest

from research.validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    norm_cdf,
    norm_ppf,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_stats,
)
from research.walk_forward import walk_forward
from strategies.ma_cross import MACrossStrategy

from tests.helpers import make_ohlcv


# ------------------------------------------------------------- 正态分布工具


def test_norm_cdf_known_values():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_norm_ppf_inverse_of_cdf():
    for p in (0.01, 0.25, 0.5, 0.75, 0.99):
        assert norm_cdf(norm_ppf(p)) == pytest.approx(p, abs=1e-6)


# ------------------------------------------------------------- 夏普统计


def test_sharpe_stats_positive_drift():
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 500)
    stats = sharpe_stats(r)
    assert stats.n == 500
    assert stats.sharpe > 0  # 正漂移


# ------------------------------------------------------------- PSR / DSR


def test_psr_increases_with_sharpe():
    low = probabilistic_sharpe_ratio(0.05, 0.0, 250, 0.0, 3.0)
    high = probabilistic_sharpe_ratio(0.20, 0.0, 250, 0.0, 3.0)
    assert 0.0 <= low <= 1.0 and 0.0 <= high <= 1.0
    assert high > low


def test_expected_max_sharpe_grows_with_trials():
    e10 = expected_max_sharpe(0.1, 10)
    e100 = expected_max_sharpe(0.1, 100)
    assert e100 > e10 > 0


def test_deflated_sharpe_penalizes_many_trials():
    """同一最优夏普，试验越多，DSR 越低（越可能是运气）。"""
    rng = np.random.default_rng(0)
    few = np.abs(rng.normal(0, 0.05, 5)) + 0.1
    many = np.concatenate([few, rng.normal(0, 0.05, 200)])
    d_few = deflated_sharpe_ratio(few, n=500, skew=0.0, kurtosis=3.0)
    d_many = deflated_sharpe_ratio(many, n=500, skew=0.0, kurtosis=3.0)
    # 试验数更多 -> 期望最大夏普门槛更高 -> DSR 不会更高
    assert d_many["sr_star"] >= d_few["sr_star"]


# ------------------------------------------------------------- PBO / CSCV


def test_pbo_low_for_one_dominant_strategy():
    """一个配置真正占优（其余为噪声）时，PBO 应较低。"""
    rng = np.random.default_rng(3)
    T, N = 240, 8
    import pandas as pd
    mat = rng.normal(0, 0.01, (T, N))
    mat[:, 0] += 0.003  # 第 0 列有真实正 alpha
    R = pd.DataFrame(mat, columns=[f"c{i}" for i in range(N)])
    out = probability_of_backtest_overfitting(R, n_splits=8)
    assert 0.0 <= out["pbo"] <= 1.0
    assert out["n_combinations"] > 0
    assert out["pbo"] < 0.5


def test_pbo_requires_two_configs():
    import pandas as pd
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(pd.DataFrame({"a": [0.1, 0.2]}))


# ------------------------------------------------------------- Walk-forward


def test_walk_forward_produces_oos_curve():
    rng = np.random.default_rng(7)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 600)))
    df = make_ohlcv(close)
    result = walk_forward(
        df, MACrossStrategy, metric="sharpe",
        train_window=200, test_window=60,
    )
    # 样本外净值非空、与折数一致
    assert len(result.oos_returns) > 0
    assert not result.folds.empty
    assert "oos_return" in result.folds.columns
    assert len(result.oos_equity) == len(result.oos_returns)


def test_walk_forward_insufficient_data_raises():
    close = np.linspace(100, 110, 50)
    df = make_ohlcv(close)
    with pytest.raises(RuntimeError):
        walk_forward(df, MACrossStrategy, train_window=250, test_window=60)
