"""压力测试模块回归测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from risk.stress import historical_scenarios, monte_carlo_stress, stress_tables


@pytest.fixture
def returns_2020():
    """覆盖 2020-03 流动性危机窗口的确定性收益序列。"""
    idx = pd.bdate_range("2019-06-01", "2020-12-31")
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
    # 在危机窗口注入连续下跌
    crash = (idx >= "2020-02-20") & (idx <= "2020-03-23")
    r[crash] = -0.02
    return r


def test_historical_scenarios_detects_crash(returns_2020):
    df = historical_scenarios(returns_2020)
    assert not df.empty
    row = df[df["情景"] == "2020-03 流动性危机"]
    assert len(row) == 1
    assert float(row["期间收益"].iloc[0]) < -0.2
    assert float(row["最大回撤"].iloc[0]) > 0.2


def test_historical_scenarios_skips_uncovered():
    """区间不覆盖任何情景时返回空表。"""
    idx = pd.bdate_range("2025-01-01", "2025-06-30")
    r = pd.Series(0.001, index=idx)
    assert historical_scenarios(r).empty


def test_historical_scenarios_non_datetime_index():
    r = pd.Series([0.01] * 50)
    assert historical_scenarios(r).empty


def test_monte_carlo_shock_worsens_drawdown(returns_2020):
    out = monte_carlo_stress(returns_2020.iloc[:200], n_sims=200, seed=7)
    assert "bootstrap 基线" in out
    base = out["bootstrap 基线"]["p95"]
    shocked = out["单日冲击 -10%"]["p95"]
    assert shocked > base  # 注入冲击后尾部回撤应恶化
    assert 0.0 < base < 1.0


def test_monte_carlo_reproducible(returns_2020):
    a = monte_carlo_stress(returns_2020.iloc[:100], n_sims=50, seed=1)
    b = monte_carlo_stress(returns_2020.iloc[:100], n_sims=50, seed=1)
    assert a == b


def test_stress_tables_shapes(returns_2020):
    scen, mc = stress_tables(returns_2020, n_sims=50)
    assert {"情景", "区间", "期间收益", "最大回撤", "恢复天数"} <= set(scen.columns)
    assert {"情景", "回撤p50", "回撤p95", "回撤p99"} <= set(mc.columns)
