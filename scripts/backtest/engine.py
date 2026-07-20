"""向量化回测引擎。

基于目标持仓信号进行逐周期收益计算，内置：
- shift(1) 防前视偏差（当日信号次日生效）
- 手续费与滑点（按持仓变动比例扣除）
- 多空支持：目标持仓可取 {-1, 0, 1}，做空盈亏自动处理
- 止损/止盈风控：按持仓成本动态离场
- 波动率目标仓位：按实现波动率缩放为连续仓位
- Buy & Hold 基准对比
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategies.base import Strategy

from .costs import CostModel
from .metrics import compute_metrics, periods_per_year
from .rules import TradingRules, apply_tradability, tradable_masks


@dataclass
class BacktestResult:
    """回测结果容器。"""

    symbol: str
    period: str
    close: pd.Series
    signals: pd.Series
    positions: pd.Series
    returns: pd.Series
    equity: pd.Series
    benchmark_equity: pd.Series
    metrics: dict = field(default_factory=dict)
    benchmark_metrics: dict = field(default_factory=dict)

    @property
    def trades(self) -> pd.DataFrame:
        """开平仓记录（时间、方向、价格）。"""
        pos = self.positions.fillna(0.0)
        change = pos.diff().fillna(pos)
        idx = change[change != 0].index
        rows = []
        for t in idx:
            action = "BUY" if change.loc[t] > 0 else "SELL"
            rows.append({"time": t, "action": action, "price": self.close.loc[t]})
        return pd.DataFrame(rows)


def run_backtest(
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
) -> BacktestResult:
    """执行回测。

    Args:
        df: 含 ``close`` 列的 OHLCV DataFrame（时间升序）。
        strategy: 策略实例。
        symbol: 标的代码（用于展示）。
        period: K 线周期（用于年化指标）。
        initial_capital: 初始资金（影响绝对金额，比率指标不受影响）。
        commission: 单边手续费率。
        slippage: 单边滑点率。
        risk_free: 年化无风险利率。
        stop_loss: 止损比例（如 0.05 表示浮亏 5% 离场），None 关闭。
        take_profit: 止盈比例（如 0.10 表示浮盈 10% 离场），None 关闭。
        vol_target: 年化目标波动率（如 0.15=15%）；设置后按实现波动率将
            {-1,0,1} 信号缩放为连续仓位。None 关闭（满仓模式）。
        vol_window: 波动率估计的滚动窗口（周期数）。
        max_leverage: 仓位上限（默认 1.0，即不加杠杆）。
        cost_model: 交易成本模型；None 时用 commission/slippage 构造（兼容旧行为）。
        exec_price: 成交价约定，``close``（收盘成交，默认）或 ``open``（次日开盘成交，
            隔夜跳空归旧持仓、日内归新持仓，更贴近现实）。
        trading_rules: A 股交易规则（涨跌停/停牌）；None 时不施加成交约束。

    Returns:
        BacktestResult。多空由策略信号 {-1, 0, 1} 决定，引擎自动处理做空盈亏。
    """
    df = df.reset_index(drop=True)
    close = df["close"].astype(float)

    signals = strategy.generate_signals(df).astype(float)
    # 应用止损/止盈（作用于目标持仓时间线，随后统一 shift(1) 执行）
    if stop_loss or take_profit:
        signals = _apply_risk_management(signals, close, stop_loss, take_profit)
    # 波动率目标仓位：将离散信号缩放为连续仓位
    if vol_target:
        signals = _apply_vol_target(
            signals, close, vol_target, vol_window, max_leverage, period
        )
    # 次日生效，避免使用未来信息
    positions = signals.shift(1).fillna(0.0)

    # A 股交易规则：涨跌停/停牌导致的不可成交，修正为实际可达成的持仓
    if trading_rules is not None:
        buy_blocked, sell_blocked = tradable_masks(df, trading_rules)
        positions = apply_tradability(positions, buy_blocked, sell_blocked)

    price_ret = close.pct_change().fillna(0.0)

    # 交易成本：默认由 commission/slippage 构造，兼容旧引擎（对称双边比例）
    model = cost_model or CostModel(commission=commission, slippage=slippage)
    dpos = positions.diff().fillna(positions)
    buy_turnover = dpos.clip(lower=0.0)
    sell_turnover = (-dpos).clip(lower=0.0)
    trade_cost = model.costs(buy_turnover, sell_turnover)

    # 逐周期毛收益：按成交价约定计算
    if exec_price == "open" and "open" in df.columns:
        open_ = df["open"].astype(float)
        prev_close = close.shift(1)
        old_pos = positions.shift(1).fillna(0.0)
        gap_ret = (open_ / prev_close - 1.0).fillna(0.0)  # 隔夜跳空由旧持仓承担
        intra_ret = (close / open_ - 1.0).fillna(0.0)  # 日内由新持仓承担
        gross = (1.0 + old_pos * gap_ret) * (1.0 + positions * intra_ret) - 1.0
    else:
        if exec_price == "open":
            print("[warn] 数据缺少 open 列，exec_price=open 退化为收盘价成交。")
        gross = positions * price_ret

    strat_ret = gross - trade_cost
    equity = (1.0 + strat_ret).cumprod()

    benchmark_ret = price_ret
    benchmark_equity = (1.0 + benchmark_ret).cumprod()

    # 附上索引便于绘图（使用 trade_date/日期列，退化为 RangeIndex）
    index = _resolve_index(df)
    for s in (close, signals, positions, strat_ret, equity, benchmark_equity):
        s.index = index

    metrics = compute_metrics(
        strat_ret, equity, period=period, positions=positions, risk_free=risk_free
    )
    benchmark_metrics = compute_metrics(
        benchmark_ret, benchmark_equity, period=period, risk_free=risk_free
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


def _resolve_index(df: pd.DataFrame) -> pd.Index:
    """从常见时间列构造索引，找不到则用序号索引。"""
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            try:
                return pd.to_datetime(df[col])
            except (ValueError, TypeError):
                return pd.Index(df[col])
    return pd.RangeIndex(len(df))


def _apply_risk_management(
    signals: pd.Series,
    close: pd.Series,
    stop_loss: float | None,
    take_profit: float | None,
) -> pd.Series:
    """对目标持仓施加止损/止盈。

    以持仓建仓价为基准计算浮动盈亏，触发阈值即离场并保持空仓，
    直到策略信号归零（一轮信号结束）后才允许重新入场，避免同一段
    信号内被反复止损又立刻重进。多头、空头分别按方向计算浮盈浮亏。

    Args:
        signals: 策略输出的目标持仓（{-1, 0, 1}）。
        close: 收盘价序列（与 signals 对齐）。
        stop_loss: 止损比例（正数），None 关闭。
        take_profit: 止盈比例（正数），None 关闭。

    Returns:
        风控调整后的目标持仓 Series。
    """
    target = signals.to_numpy(dtype=float)
    price = close.to_numpy(dtype=float)
    n = len(target)
    adjusted = np.zeros(n)

    pos = 0.0
    entry = np.nan
    blocked = False  # 触发止损/止盈后锁定，等目标归零再解锁

    for i in range(n):
        tgt = target[i]
        if tgt == 0.0:
            pos, entry, blocked = 0.0, np.nan, False
        elif blocked:
            pos, entry = 0.0, np.nan
        else:
            # 首次入场或方向反转：以当前 bar 收盘价为建仓价
            if pos == 0.0 or np.sign(tgt) != np.sign(pos):
                pos, entry = tgt, price[i]
            # 基于建仓价计算浮动盈亏（区分多空方向）
            ret = price[i] / entry - 1.0 if pos > 0 else entry / price[i] - 1.0
            if (stop_loss and ret <= -stop_loss) or (take_profit and ret >= take_profit):
                pos, entry, blocked = 0.0, np.nan, True
        adjusted[i] = pos

    return pd.Series(adjusted, index=signals.index)


def _apply_vol_target(
    signals: pd.Series,
    close: pd.Series,
    vol_target: float,
    window: int,
    max_leverage: float,
    period: str,
) -> pd.Series:
    """波动率目标仓位：将离散信号缩放为连续仓位。

    仓位 = 信号 * min(目标波动率 / 实现波动率, max_leverage)。
    实现波动率由收益率滚动标准差年化得到（使用截至当前 bar 的数据，
    不引入未来信息）；波动率尚未形成或为 0 时仓位置 0。

    Args:
        signals: 目标持仓信号（{-1, 0, 1}）。
        close: 收盘价序列。
        vol_target: 年化目标波动率（正数）。
        window: 波动率滚动窗口。
        max_leverage: 仓位上限。
        period: K 线周期，用于年化。

    Returns:
        连续仓位 Series。
    """
    ann = periods_per_year(period)
    ret = close.pct_change()
    realized_vol = ret.rolling(window).std() * np.sqrt(ann)
    scale = (vol_target / realized_vol).clip(upper=max_leverage)
    scale = scale.replace([np.inf, -np.inf], np.nan)
    return (signals * scale).fillna(0.0)
