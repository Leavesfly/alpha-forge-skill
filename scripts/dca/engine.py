"""定投（定期定额）回测引擎。

与信号-仓位式回测不同，定投按固定周期注入现金、累积份额，靠摊薄成本获利，
因此单独建模。除**纯定投**外，还内置三种增强定投模式：

- ``ma``        ：收盘价低于均线时按 ``boost`` 倍加码（单档）。
- ``smart``     ：智能定投，按偏离均线的幅度分档加码/减码（越便宜投越多，越贵越少乃至暂停）。
- ``dip``       ：超跌回撤加码，按距近期高点的回撤深度分档，叠加 RSI 超卖触发。
- ``value_avg`` ：价值平均，盯住目标市值增长线，每期补足差额（涨多可卖出）。

以现金流账本记账（买入为流出、卖出为流入），资金加权收益率（XIRR）计量，
并与「一次性投入」「纯定投」两条基准对比。增强模式的择时判断仅依据截至当日（含）
的数据，且买卖均在当日收盘价成交，不引入未来信息。绩效由 ``dca.metrics`` 计算。

支持**显式分红建模**（``dividends`` + ``div_policy``）：按除权除息日持仓份额
计现金分红，``reinvest`` 当日收盘价再投入、``cash`` 计入投资者现金流（XIRR 口径）。
显式分红应搭配**不复权**价格使用，否则与前复权隐含的分红重复计收益。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from utils import resolve_time_index

from .metrics import compute_dca_metrics, compute_lumpsum_metrics

#: 支持的定投模式
MODES = ("fixed", "ma", "smart", "dip", "value_avg")

#: 分红处理策略：再投入 / 现金落袋
DIV_POLICIES = ("reinvest", "cash")


@dataclass
class DCAResult:
    """定投回测结果容器。"""

    symbol: str
    period: str
    freq: str
    mode: str
    close: pd.Series
    contribution: pd.Series  # 每期投入/减仓的名义金额（买入>0，卖出<0，非交易日为 0）
    cashflow: pd.Series  # 实际现金流（流出为负、流入为正），用于 XIRR
    invested: pd.Series  # 累计净投入本金
    shares: pd.Series  # 累计份额
    market_value: pd.Series  # 持仓市值
    metrics: dict = field(default_factory=dict)
    lumpsum_metrics: dict = field(default_factory=dict)
    dca_metrics: dict | None = None  # 纯定投基准（增强模式下才有）
    dividends: pd.Series | None = None  # 逐日到账现金分红（显式分红建模时才有）
    div_policy: str | None = None

    @property
    def transactions(self) -> pd.DataFrame:
        """每笔交易记录（时间、方向、名义金额、价格）。"""
        mask = self.contribution.to_numpy() != 0.0
        idx = self.contribution.index[mask]
        rows = []
        for t in idx:
            amt = float(self.contribution.loc[t])
            rows.append(
                {
                    "time": t,
                    "action": "BUY" if amt > 0 else "SELL",
                    "amount": amt,
                    "price": float(self.close.loc[t]),
                }
            )
        return pd.DataFrame(rows)


def run_dca_backtest(
    df: pd.DataFrame,
    symbol: str = "",
    period: str = "1d",
    freq: str = "monthly",
    amount: float = 1000.0,
    commission: float = 0.0005,
    slippage: float = 0.0005,
    mode: str = "fixed",
    ma_window: int = 60,
    boost: float = 2.0,
    dip_window: int = 120,
    dividends: pd.Series | None = None,
    div_policy: str = "reinvest",
) -> DCAResult:
    """执行定投回测。

    Args:
        df: 含 ``close`` 列的 OHLCV DataFrame（时间升序）。
        symbol: 标的代码（用于展示）。
        period: K 线周期（用于展示与非时间索引退化）。
        freq: 定投频率，``daily`` / ``weekly`` / ``monthly``。
        amount: 每期基准投入金额；``value_avg`` 模式下为每期目标市值增量。
        commission: 单边手续费率。
        slippage: 单边滑点率。
        mode: 定投模式，见模块 docstring 与 ``MODES``。
        ma_window: ``ma`` / ``smart`` 模式的均线窗口。
        boost: 加码基准倍数（``ma`` 低于均线倍数；``smart`` / ``dip`` 分档以此缩放）。
        dip_window: ``dip`` 模式的回撤参考高点滚动窗口。
        dividends: 每股现金分红序列（索引为除权除息日）；提供时显式建模分红，
            应搭配不复权价格使用。
        div_policy: 分红处理：``reinvest``（默认，当日收盘价再投入）或
            ``cash``（现金落袋，计入 XIRR 现金流）。

    Returns:
        DCAResult。年化收益率为资金加权 XIRR，并附一次性投入与纯定投两条基准。
    """
    if mode not in MODES:
        raise ValueError(f"未知定投模式 '{mode}'，可选：{', '.join(MODES)}")
    if div_policy not in DIV_POLICIES:
        raise ValueError(f"未知分红策略 '{div_policy}'，可选：{', '.join(DIV_POLICIES)}")

    df = df.reset_index(drop=True)
    close = df["close"].astype(float)
    index = resolve_time_index(df)
    close.index = index

    mask = _contribution_mask(index, freq)
    cost_rate = commission + slippage
    dps = _align_dividends(index, dividends)

    ledger = _simulate(
        close, mask, mode, amount, cost_rate, ma_window, boost, dip_window,
        dps=dps, div_policy=div_policy,
    )
    metrics = compute_dca_metrics(
        ledger["invested"],
        ledger["market_value"],
        ledger["shares"],
        ledger["contribution"],
        ledger["num_contributions"],
        cashflow=ledger["cashflow"],
        dividend_income=ledger["dividend_income"],
        total_dividends=ledger["total_dividends"],
    )
    lumpsum_metrics = compute_lumpsum_metrics(
        close, metrics["total_invested"], cost_rate, dividends=dps
    )

    # 增强模式额外给出「同参数纯定投」基准，用于判断增强是否真的更优
    dca_metrics = None
    if mode != "fixed":
        base = _simulate(
            close, mask, "fixed", amount, cost_rate, ma_window, boost, dip_window,
            dps=dps, div_policy=div_policy,
        )
        dca_metrics = compute_dca_metrics(
            base["invested"],
            base["market_value"],
            base["shares"],
            base["contribution"],
            base["num_contributions"],
            cashflow=base["cashflow"],
            dividend_income=base["dividend_income"],
            total_dividends=base["total_dividends"],
        )

    return DCAResult(
        symbol=symbol,
        period=period,
        freq=freq,
        mode=mode,
        close=close,
        contribution=ledger["contribution"],
        cashflow=ledger["cashflow"],
        invested=ledger["invested"],
        shares=ledger["shares"],
        market_value=ledger["market_value"],
        metrics=metrics,
        lumpsum_metrics=lumpsum_metrics,
        dca_metrics=dca_metrics,
        dividends=ledger["dividends"] if dividends is not None else None,
        div_policy=div_policy if dividends is not None else None,
    )


def _simulate(
    close: pd.Series,
    mask: np.ndarray,
    mode: str,
    amount: float,
    cost_rate: float,
    ma_window: int,
    boost: float,
    dip_window: int,
    dps: np.ndarray | None = None,
    div_policy: str = "reinvest",
) -> dict:
    """按模式推进现金流账本，返回各曲线（Series）与定投期数。

    统一成本模型：买入时成本从买入现金中扣除（份额 = 现金×(1-成本)/价格）；
    卖出时成本从卖出所得中扣除（到手现金 = 市值×(1-成本)）。
    ``cashflow`` 为投资者视角的实际现金流（买入为负、卖出为正），用于 XIRR。
    ``contribution`` 为名义交易金额（买入>0、卖出<0），用于展示与绘图。

    分红（``dps`` 非空时）：除权除息日按**当日定投前**的持仓份额计现金分红，
    ``reinvest`` 当日收盘价再投入（不计入本金与外部现金流）；``cash`` 计入
    正向现金流（落袋，参与 XIRR）。
    """
    price = close.to_numpy(dtype=float)
    n = len(price)
    mult = None if mode == "value_avg" else _multiplier_series(
        close, mode, ma_window, boost, dip_window
    )

    contribution = np.zeros(n)
    cashflow = np.zeros(n)
    invested = np.zeros(n)
    shares_curve = np.zeros(n)
    market_value = np.zeros(n)
    dividend_recv = np.zeros(n)

    shares_cum = 0.0
    invested_cum = 0.0
    dividend_income = 0.0  # cash 策略下累计落袋分红（计入盈亏）
    num = 0
    k = 0  # value_avg 的已定投期数（用于目标市值增长线）
    for i in range(n):
        # 分红：按当日定投前的持仓计（除权日当天买入不享当期分红）
        if dps is not None and dps[i] > 0 and shares_cum > 0 and price[i] > 0:
            cash_div = shares_cum * dps[i]
            dividend_recv[i] = cash_div
            if div_policy == "reinvest":
                shares_cum += cash_div * (1.0 - cost_rate) / price[i]
            else:  # cash：落袋，计入正向现金流
                cashflow[i] += cash_div
                dividend_income += cash_div
        if mask[i] and price[i] > 0:
            if mode == "value_avg":
                k += 1
                target = amount * k
                cur_value = shares_cum * price[i]
                gap = target - cur_value
                if gap >= 0:  # 补仓买入
                    shares_cum += gap * (1.0 - cost_rate) / price[i]
                    invested_cum += gap
                    contribution[i] = gap
                    cashflow[i] = -gap
                    if gap > 0:
                        num += 1
                else:  # 涨过目标，卖出减仓
                    sell_value = min(-gap, cur_value)
                    shares_cum -= sell_value / price[i]
                    proceeds = sell_value * (1.0 - cost_rate)
                    invested_cum -= proceeds
                    contribution[i] = -sell_value
                    cashflow[i] = proceeds
                    num += 1
            else:
                amt = amount * float(mult[i])
                if amt > 0:
                    shares_cum += amt * (1.0 - cost_rate) / price[i]
                    invested_cum += amt
                    contribution[i] = amt
                    cashflow[i] = -amt
                    num += 1
                # amt == 0（smart 暂停档）：不交易
        shares_curve[i] = shares_cum
        invested[i] = invested_cum
        market_value[i] = shares_cum * price[i]

    return {
        "contribution": pd.Series(contribution, index=close.index),
        "cashflow": pd.Series(cashflow, index=close.index),
        "invested": pd.Series(invested, index=close.index),
        "shares": pd.Series(shares_curve, index=close.index),
        "market_value": pd.Series(market_value, index=close.index),
        "num_contributions": num,
        "dividends": pd.Series(dividend_recv, index=close.index),
        "dividend_income": dividend_income,
        "total_dividends": float(dividend_recv.sum()),
    }


def _align_dividends(index: pd.Index, dividends: pd.Series | None) -> np.ndarray | None:
    """把每股分红序列对齐到交易日历：除权日不是交易日时顺延至其后首个交易日。

    超出回测区间的分红直接丢弃；非时间索引时无法对齐，返回 None。
    """
    if dividends is None or len(dividends) == 0:
        return None
    if not isinstance(index, pd.DatetimeIndex):
        return None
    dps = np.zeros(len(index))
    div = dividends.dropna()
    dates = pd.DatetimeIndex(pd.to_datetime(div.index))
    for dt, value in zip(dates, div.to_numpy(dtype=float)):
        if value <= 0:
            continue
        if dt < index[0]:
            continue  # 除权日在回测区间之前（当时尚无持仓）
        pos = int(index.searchsorted(dt, side="left"))
        if pos >= len(index):
            continue  # 除权日在回测区间之后
        dps[pos] += value
    return dps if dps.any() else None



def _contribution_mask(index: pd.Index, freq: str) -> np.ndarray:
    """标记定投日：每个周期（日/周/月）的首个交易日。

    非时间索引时退化为按固定间隔（周≈5 根、月≈21 根）投入。
    """
    n = len(index)
    if freq == "daily":
        return np.ones(n, dtype=bool)

    if not isinstance(index, pd.DatetimeIndex):
        step = {"weekly": 5, "monthly": 21}.get(freq, 21)
        mask = np.zeros(n, dtype=bool)
        mask[::step] = True
        return mask

    if freq == "weekly":
        iso = index.isocalendar()
        keys = list(zip(iso["year"].to_numpy(), iso["week"].to_numpy()))
    elif freq == "monthly":
        keys = list(zip(index.year.to_numpy(), index.month.to_numpy()))
    else:
        raise ValueError(f"未知定投频率 '{freq}'，可选：daily/weekly/monthly")

    mask = np.zeros(n, dtype=bool)
    seen: set = set()
    for i, key in enumerate(keys):
        if key not in seen:
            seen.add(key)
            mask[i] = True
    return mask


def _multiplier_series(
    close: pd.Series, mode: str, ma_window: int, boost: float, dip_window: int
) -> np.ndarray:
    """各模式下每期投入的倍数序列（相对基准 amount）。

    - ``fixed``：恒为 1（纯定投）。
    - ``ma``   ：收盘价低于均线投 ``boost`` 倍，否则 1 倍（单档）。
    - ``smart``：按偏离均线幅度分档（越便宜投越多、越贵越少乃至暂停）。
    - ``dip``  ：按距近期高点的回撤深度分档，叠加 RSI 超卖再加一档。

    均线/回撤/RSI 均用截至当日（含）数据计算，买入在当日收盘价，不引入未来信息。
    """
    c = close.to_numpy(dtype=float)
    n = len(c)

    if mode == "fixed":
        return np.ones(n)

    if mode == "ma":
        ma = close.rolling(ma_window).mean().to_numpy()
        mult = np.where(c < ma, boost, 1.0)
        mult[np.isnan(ma)] = 1.0
        return mult

    if mode == "smart":
        ma = close.rolling(ma_window).mean().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            dev = c / ma - 1.0  # 相对均线偏离
        mid = 1.0 + (boost - 1.0) * 0.5
        mult = np.ones(n)
        mult = np.where((dev > -0.15) & (dev <= -0.05), mid, mult)
        mult = np.where(dev <= -0.15, boost, mult)
        mult = np.where((dev >= 0.05) & (dev < 0.15), 0.5, mult)
        mult = np.where(dev >= 0.15, 0.0, mult)  # 太贵，暂停定投
        mult[np.isnan(ma)] = 1.0
        return mult

    if mode == "dip":
        peak = close.rolling(dip_window, min_periods=1).max().to_numpy()
        dd = c / peak - 1.0  # 距近期高点回撤（<=0）
        mild = 1.0 + (boost - 1.0) * 0.5
        deep = boost * 1.5
        mult = np.ones(n)
        mult = np.where(dd <= -0.05, mild, mult)
        mult = np.where(dd <= -0.15, boost, mult)
        mult = np.where(dd <= -0.30, deep, mult)
        # RSI 超卖（<30）额外加一档，封顶 deep
        rsi = _rsi(close, 14).to_numpy()
        mult = np.where(rsi < 30.0, np.minimum(mult + 0.5, deep), mult)
        return mult

    raise ValueError(f"未知定投模式 '{mode}'")


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 平滑 RSI；未形成前填充中性值 50。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)
