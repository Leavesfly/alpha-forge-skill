"""分红数据获取：A 股现金分红历史（akshare 数据源）。

从 datafeed.py 拆分而来，职责单一化。
"""

from __future__ import annotations

import contextlib
import sys

import pandas as pd

from market import SYMBOL_FORMAT_HINT, SYMBOL_RE


def fetch_dividends(symbol: str) -> pd.Series:
    """拉取 A 股现金分红历史（每股派现，索引为除权除息日）。

    数据源 akshare 分红送配详情（东财），无需 API Key；仅支持 A 股。
    供 run_dca.py 显式分红建模（--dividends auto）使用，应搭配不复权价格。

    Returns:
        每股现金分红 Series（float，DatetimeIndex 为除权除息日，升序）。

    Raises:
        RuntimeError: 非 A 股、接口异常或无分红记录时。
    """
    if not SYMBOL_RE.match((symbol or "").strip()):
        raise ValueError(f"标的代码不合法：'{symbol}'。{SYMBOL_FORMAT_HINT}")
    if not symbol.upper().endswith((".SH", ".SZ", ".BJ")):
        raise RuntimeError(
            f"分红数据目前仅支持 A 股（收到 {symbol}）；"
            "其他市场可用 --dividends <CSV> 提供（列：date,dps）。"
        )
    import akshare as ak

    code = symbol.split(".")[0]
    try:
        with contextlib.redirect_stdout(sys.stderr):
            df = ak.stock_fhps_detail_em(symbol=code)
    except TypeError:
        # akshare 内部在 API 返回 result=None 时抛 TypeError（股票从未分红或北交所等）
        raise RuntimeError(
            f"{symbol} 无分红记录（可能从未分红，或北交所/科创板新股不支持该接口）；"
            "如需分红建模可用 --dividends <CSV> 提供（列：date,dps）。"
        ) from None
    if df is None or len(df) == 0:
        raise RuntimeError(f"akshare 未返回 {symbol} 的分红记录。")

    # 防御式取列：「现金分红-现金分红比例」为每 10 股派现金额
    div_col = next((c for c in df.columns if "现金分红比例" in str(c)), None)
    date_col = next((c for c in df.columns if "除权除息日" in str(c)), None)
    if div_col is None or date_col is None:
        raise RuntimeError(
            f"分红数据列名不兼容（实际列：{list(df.columns)}），"
            "可能 akshare 接口变更；可改用 --dividends <CSV> 提供（列：date,dps）。"
        )
    out = df[[date_col, div_col]].dropna()
    dps = pd.to_numeric(out[div_col], errors="coerce") / 10.0  # 每 10 股 -> 每股
    dates = pd.to_datetime(out[date_col], errors="coerce")
    series = pd.Series(dps.to_numpy(), index=pd.DatetimeIndex(dates))
    series = series[series > 0].dropna().sort_index()
    if series.empty:
        raise RuntimeError(f"{symbol} 无有效现金分红记录（可能从未分红）。")
    return series
