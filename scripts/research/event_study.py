"""事件研究（Event Study）：AAR / CAAR 分析。

给定一组事件日期（财报、分红、重大公告等），统计事件窗内的
（超额）收益均值 AAR 与累计均值 CAAR，回答「该类事件前后
价格平均如何反应」。

- 超额收益：提供基准（如指数/行业 ETF）时为 r_stock - r_benchmark，
  否则退化为原始收益；
- 事件日对齐：非交易日自动对齐到其后最近的交易日；
- 事件窗不完整（贴近样本边界）的事件自动剔除并计数。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def event_study(
    prices: pd.Series,
    event_dates: list,
    window: tuple[int, int] = (-10, 20),
    benchmark: pd.Series | None = None,
) -> dict:
    """事件窗 AAR/CAAR 计算。

    Args:
        prices: 收盘价序列（DatetimeIndex，升序）。
        event_dates: 事件日期列表（str 或 Timestamp）。
        window: 事件窗（相对交易日），如 (-10, 20)。
        benchmark: 可选基准收盘价序列（与 prices 同频）。

    Returns:
        {
          "table": DataFrame（index=相对交易日，列 AAR/CAAR），
          "n_used": 参与统计的事件数,
          "n_skipped": 因窗口不完整被剔除的事件数,
          "per_event": DataFrame（各事件的窗口累计超额收益）,
        }
    """
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("prices 需要 DatetimeIndex（升序）")
    pre, post = int(window[0]), int(window[1])
    if pre > 0 or post < 0 or post - pre < 1:
        raise ValueError(f"非法事件窗 {window}，应形如 (-10, 20)")

    ret = prices.pct_change()
    if benchmark is not None:
        bench_ret = benchmark.pct_change().reindex(ret.index)
        abnormal = (ret - bench_ret).dropna()
    else:
        abnormal = ret.dropna()

    idx = abnormal.index
    rel_days = np.arange(pre, post + 1)
    rows = []
    per_event = {}
    n_skipped = 0
    for d in event_dates:
        ts = pd.Timestamp(d)
        pos = int(idx.searchsorted(ts))  # 非交易日对齐到其后最近交易日
        if pos >= len(idx):
            n_skipped += 1
            continue
        lo, hi = pos + pre, pos + post
        if lo < 0 or hi >= len(idx):
            n_skipped += 1  # 窗口越界，剔除
            continue
        seg = abnormal.iloc[lo : hi + 1].to_numpy()
        rows.append(seg)
        per_event[str(ts)[:10]] = float(np.prod(1.0 + seg) - 1.0)

    if not rows:
        raise RuntimeError(
            "没有任何事件的窗口落在样本区间内，请检查事件日期与 --count。"
        )

    mat = np.vstack(rows)  # 事件 × 相对日
    aar = mat.mean(axis=0)
    caar = np.cumsum(aar)
    table = pd.DataFrame({"AAR": aar, "CAAR": caar}, index=rel_days)
    table.index.name = "rel_day"

    return {
        "table": table,
        "n_used": len(rows),
        "n_skipped": n_skipped,
        "per_event": pd.DataFrame(
            {"cum_abnormal_return": per_event}
        ).rename_axis("event_date"),
    }
