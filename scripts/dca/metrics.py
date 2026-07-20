"""定投绩效指标计算。

定投（定期定额）因资金分批注入，不能用时间加权收益衡量，核心指标为
**资金加权收益率（XIRR / 内部收益率）**：对带日期的现金流求解使净现值为 0
的年化贴现率，正确反映每笔资金的实际持有时长。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.metrics import max_drawdown


def _year_fractions(dates: pd.DatetimeIndex) -> np.ndarray:
    """各现金流相对首笔的年化时间差（按 365 天/年）。"""
    origin = dates[0]
    return np.array([(d - origin).days / 365.0 for d in dates], dtype=float)


def xirr(
    amounts: np.ndarray,
    year_fracs: np.ndarray,
    lower: float = -0.9999,
    upper: float = 100.0,
    tol: float = 1e-8,
    max_iter: int = 200,
) -> float:
    """求解带日期现金流的内部收益率（年化，XIRR）。

    约定现金流方向：投入为负、期末市值为正。用二分法在 [lower, upper] 区间
    求解 NPV(r)=Σ cf_i/(1+r)^t_i = 0，稳健但要求区间端点异号。

    Args:
        amounts: 现金流金额数组（投入为负、回收为正）。
        year_fracs: 与 amounts 对齐的年化时间差数组（首项为 0）。
        lower/upper: 年化收益率搜索区间。
        tol: NPV 收敛容差。
        max_iter: 最大迭代次数。

    Returns:
        年化资金加权收益率；无法求解（现金流同号等）时返回 nan。
    """
    amounts = np.asarray(amounts, dtype=float)

    def npv(rate: float) -> float:
        return float(np.sum(amounts / (1.0 + rate) ** year_fracs))

    # 现金流全部同号则无解
    if np.all(amounts >= 0) or np.all(amounts <= 0):
        return float("nan")

    f_low, f_high = npv(lower), npv(upper)
    if np.isnan(f_low) or np.isnan(f_high) or f_low * f_high > 0:
        return float("nan")  # 区间内无符号变化，无法二分

    lo, hi = lower, upper
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            return float(mid)
        if f_low * f_mid < 0:
            hi, f_high = mid, f_mid
        else:
            lo, f_low = mid, f_mid
    return float((lo + hi) / 2.0)


def compute_dca_metrics(
    invested: pd.Series,
    market_value: pd.Series,
    shares: pd.Series,
    contribution: pd.Series,
    num_contributions: int,
    cashflow: pd.Series | None = None,
    dividend_income: float = 0.0,
    total_dividends: float = 0.0,
) -> dict:
    """计算定投绩效指标。

    Args:
        invested: 累计净投入本金曲线。
        market_value: 持仓市值曲线。
        shares: 累计份额曲线。
        contribution: 每期名义交易金额（买入>0、卖出<0，非交易日为 0）。
        num_contributions: 定投/交易期数。
        cashflow: 实际现金流（流出为负、流入为正），用于 XIRR；
            缺省时退化为 ``-contribution``（纯买入场景等价）。
        dividend_income: 现金落袋的累计分红（cash 策略），计入盈亏；
            再投入策略下为 0（已体现在市值中）。
        total_dividends: 累计到账现金分红总额（两种策略都记，仅展示）。

    Returns:
        指标名 -> 数值 的字典。年化收益率为 XIRR（资金加权）。
    """
    total_invested = float(invested.iloc[-1])
    final_value = float(market_value.iloc[-1])
    total_profit = final_value + dividend_income - total_invested
    total_return = total_profit / total_invested if total_invested > 0 else 0.0

    # 平均持仓成本 = 累计净投入 / 累计份额（反映实际买入均价效果）
    final_shares = float(shares.iloc[-1])
    avg_cost = total_invested / final_shares if final_shares > 0 else 0.0

    # 资金加权年化收益率（XIRR）：用实际现金流（买入为负、卖出为正）+ 期末市值
    cf = cashflow if cashflow is not None else -contribution
    annual_return = _dca_xirr(cf, final_value, market_value.index)

    # 回撤：基于「市值/累计投入」比值曲线（成本调整后的净值），
    # 只在已开始投入后计算，避免早期分母为 0。
    ratio = _cost_adjusted_nav(market_value, invested)
    mdd = max_drawdown(ratio) if len(ratio) else 0.0

    return {
        "num_contributions": num_contributions,
        "total_invested": total_invested,
        "final_value": final_value,
        "total_profit": total_profit,
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": mdd,
        "avg_cost": avg_cost,
        "num_periods": int(len(market_value)),
        "total_dividends": float(total_dividends),
    }


def compute_lumpsum_metrics(
    close: pd.Series,
    total_invested: float,
    cost_rate: float,
    dividends: "np.ndarray | None" = None,
) -> dict:
    """一次性投入基准：期初一次性投入 total_invested，持有到末期。

    作为定投的对照，直接回答「若有同等本金在期初全部买入」的结果。

    Args:
        close: 收盘价序列（索引为日期）。
        total_invested: 与定投相同的总投入本金。
        cost_rate: 单边成本率（手续费 + 滑点）。
        dividends: 对齐到交易日历的每股分红数组（显式分红建模时传入）；
            基准按「分红现金落袋不再投」计入盈亏，与不复权价口径一致。

    Returns:
        与 compute_dca_metrics 同口径的部分指标字典（年化为 CAGR）。
    """
    price0 = float(close.iloc[0])
    shares = total_invested * (1.0 - cost_rate) / price0
    value_curve = shares * close
    final_value = float(value_curve.iloc[-1])
    # 显式分红：固定份额 × 每股分红，现金落袋计入盈亏（不再投）
    dividend_cash = float(shares * dividends.sum()) if dividends is not None else 0.0
    total_profit = final_value + dividend_cash - total_invested
    total_return = total_profit / total_invested if total_invested > 0 else 0.0

    years = _year_span(close.index)
    if years > 0 and final_value > 0 and total_invested > 0:
        annual_return = float(
            ((final_value + dividend_cash) / total_invested) ** (1.0 / years) - 1.0
        )
    else:
        annual_return = 0.0

    mdd = max_drawdown(close / price0)

    return {
        "total_invested": total_invested,
        "final_value": final_value,
        "total_profit": total_profit,
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": mdd,
        "total_dividends": dividend_cash,
    }


def _cost_adjusted_nav(market_value: pd.Series, invested: pd.Series) -> pd.Series:
    """成本调整净值 = 市值 / 累计投入（仅在已投入后有意义）。"""
    started = invested > 0
    if not started.any():
        return pd.Series(dtype=float)
    mv = market_value[started]
    inv = invested[started]
    return (mv / inv).replace([np.inf, -np.inf], np.nan).dropna()


def _year_span(index: pd.Index) -> float:
    """索引首末的年化跨度（按 365 天/年）；非时间索引退化为 nan 提示。"""
    if isinstance(index, pd.DatetimeIndex) and len(index) > 1:
        return (index[-1] - index[0]).days / 365.0
    return 0.0


def _dca_xirr(cashflow: pd.Series, final_value: float, index: pd.Index) -> float:
    """由实际现金流与期末市值构造序列并求 XIRR。

    ``cashflow`` 为投资者视角的实际现金流：买入为负（流出）、卖出为正（流入）。
    期末市值作为最后一笔正向回收现金流。
    """
    if not isinstance(index, pd.DatetimeIndex):
        return float("nan")

    mask = cashflow.to_numpy() != 0.0
    if not mask.any():
        return float("nan")

    dates = list(index[mask])
    amounts = list(cashflow.to_numpy()[mask])  # 已带符号：买入负、卖出正

    # 期末市值作为一笔正向回收现金流
    dates.append(index[-1])
    amounts.append(final_value)

    dt_index = pd.DatetimeIndex(dates)
    year_fracs = _year_fractions(dt_index)
    return xirr(np.array(amounts, dtype=float), year_fracs)


def format_dca_report(metrics: dict, title: str = "定投绩效报告") -> str:
    """将定投指标字典格式化为可读文本报告。"""
    annual = metrics["annual_return"]
    annual_str = "N/A（缺少日期或现金流同号）" if np.isnan(annual) else f"{annual * 100:+.2f}%"
    lines = [
        f"===== {title} =====",
        f"定投期数      : {metrics['num_contributions']}",
        f"累计投入本金  : {metrics['total_invested']:,.2f}",
        f"期末市值      : {metrics['final_value']:,.2f}",
        f"累计盈亏      : {metrics['total_profit']:+,.2f}",
        f"累计收益率    : {metrics['total_return'] * 100:+.2f}%",
        f"年化收益率(IRR): {annual_str}",
        f"最大回撤      : {metrics['max_drawdown'] * 100:.2f}%",
        f"平均持仓成本  : {metrics['avg_cost']:.4f}",
        f"回测周期数    : {metrics['num_periods']}",
    ]
    if metrics.get("total_dividends", 0.0) > 0:
        lines.insert(5, f"累计现金分红  : {metrics['total_dividends']:,.2f}")
    return "\n".join(lines)


def format_lumpsum_report(metrics: dict, title: str = "一次性投入基准") -> str:
    """格式化一次性投入基准报告。"""
    lines = [
        f"===== {title} =====",
        f"累计投入本金  : {metrics['total_invested']:,.2f}",
        f"期末市值      : {metrics['final_value']:,.2f}",
        f"累计盈亏      : {metrics['total_profit']:+,.2f}",
        f"累计收益率    : {metrics['total_return'] * 100:+.2f}%",
        f"年化收益率(CAGR): {metrics['annual_return'] * 100:+.2f}%",
        f"最大回撤      : {metrics['max_drawdown'] * 100:.2f}%",
    ]
    return "\n".join(lines)
