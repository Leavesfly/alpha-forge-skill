"""新闻情绪策略：逐条情绪分 -> 日度情绪 -> {-1,0,1} 信号 -> 复用回测引擎。

信号逻辑：
- 将逐条情绪分按「日」聚合为日度情绪，对齐到交易日；
- 无新闻日按新闻的持续效应前向填充有限天数（hold）；
- 平滑后用「中性带阈值」转为持仓：情绪 > entry 做多，< -entry 且允许做空则做空，
  介于其间空仓。信号交由回测引擎 shift(1) 次日生效，规避前视。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult, run_backtest
from strategies.base import Strategy


class _PrecomputedSignalStrategy(Strategy):
    """承载预先算好的信号，供回测引擎复用（自包含，避免跨模块耦合）。"""

    name = "sentiment"
    display_name = "新闻情绪"

    def __init__(self, signals: np.ndarray, **params):
        super().__init__(**params)
        self._signals = np.asarray(signals, dtype=float)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n = len(df)
        sig = self._signals
        if len(sig) != n:
            sig = np.resize(sig, n)
        return pd.Series(sig, index=df.index)


@dataclass
class SentimentResult:
    """新闻情绪策略结果容器。"""

    backtest: BacktestResult
    daily_sentiment: pd.Series
    n_news: int = 0
    n_days_with_news: int = 0
    entry: float = 0.2
    exit: float = 0.05
    hold: int = 5


def _date_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    """从常见时间列构造（按日归一化的）日期索引。"""
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            return pd.to_datetime(df[col]).dt.normalize()
    return pd.to_datetime(pd.RangeIndex(len(df)).astype(str), errors="coerce")


def aggregate_daily(scores: pd.DataFrame) -> pd.Series:
    """将逐条情绪分按日聚合为日度情绪均值。"""
    s = scores.copy()
    s["day"] = pd.to_datetime(s["date"]).dt.normalize()
    return s.groupby("day")["score"].mean().sort_index()


def run_sentiment_strategy(
    df: pd.DataFrame,
    scores: pd.DataFrame,
    symbol: str = "",
    period: str = "1d",
    entry: float = 0.2,
    exit: float = 0.05,
    hold: int = 5,
    smooth: int = 3,
    allow_short: bool = False,
    commission: float = 0.0005,
    slippage: float = 0.0005,
) -> SentimentResult:
    """基于新闻情绪分回测。

    Args:
        df: OHLCV DataFrame（时间升序）。
        scores: 逐条情绪分 DataFrame（date/score）。
        entry: 开仓情绪阈值（|情绪| 超过才入场）。
        exit: 中性带下限（保留参数，用于阈值语义说明）。
        hold: 新闻情绪的持续天数（无新闻日前向填充上限）。
        smooth: 日度情绪的滚动平滑窗口。
        allow_short: 是否允许做空（极端利空输出 -1）。

    Returns:
        SentimentResult。
    """
    df = df.reset_index(drop=True)
    dates = _date_index(df)
    n = len(df)

    daily = aggregate_daily(scores)
    n_days_with_news = int(daily.notna().sum())

    # 对齐到交易日：无新闻日先置 NaN，再按持续效应前向填充有限天数
    aligned = daily.reindex(dates.unique())
    aligned = aligned.ffill(limit=hold)
    # 映射回每根 K 线
    sent_by_bar = dates.map(aligned).astype(float)
    sent_by_bar = pd.Series(sent_by_bar, index=range(n))
    smoothed = sent_by_bar.rolling(smooth, min_periods=1).mean().fillna(0.0)

    short_val = -1.0 if allow_short else 0.0
    signals = np.where(
        smoothed.to_numpy() > entry, 1.0,
        np.where(smoothed.to_numpy() < -entry, short_val, 0.0),
    )

    strategy = _PrecomputedSignalStrategy(signals, allow_short=allow_short)
    backtest = run_backtest(
        df, strategy, symbol=symbol, period=period,
        commission=commission, slippage=slippage,
    )

    daily_sentiment = pd.Series(smoothed.to_numpy(), index=backtest.equity.index)

    return SentimentResult(
        backtest=backtest,
        daily_sentiment=daily_sentiment,
        n_news=len(scores),
        n_days_with_news=n_days_with_news,
        entry=entry,
        exit=exit,
        hold=hold,
    )
