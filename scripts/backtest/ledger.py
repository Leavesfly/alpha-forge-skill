"""账本引擎：现金 + 整数股的高保真单标的回测。

与向量化引擎（engine.py）的区别：
- 以真实账本（现金余额 + 持股数）逐日演进，而非收益率相乘；
- 整数股约束：按 ``lot_size`` 取整（A 股一手 100 股），小资金可能无法建仓；
- 成本按成交金额从现金中扣除（含卖出单边印花税）；
- 信号管线（shift(1) 防前视、止损止盈、波动率目标、涨跌停/停牌约束、
  成交价约定）与向量化引擎保持一致，便于互相校验。

无摩擦（零成本、lot_size=1、大资金）时净值与向量化引擎近似一致，
取整误差随资金规模增大而消失。分红除权现金流暂未建模（已知局限，
回测请使用前复权数据）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import Strategy
from utils import resolve_time_index

from .costs import CostModel
from .engine import (
    BacktestResult,
    _apply_risk_management,
    _apply_vol_target,
)
from .metrics import compute_metrics
from .rules import TradingRules, tradable_masks


def run_backtest_ledger(
    df: pd.DataFrame,
    strategy: Strategy,
    symbol: str = "",
    period: str = "1d",
    initial_capital: float = 1_000_000.0,
    commission: float = 0.0005,
    slippage: float = 0.0005,
    risk_free: float = 0.0,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    vol_target: float | None = None,
    vol_window: int = 20,
    max_leverage: float = 1.0,
    cost_model: CostModel | None = None,
    exec_price: str = "close",
    trading_rules: TradingRules | None = None,
    lot_size: int = 1,
) -> BacktestResult:
    """账本引擎回测，参数语义与 ``run_backtest`` 一致。

    Args:
        lot_size: 最小交易单位（股）；A 股一手为 100。
        initial_capital: 初始资金；账本引擎下真实影响可建仓数量。

    Returns:
        BacktestResult：equity 为归一化净值（期初 1.0），positions 为
        逐日实际仓位比例（持仓市值 / 总资产）。
    """
    df = df.reset_index(drop=True)
    close = df["close"].astype(float)

    signals = strategy.generate_signals(df).astype(float)
    if stop_loss or take_profit:
        signals = _apply_risk_management(signals, close, stop_loss, take_profit)
    if vol_target:
        signals = _apply_vol_target(
            signals, close, vol_target, vol_window, max_leverage, period
        )

    if trading_rules is not None:
        buy_blocked, sell_blocked = tradable_masks(df, trading_rules)
    else:
        n = len(df)
        buy_blocked = np.zeros(n, dtype=bool)
        sell_blocked = np.zeros(n, dtype=bool)

    model = cost_model or CostModel(commission=commission, slippage=slippage)
    both_rate = model.commission + model.slippage + model.transfer_fee

    price_close = close.to_numpy(dtype=float)
    # 成交时间线与向量化引擎对齐：
    # - close：信号 bar 的收盘价成交（target[t]=signals[t]，持仓吃 t+1 收益）；
    # - open ：次日开盘成交（target[t]=signals[t-1]，隔夜跳空归旧持仓）。
    if exec_price == "open" and "open" in df.columns:
        price_exec = df["open"].astype(float).to_numpy()
        target = signals.shift(1).fillna(0.0).to_numpy(dtype=float)
    else:
        if exec_price == "open":
            print("[warn] 数据缺少 open 列，exec_price=open 退化为收盘价成交。")
        price_exec = price_close
        target = signals.fillna(0.0).to_numpy(dtype=float)

    n = len(df)
    lot = max(1, int(lot_size))
    cash = float(initial_capital)
    shares = 0  # 有符号持股数（负为空头）
    equity_arr = np.empty(n)
    pos_frac = np.empty(n)
    warned_unaffordable = False

    for t in range(n):
        px = price_exec[t]
        equity_now = cash + shares * px
        # 目标持股：按目标仓位比例折算，向零取整到 lot 的整数倍
        desired = int(target[t] * equity_now / (px * lot)) * lot

        if desired > shares and buy_blocked[t]:
            desired = shares  # 想加仓却涨停/停牌，维持
        elif desired < shares and sell_blocked[t]:
            desired = shares  # 想减仓却跌停/停牌，维持

        delta = desired - shares
        if delta != 0:
            notional = abs(delta) * px
            cost = notional * both_rate
            if delta < 0:
                cost += notional * model.stamp_duty  # 卖出单边印花税
            cash -= delta * px + cost
            shares = desired
        elif target[t] != 0.0 and shares == 0 and not warned_unaffordable:
            # 想建仓但一手都买不起：提示资金不足（只提示一次）
            print(
                f"[warn] 资金不足以按 lot_size={lot} 建仓"
                f"（单手约 {px * lot:,.0f}），持仓维持为 0。"
            )
            warned_unaffordable = True

        equity_eod = cash + shares * price_close[t]
        equity_arr[t] = equity_eod
        pos_frac[t] = (shares * price_close[t] / equity_eod) if equity_eod > 0 else 0.0

    equity = pd.Series(equity_arr / initial_capital)
    strat_ret = equity.pct_change().fillna(equity.iloc[0] - 1.0 if n else 0.0)
    positions = pd.Series(pos_frac)

    price_ret = close.pct_change().fillna(0.0)
    benchmark_equity = (1.0 + price_ret).cumprod()

    index = resolve_time_index(df)
    for s in (close, signals, positions, strat_ret, equity, benchmark_equity):
        s.index = index

    metrics = compute_metrics(
        strat_ret, equity, period=period, positions=positions, risk_free=risk_free
    )
    benchmark_metrics = compute_metrics(
        price_ret, benchmark_equity, period=period, risk_free=risk_free
    )

    return BacktestResult(
        symbol=symbol,
        period=period,
        close=close,
        signals=signals,
        positions=positions,
        returns=strat_ret,
        equity=equity,
        benchmark_equity=benchmark_equity,
        metrics=metrics,
        benchmark_metrics=benchmark_metrics,
    )
