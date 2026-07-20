"""回测引擎回归测试。

锁定引擎的关键不变量，尤其是「防前视偏差」这一铁律：
当日信号必须次日生效，未来价格不得影响历史净值。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from strategies.base import Strategy

from tests.helpers import make_ohlcv


class ConstLongStrategy(Strategy):
    """始终满仓多头，用于隔离引擎行为。"""

    name = "const_long"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


class StepStrategy(Strategy):
    """在指定位置开始持有多头，之前空仓。"""

    def __init__(self, enter_at: int, **params):
        super().__init__(**params)
        self.enter_at = enter_at

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        sig = np.zeros(len(df))
        sig[self.enter_at:] = 1.0
        return pd.Series(sig, index=df.index)


def test_positions_are_signals_shifted_by_one(random_walk_df):
    """核心：持仓 = 信号 shift(1)，首个 bar 必为 0（不能当天成交）。"""
    result = run_backtest(random_walk_df, StepStrategy(enter_at=10))
    assert result.positions.iloc[0] == 0.0
    # 第 10 根出信号，第 11 根（次日）才建仓
    assert result.positions.iloc[10] == 0.0
    assert result.positions.iloc[11] == 1.0


def test_future_prices_do_not_affect_past_equity(random_walk_df):
    """防前视铁律：篡改最后一根 K 线的收盘价，历史净值不得改变。"""
    base = run_backtest(random_walk_df, ConstLongStrategy())

    tampered = random_walk_df.copy()
    tampered.loc[tampered.index[-1], "close"] *= 2.0  # 未来暴涨
    perturbed = run_backtest(tampered, ConstLongStrategy())

    # 除最后一根外，净值序列应逐点一致
    pd.testing.assert_series_equal(
        base.equity.iloc[:-1], perturbed.equity.iloc[:-1]
    )


def test_transaction_cost_deducted_on_turnover(trending_up_df):
    """一次建仓的成本应等于换手 × (手续费 + 滑点)。"""
    commission, slippage = 0.001, 0.001
    result = run_backtest(
        trending_up_df,
        StepStrategy(enter_at=5),
        commission=commission,
        slippage=slippage,
    )
    # 建仓当根（持仓从 0->1）应扣除一次成本
    entry_pos = 6  # enter_at=5 -> 次日 6 建仓
    price_ret = trending_up_df["close"].pct_change().fillna(0.0).to_numpy()
    expected = 1.0 * price_ret[entry_pos] - (commission + slippage)
    assert result.returns.iloc[entry_pos] == pytest.approx(expected, rel=1e-9)


def test_zero_cost_matches_gross(trending_up_df):
    """零成本时策略净值 = 持仓 × 价格收益的复利。"""
    result = run_backtest(
        trending_up_df, ConstLongStrategy(), commission=0.0, slippage=0.0
    )
    price_ret = trending_up_df["close"].pct_change().fillna(0.0)
    positions = pd.Series(1.0, index=result.positions.index).shift(1).fillna(0.0)
    expected_equity = (1.0 + positions.values * price_ret.values).cumprod()
    np.testing.assert_allclose(result.equity.values, expected_equity, rtol=1e-9)


def test_benchmark_is_buy_and_hold(random_walk_df):
    """基准净值应为买入持有（收盘价复利），与策略无关。"""
    result = run_backtest(random_walk_df, StepStrategy(enter_at=50))
    bh = (1.0 + random_walk_df["close"].pct_change().fillna(0.0)).cumprod()
    np.testing.assert_allclose(result.benchmark_equity.values, bh.values, rtol=1e-9)


def test_short_profits_when_price_falls(trending_down_df):
    """做空在下行行情中应盈利（净值 > 1）。"""
    result = run_backtest(
        trending_down_df,
        ConstShortStrategy(),
        commission=0.0,
        slippage=0.0,
    )
    assert result.equity.iloc[-1] > 1.0


class ConstShortStrategy(Strategy):
    name = "const_short"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(-1.0, index=df.index)


def test_stop_loss_forces_exit():
    """浮亏超过止损阈值应离场并保持空仓（直到信号归零）。"""
    # 建仓后持续下跌，触发 10% 止损
    close = np.array([100, 100, 98, 95, 89, 85, 80], dtype=float)
    df = make_ohlcv(close)
    result = run_backtest(
        df, ConstLongStrategy(), stop_loss=0.10, commission=0.0, slippage=0.0
    )
    # 触发止损后，尾部应回到空仓（0）
    assert result.positions.iloc[-1] == 0.0


def test_vol_target_produces_continuous_positions(random_walk_df):
    """波动率目标模式下持仓应为连续值，而非仅 {-1,0,1}。"""
    result = run_backtest(
        random_walk_df, ConstLongStrategy(), vol_target=0.15, vol_window=20
    )
    nonzero = result.positions[result.positions != 0.0]
    # 至少存在一个非 1.0 的连续仓位
    assert (nonzero != 1.0).any()
    # 且不超过默认杠杆上限 1.0
    assert result.positions.max() <= 1.0 + 1e-9
