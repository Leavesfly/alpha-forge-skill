"""绩效指标计算。

输入策略收益率序列（逐周期简单收益），输出核心绩效指标字典。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: 不同周期对应的年化因子（每年周期数）
ANNUALIZATION = {
    "1m": 240 * 240,
    "5m": 240 * 48,
    "15m": 240 * 16,
    "30m": 240 * 8,
    "60m": 240 * 4,
    "1d": 240,
    "1w": 52,
    "1M": 12,
    "1Q": 4,
    "1Y": 1,
}


def periods_per_year(period: str) -> float:
    return float(ANNUALIZATION.get(period, 240))


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（正值，如 0.25 表示 -25%）。"""
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(-drawdown.min())


def max_drawdown_duration(equity: pd.Series) -> int:
    """最长回撤持续期：净值低于前高的最长连续周期数。"""
    if equity.empty:
        return 0
    below = (equity < equity.cummax()).to_numpy()
    longest = current = 0
    for flag in below:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return int(longest)


def omega_ratio(returns: pd.Series, threshold: float = 0.0) -> float:
    """欧米茄比率：阈值之上收益总和 / 阈值之下亏损总和（>1 偏正）。"""
    r = returns.dropna() - threshold
    gains = float(r[r > 0].sum())
    losses = float(-r[r < 0].sum())
    if losses <= 0:
        return 0.0 if gains <= 0 else _PF_CAP
    return float(gains / losses)


#: 盈亏比/欧米茄无亏损时的封顶值（避免 inf 破坏 JSON 序列化）
_PF_CAP = 999.0


def compute_metrics(
    returns: pd.Series,
    equity: pd.Series,
    period: str = "1d",
    positions: pd.Series | None = None,
    risk_free: float = 0.0,
) -> dict:
    """计算绩效指标。

    Args:
        returns: 逐周期策略收益率序列。
        equity: 策略净值曲线（起始为 1.0）。
        period: K 线周期，用于年化。
        positions: 实际持仓序列，用于统计交易次数与胜率。
        risk_free: 年化无风险利率。

    Returns:
        指标名 -> 数值 的字典。
    """
    returns = returns.dropna()
    ann = periods_per_year(period)
    n = len(returns)

    total_return = float(equity.iloc[-1] - 1.0) if len(equity) else 0.0

    if n > 0:
        annual_return = float((1.0 + total_return) ** (ann / n) - 1.0)
        annual_vol = float(returns.std(ddof=0) * np.sqrt(ann))
    else:
        annual_return = 0.0
        annual_vol = 0.0

    rf_per_period = risk_free / ann
    excess = returns - rf_per_period
    std = returns.std(ddof=0)
    sharpe = float(excess.mean() / std * np.sqrt(ann)) if std > 0 else 0.0

    # 索提诺比率：仅用下行波动
    downside = returns[returns < rf_per_period]
    downside_std = downside.std(ddof=0)
    sortino = (
        float(excess.mean() / downside_std * np.sqrt(ann)) if downside_std > 0 else 0.0
    )

    mdd = max_drawdown(equity)
    calmar = float(annual_return / mdd) if mdd > 0 else 0.0

    # 交易统计：按持仓区间聚合（兼容多空），盈亏比基于逐笔盈亏
    num_trades = 0
    win_rate = 0.0
    profit_factor = 0.0
    if positions is not None and len(positions) > 0:
        trades = _trade_returns(positions, returns)
        if trades:
            num_trades = len(trades)
            win_rate = float(sum(1 for t in trades if t > 0) / num_trades)
            gross_profit = float(sum(t for t in trades if t > 0))
            gross_loss = float(-sum(t for t in trades if t < 0))
            if gross_loss > 0:
                profit_factor = float(gross_profit / gross_loss)
            elif gross_profit > 0:
                profit_factor = _PF_CAP

    # 收益分布形状：偏度/峰度（超额峰度），样本不足时置 0
    skew = float(returns.skew()) if n >= 3 else 0.0
    kurtosis = float(returns.kurt()) if n >= 4 else 0.0

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "omega": omega_ratio(returns, rf_per_period),
        "max_drawdown": mdd,
        "max_dd_duration": max_drawdown_duration(equity),
        "calmar": calmar,
        "num_trades": num_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "skew": skew,
        "kurtosis": kurtosis,
        "num_periods": n,
    }


def _trade_returns(positions: pd.Series, returns: pd.Series) -> list[float]:
    """按持仓区间聚合，返回逐笔交易收益列表。

    一笔交易 = 一段方向不变的非零持仓；方向反转视为新开一笔。
    returns 已包含持仓方向（strat_ret = positions * price_ret），故多空均适用。
    """
    pos = positions.fillna(0.0).to_numpy()
    rets = returns.reindex(positions.index).fillna(0.0).to_numpy()

    trades: list[float] = []
    in_trade = False
    cum = 1.0
    cur_sign = 0.0
    for i in range(len(pos)):
        p = pos[i]
        if not in_trade:
            if p != 0.0:
                in_trade = True
                cur_sign = np.sign(p)
                cum = 1.0 + rets[i]
        else:
            if p == 0.0:
                trades.append(cum - 1.0)
                in_trade = False
            elif np.sign(p) != cur_sign:
                # 方向反转：先结算上一笔，再开新一笔
                trades.append(cum - 1.0)
                cur_sign = np.sign(p)
                cum = 1.0 + rets[i]
            else:
                cum *= 1.0 + rets[i]
    if in_trade:
        trades.append(cum - 1.0)

    return trades


def _trade_stats(positions: pd.Series, returns: pd.Series) -> tuple[int, float]:
    """向后兼容：返回（交易次数, 胜率）。"""
    trades = _trade_returns(positions, returns)
    if not trades:
        return 0, 0.0
    wins = sum(1 for t in trades if t > 0)
    return len(trades), float(wins / len(trades))


def relative_metrics(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    period: str = "1d",
    risk_free: float = 0.0,
) -> dict:
    """基准相对指标：信息比率、跟踪误差、Beta 与年化 Jensen's Alpha。

    Args:
        returns: 策略逐周期收益。
        benchmark_returns: 基准逐周期收益（与 returns 对齐）。
        period: K 线周期，用于年化。
        risk_free: 年化无风险利率。

    Returns:
        {information_ratio, tracking_error, beta, alpha} 字典；样本不足时均为 0。
    """
    aligned = pd.concat([returns, benchmark_returns], axis=1).dropna()
    if len(aligned) < 2:
        return {"information_ratio": 0.0, "tracking_error": 0.0, "beta": 0.0, "alpha": 0.0}
    r = aligned.iloc[:, 0]
    b = aligned.iloc[:, 1]
    ann = periods_per_year(period)
    rf_pp = risk_free / ann

    active = r - b
    te_std = active.std(ddof=0)
    tracking_error = float(te_std * np.sqrt(ann))
    ir = float(active.mean() / te_std * np.sqrt(ann)) if te_std > 0 else 0.0

    var_b = b.var(ddof=0)
    beta = float(((r - r.mean()) * (b - b.mean())).mean() / var_b) if var_b > 0 else 0.0
    # Jensen's Alpha：逐周期超额截距按周期数年化
    alpha = float((r.mean() - rf_pp - beta * (b.mean() - rf_pp)) * ann)
    return {
        "information_ratio": ir,
        "tracking_error": tracking_error,
        "beta": beta,
        "alpha": alpha,
    }


def format_report(metrics: dict, title: str = "回测绩效报告") -> str:
    """将指标字典格式化为可读文本报告。"""
    lines = [
        f"===== {title} =====",
        f"累计收益率    : {metrics['total_return'] * 100:+.2f}%",
        f"年化收益率    : {metrics['annual_return'] * 100:+.2f}%",
        f"年化波动率    : {metrics['annual_volatility'] * 100:.2f}%",
        f"夏普比率      : {metrics['sharpe']:.2f}",
        f"索提诺比率    : {metrics['sortino']:.2f}",
        f"欧米茄比率    : {metrics.get('omega', 0.0):.2f}",
        f"最大回撤      : {metrics['max_drawdown'] * 100:.2f}%",
        f"最长回撤期    : {int(metrics.get('max_dd_duration', 0))} 周期",
        f"卡玛比率      : {metrics['calmar']:.2f}",
        f"交易次数      : {metrics['num_trades']}",
        f"胜率          : {metrics['win_rate'] * 100:.2f}%",
        f"盈亏比        : {metrics.get('profit_factor', 0.0):.2f}",
        f"回测周期数    : {metrics['num_periods']}",
    ]
    return "\n".join(lines)
