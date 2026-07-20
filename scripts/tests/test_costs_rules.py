"""P0-2 新增能力测试：成本模型、A 股交易规则、成交价约定。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.rules import (
    TradingRules,
    apply_tradability,
    tradable_masks,
)
from strategies.base import Strategy

from tests.helpers import make_ohlcv


# ---------------------------------------------------------------- 辅助策略


class ConstLongStrategy(Strategy):
    name = "const_long"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


class StepStrategy(Strategy):
    def __init__(self, enter_at: int, **params):
        super().__init__(**params)
        self.enter_at = enter_at

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        sig = np.zeros(len(df))
        sig[self.enter_at:] = 1.0
        return pd.Series(sig, index=df.index)


class ExitStrategy(Strategy):
    """前段满仓多头，exit_at 之后空仓。"""

    def __init__(self, exit_at: int, **params):
        super().__init__(**params)
        self.exit_at = exit_at

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        sig = np.ones(len(df))
        sig[self.exit_at:] = 0.0
        return pd.Series(sig, index=df.index)


# ---------------------------------------------------------------- 成本模型


def test_cost_model_default_matches_legacy():
    """默认成本模型 = 总换手 × (佣金+滑点)，与旧引擎一致。"""
    model = CostModel(commission=0.0005, slippage=0.0005)
    buy = pd.Series([1.0, 0.0])
    sell = pd.Series([0.0, 1.0])
    cost = model.costs(buy, sell)
    assert cost.iloc[0] == pytest.approx(0.001)
    assert cost.iloc[1] == pytest.approx(0.001)


def test_astock_preset_charges_stamp_duty_on_sell_only():
    model = CostModel.preset("astock", commission=0.0, slippage=0.0)
    buy = pd.Series([1.0, 0.0])
    sell = pd.Series([0.0, 1.0])
    cost = model.costs(buy, sell)
    # 买入仅过户费；卖出过户费 + 印花税
    assert cost.iloc[0] == pytest.approx(0.00001)
    assert cost.iloc[1] == pytest.approx(0.00001 + 0.0005)


def test_astock_costs_higher_than_generic_in_backtest():
    close = np.array([100, 101, 102, 103, 104, 105], dtype=float)
    df = make_ohlcv(close)
    strat = ExitStrategy(exit_at=4)
    generic = run_backtest(df, strat, cost_model=CostModel.preset("generic"))
    astock = run_backtest(df, strat, cost_model=CostModel.preset("astock"))
    # 卖出印花税使 A 股净值更低
    assert astock.equity.iloc[-1] < generic.equity.iloc[-1]


def test_unknown_market_preset_raises():
    with pytest.raises(ValueError):
        CostModel.preset("mars")


# ---------------------------------------------------------------- 交易规则


def test_limit_up_blocks_buy():
    # index 2 涨 12% -> 触及涨停，买入受阻
    close = np.array([100, 100, 112, 113, 114], dtype=float)
    df = make_ohlcv(close)
    rules = TradingRules(limit_pct=0.10)
    result = run_backtest(
        df, StepStrategy(enter_at=1), trading_rules=rules,
        commission=0.0, slippage=0.0,
    )
    assert result.positions.iloc[2] == 0.0  # 涨停买不进
    assert result.positions.iloc[3] == 1.0  # 次日恢复建仓


def test_limit_down_blocks_sell():
    # index 4 跌 12% -> 触及跌停，卖出受阻，被迫持有
    close = np.array([100, 100, 100, 100, 88], dtype=float)
    df = make_ohlcv(close)
    rules = TradingRules(limit_pct=0.10)
    result = run_backtest(
        df, ExitStrategy(exit_at=3), trading_rules=rules,
        commission=0.0, slippage=0.0,
    )
    assert result.positions.iloc[4] == 1.0  # 跌停卖不出


def test_suspension_blocks_trading():
    close = np.array([100, 101, 102, 103, 104], dtype=float)
    vol = np.array([1e6, 1e6, 0.0, 1e6, 1e6])  # index 2 停牌
    df = make_ohlcv(close, volume=vol)
    rules = TradingRules(limit_pct=None, check_suspension=True)
    result = run_backtest(
        df, StepStrategy(enter_at=1), trading_rules=rules,
        commission=0.0, slippage=0.0,
    )
    assert result.positions.iloc[2] == 0.0  # 停牌买不进
    assert result.positions.iloc[3] == 1.0  # 复牌后建仓


def test_apply_tradability_is_pure_when_unblocked():
    target = pd.Series([0.0, 1.0, 1.0, 0.0])
    n = len(target)
    no_block = np.zeros(n, dtype=bool)
    out = apply_tradability(target, no_block, no_block)
    pd.testing.assert_series_equal(out, target)


def test_tradable_masks_detect_limits():
    close = np.array([100, 112, 88], dtype=float)
    df = make_ohlcv(close)
    buy_blocked, sell_blocked = tradable_masks(df, TradingRules(limit_pct=0.10))
    assert buy_blocked[1] and not sell_blocked[1]   # 涨停
    assert sell_blocked[2] and not buy_blocked[2]   # 跌停


# ---------------------------------------------------------------- 成交价约定


def test_open_execution_misses_entry_gap():
    """次日开盘成交：建仓 bar 不应吃到隔夜跳空收益。"""
    close = np.array([100, 110, 121], dtype=float)
    open_ = np.array([100, 105, 121], dtype=float)  # index1 跳空高开至 105
    df = make_ohlcv(close, open_prices=open_)

    close_exec = run_backtest(
        df, ConstLongStrategy(), exec_price="close",
        commission=0.0, slippage=0.0,
    )
    open_exec = run_backtest(
        df, ConstLongStrategy(), exec_price="open",
        commission=0.0, slippage=0.0,
    )
    # 收盘成交吃到全程 10%
    assert close_exec.returns.iloc[1] == pytest.approx(0.10)
    # 开盘成交只吃到 open->close 段（110/105-1）
    assert open_exec.returns.iloc[1] == pytest.approx(110 / 105 - 1)
    assert open_exec.returns.iloc[1] < close_exec.returns.iloc[1]


def test_open_execution_matches_close_on_no_trade_bars():
    """无换手的 bar，两种成交约定收益应一致。"""
    close = np.array([100, 110, 121], dtype=float)
    open_ = np.array([100, 105, 121], dtype=float)
    df = make_ohlcv(close, open_prices=open_)
    close_exec = run_backtest(df, ConstLongStrategy(), exec_price="close",
                              commission=0.0, slippage=0.0)
    open_exec = run_backtest(df, ConstLongStrategy(), exec_price="open",
                             commission=0.0, slippage=0.0)
    # index 2 持仓未变（1->1），两者一致
    assert open_exec.returns.iloc[2] == pytest.approx(close_exec.returns.iloc[2])
