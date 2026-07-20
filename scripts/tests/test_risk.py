"""风险管理测试：风险度量、暴露约束、回撤熔断、业绩归因。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from risk.attribution import factor_attribution, return_contribution
from risk.limits import apply_exposure_limits, drawdown_circuit_breaker
from risk.metrics import (
    conditional_var,
    downside_deviation,
    parametric_var,
    risk_report,
    tail_ratio,
    ulcer_index,
    value_at_risk,
)


# ----------------------------------------------------------------- 风险度量


def test_cvar_not_less_than_var():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0, 0.02, 1000))
    var = value_at_risk(r, 0.95)
    cvar = conditional_var(r, 0.95)
    assert cvar >= var >= 0


def test_parametric_var_positive_for_risky_series():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(-0.001, 0.02, 500))
    assert parametric_var(r, 0.95) > 0


def test_downside_deviation_ignores_upside():
    r = pd.Series([0.05, 0.05, 0.05])  # 全为正
    assert downside_deviation(r, mar=0.0) == pytest.approx(0.0)
    r2 = pd.Series([-0.02, 0.05, -0.03])
    assert downside_deviation(r2, mar=0.0) > 0


def test_tail_ratio_symmetric_near_one():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0, 0.02, 5000))
    assert tail_ratio(r) == pytest.approx(1.0, abs=0.2)


def test_ulcer_index_zero_for_monotonic_up():
    eq = pd.Series([1.0, 1.1, 1.2, 1.3])
    assert ulcer_index(eq) == pytest.approx(0.0)


def test_risk_report_keys():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0005, 0.015, 300))
    rep = risk_report(r, period="1d")
    for key in ("var", "cvar", "parametric_var", "annualized_var",
                "downside_deviation", "tail_ratio", "ulcer_index"):
        assert key in rep


# ----------------------------------------------------------------- 暴露约束


def test_max_weight_caps_positions():
    w = pd.DataFrame({"A": [0.5, 0.6], "B": [0.5, 0.4]})
    out = apply_exposure_limits(w, max_weight=0.4)
    assert (out.abs() <= 0.4 + 1e-9).all().all()


def test_max_gross_scales_down():
    w = pd.DataFrame({"A": [0.8], "B": [0.8]})  # gross=1.6
    out = apply_exposure_limits(w, max_gross=1.0)
    assert out.abs().sum(axis=1).iloc[0] == pytest.approx(1.0)


def test_max_net_scales_down_long_bias():
    w = pd.DataFrame({"A": [0.9], "B": [0.9]})  # net=1.8
    out = apply_exposure_limits(w, max_net=1.0)
    assert abs(out.sum(axis=1).iloc[0]) == pytest.approx(1.0)


# ----------------------------------------------------------------- 回撤熔断


def test_drawdown_breaker_halts_after_breach():
    positions = pd.Series([1.0] * 10)
    price_ret = pd.Series([-0.05] * 10)  # 持续下跌
    out = drawdown_circuit_breaker(positions, price_ret, threshold=0.20, deleverage=0.0)
    # 回撤突破 20% 后清仓（尾部为 0），突破前维持满仓
    assert out.iloc[-1] == 0.0
    assert out.iloc[0] == 1.0


def test_drawdown_breaker_no_halt_when_flat_or_up():
    positions = pd.Series([1.0] * 10)
    price_ret = pd.Series([0.01] * 10)  # 持续上涨
    out = drawdown_circuit_breaker(positions, price_ret, threshold=0.20)
    assert (out == 1.0).all()  # 从不触发


def test_drawdown_breaker_deleverage_partial():
    positions = pd.Series([1.0] * 10)
    price_ret = pd.Series([-0.05] * 10)
    out = drawdown_circuit_breaker(positions, price_ret, threshold=0.20, deleverage=0.5)
    assert out.iloc[-1] == pytest.approx(0.5)  # 减半而非清仓


# ----------------------------------------------------------------- 业绩归因


def test_return_contribution_sums_to_portfolio():
    weights = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]})
    rets = pd.DataFrame({"A": [0.02, -0.01], "B": [0.01, 0.03]})
    contrib = return_contribution(weights, rets)
    total = (weights * rets).sum().sum()
    assert contrib.sum() == pytest.approx(total)
    # A 的贡献 = 0.5*0.02 + 0.5*(-0.01) = 0.005
    assert contrib["A"] == pytest.approx(0.005)


def test_factor_attribution_recovers_betas():
    rng = np.random.default_rng(5)
    n = 500
    f1 = rng.normal(0, 0.02, n)
    f2 = rng.normal(0, 0.02, n)
    y = 0.5 * f1 - 0.3 * f2 + rng.normal(0, 1e-4, n) + 0.0002
    factors = pd.DataFrame({"mkt": f1, "size": f2})
    out = factor_attribution(pd.Series(y), factors)
    assert out["betas"]["mkt"] == pytest.approx(0.5, abs=0.05)
    assert out["betas"]["size"] == pytest.approx(-0.3, abs=0.05)
    assert out["r_squared"] > 0.9


def test_factor_attribution_needs_enough_samples():
    with pytest.raises(ValueError):
        factor_attribution(
            pd.Series([0.01, 0.02]),
            pd.DataFrame({"f": [0.01, 0.02]}),
        )
