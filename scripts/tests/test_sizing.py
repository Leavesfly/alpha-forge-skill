"""仓位管理测试：评分建议仓位（风险预算法）与回测半 Kelly 连续仓位。"""

from __future__ import annotations

import numpy as np
import pytest

from backtest.engine import run_backtest
from scoring.plan import attach_position_sizing, build_trade_plan, format_plan
from strategies import get_strategy
from tests.helpers import make_ohlcv


# ---------------------------------------------------------------- 建议仓位


def _plan(close=100.0, atr=2.0, ma20=98.0) -> dict:
    plan = build_trade_plan(close, ma20, atr)
    assert plan is not None
    return plan


def test_sizing_risk_budget_math():
    """股数 = 资金×风险比例 / R（向下取整到 lot），止损亏损≈风险预算。"""
    plan = attach_position_sizing(_plan(), capital=100_000, risk_pct=0.01, lot_size=100)
    s = plan["sizing"]
    # R = 2×ATR = 4；风险额 1000；1000/4 = 250 股 -> 取整 200 股
    assert s["risk_amount"] == 1000.0
    assert s["suggested_shares"] == 200
    assert s["position_value"] == pytest.approx(200 * plan["entry"])
    # 实际止损亏损 = 股数 × R <= 风险预算
    assert s["suggested_shares"] * plan["r"] <= s["risk_amount"]


def test_sizing_capped_by_capital():
    """低波动标的（R 极小）不允许超出可用资金。"""
    plan = attach_position_sizing(
        _plan(close=100.0, atr=0.05), capital=10_000, risk_pct=0.05, lot_size=1
    )
    s = plan["sizing"]
    assert s["position_value"] <= 10_000
    assert s["position_pct"] <= 1.0


def test_sizing_insufficient_for_one_lot():
    """资金不足一手时建议股数为 0，展示行给出提示。"""
    plan = attach_position_sizing(_plan(), capital=1_000, risk_pct=0.01, lot_size=100)
    assert plan["sizing"]["suggested_shares"] == 0
    lines = format_plan(plan)
    assert any("资金不足一手" in line for line in lines)


def test_sizing_passthrough_when_disabled():
    """plan 为 None 或资金无效时透传不报错。"""
    assert attach_position_sizing(None, 100_000) is None
    plan = _plan()
    assert "sizing" not in attach_position_sizing(plan, 0)


def test_format_plan_contains_sizing_line():
    plan = attach_position_sizing(_plan(), capital=100_000, risk_pct=0.01, lot_size=100)
    lines = format_plan(plan)
    assert any("建议仓位" in line and "200 股" in line for line in lines)


# ---------------------------------------------------------------- Kelly 仓位


@pytest.fixture
def trend_df():
    rng = np.random.default_rng(11)
    close = 100.0 * np.exp(np.cumsum(0.002 + 0.015 * rng.standard_normal(300)))
    return make_ohlcv(close)


def test_kelly_positions_are_continuous_and_bounded(trend_df):
    """Kelly 仓位为 [0, max_leverage] 的连续值，不超过上限。"""
    strategy = get_strategy("ma_cross")
    res = run_backtest(trend_df, strategy, kelly=True, kelly_window=40, max_leverage=1.0)
    pos = res.positions.to_numpy()
    assert (pos >= -1e-9).all() and (pos <= 1.0 + 1e-9).all()
    # 存在非 {0,1} 的中间仓位（连续缩放生效）
    interior = pos[(pos > 1e-6) & (pos < 1 - 1e-6)]
    assert len(interior) > 0


def test_kelly_takes_priority_over_vol_target(trend_df):
    """kelly 与 vol_target 同时设置时按 kelly 计算。"""
    strategy = get_strategy("ma_cross")
    only_kelly = run_backtest(trend_df, strategy, kelly=True)
    both = run_backtest(trend_df, strategy, kelly=True, vol_target=0.15)
    assert (only_kelly.positions == both.positions).all()


def test_kelly_zero_when_negative_expectation():
    """单边下跌（滚动 μ<0）时 Kelly 仓位应为 0（不反向加杠杆）。"""
    close = 100.0 * (1.0 - 0.01) ** np.arange(200)
    df = make_ohlcv(close)
    strategy = get_strategy("momentum")  # 下跌中动量给 0 或 1 信号
    res = run_backtest(df, strategy, kelly=True, kelly_window=30)
    assert (res.positions.to_numpy() <= 1e-9).all()
