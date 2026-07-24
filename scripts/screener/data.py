"""价值筛选数据获取：A 股批量快照（akshare）+ 逐只深度指标 + 港美股（yfinance）。

数据源策略：
- A 股 Phase 1：``ak.stock_zh_a_spot_em()`` 一次拉取全市场 PE/PB/市值/名称；
- A 股 Phase 2：``ak.stock_financial_analysis_indicator(symbol)`` 逐只取 ROE/负债率/增速；
- 港美股：``yf.Ticker(sym).info`` 逐只取全部指标（无免费批量接口）。

所有接口异常返回 None（调用方跳过该标的，不中断整体扫描）。
"""

from __future__ import annotations

import contextlib
import sys
from typing import Callable

import pandas as pd

#: A 股市场后缀
_A_SUFFIXES = (".SH", ".SZ", ".BJ")

#: akshare spot_em 列名候选（版本间可能微调）
_SPOT_COL_MAP = {
    "code": ["代码", "股票代码", "code"],
    "name": ["名称", "股票名称", "name"],
    "close": ["最新价", "收盘价", "close"],
    "pe": ["市盈率-动态", "市盈率(动态)", "市盈率", "pe"],
    "pb": ["市净率", "pb"],
    "total_mv": ["总市值", "total_mv"],
    "div_yield": ["股息率", "dividend_yield"],
}


def is_a_share(symbol: str) -> bool:
    """是否 A 股标的。"""
    return symbol.upper().endswith(_A_SUFFIXES)


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """在 DataFrame 列中按候选名模糊匹配第一个存在的列。"""
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    # 退化：包含匹配
    for cand in candidates:
        for orig, real in cols_lower.items():
            if cand.lower() in orig:
                return real
    return None


def fetch_astock_snapshot(log: Callable[..., None] | None = None) -> pd.DataFrame | None:
    """A 股全市场快照：代码/名称/PE/PB/总市值/股息率。

    调用 ``ak.stock_zh_a_spot_em()``（东财实时行情，含 PE/PB/市值），
    一次返回全部 A 股（~5000 只），无需 API Key。

    Returns:
        归一化 DataFrame（列：code, name, close, pe, pb, total_mv, div_yield）；
        接口异常时返回 None。
    """
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            raw = ak.stock_zh_a_spot_em()
    except Exception as exc:
        if log:
            log(f"[warn] akshare 全市场快照拉取失败（{type(exc).__name__}: {exc}）")
        return None

    if raw is None or len(raw) == 0:
        if log:
            log("[warn] akshare 全市场快照返回空数据")
        return None

    # 列名归一化
    df = pd.DataFrame()
    for std_name, candidates in _SPOT_COL_MAP.items():
        col = _find_col(raw, candidates)
        if col is not None:
            df[std_name] = raw[col].values
        else:
            df[std_name] = None

    # 数值列转换
    for col in ("close", "pe", "pb", "total_mv", "div_yield"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 总市值：东财口径为元，转为亿
    if "total_mv" in df.columns:
        # 如果数值范围看起来是元（>1e8），转亿
        median_mv = df["total_mv"].median()
        if pd.notna(median_mv) and median_mv > 1e8:
            df["total_mv"] = df["total_mv"] / 1e8

    df = df.dropna(subset=["code"]).reset_index(drop=True)
    return df


def fetch_astock_detail(code: str) -> dict | None:
    """A 股单标的深度财务指标：ROE/资产负债率/净利润增速/资产增速/每股经营现金流。

    调用 ``ak.stock_financial_analysis_indicator(symbol, start_year)``（新浪财务分析指标）。
    注意：start_year 缺省值（1900）会返回空表，必须传近年份；取近两年保证至少有一个年报期。

    Returns:
        ``{"roe", "debt_ratio", "profit_growth", "asset_growth", "ocf_per_share"}``
        （值均为 float|None）；接口异常时返回 None。
    """
    try:
        from datetime import date

        import akshare as ak

        start_year = str(date.today().year - 1)
        with contextlib.redirect_stdout(sys.stderr):
            df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
    except Exception:
        return None

    if df is None or len(df) == 0:
        return None

    result: dict = {
        "roe": None, "debt_ratio": None, "profit_growth": None,
        "asset_growth": None, "ocf_per_share": None,
    }

    # 取最新一期（按日期降序取第一行）
    date_col = _find_col(df, ["日期", "报告期", "date"])
    if date_col:
        df = df.sort_values(date_col, ascending=False)
    latest = df.iloc[0] if len(df) else None
    if latest is None:
        return result

    # ROE（净资产收益率）
    roe_col = _find_col(df, ["净资产收益率(%)", "净资产收益率", "加权净资产收益率", "roe"])
    if roe_col:
        val = pd.to_numeric(latest.get(roe_col), errors="coerce")
        if pd.notna(val):
            result["roe"] = float(val)

    # 资产负债率
    debt_col = _find_col(df, ["资产负债率(%)", "资产负债率", "debt_ratio"])
    if debt_col:
        val = pd.to_numeric(latest.get(debt_col), errors="coerce")
        if pd.notna(val):
            result["debt_ratio"] = float(val)

    # 净利润同比增长率
    growth_col = _find_col(df, [
        "净利润增长率(%)", "净利润同比增长率", "净利润增长率",
        "归属净利润同比增长率", "profit_growth",
    ])
    if growth_col:
        val = pd.to_numeric(latest.get(growth_col), errors="coerce")
        if pd.notna(val):
            result["profit_growth"] = float(val)

    # 总资产增长率（十倍股「聪明增长」维度：资产增速应低于利润增速）
    asset_col = _find_col(df, ["总资产增长率(%)", "总资产增长率", "asset_growth"])
    if asset_col:
        val = pd.to_numeric(latest.get(asset_col), errors="coerce")
        if pd.notna(val):
            result["asset_growth"] = float(val)

    # 每股经营性现金流（现金流收益率 = 每股经营现金流 / 股价，FCF Yield 的免费近似）
    ocf_col = _find_col(df, ["每股经营性现金流(元)", "每股经营性现金流", "ocf_per_share"])
    if ocf_col:
        val = pd.to_numeric(latest.get(ocf_col), errors="coerce")
        if pd.notna(val):
            result["ocf_per_share"] = float(val)

    return result


def fetch_price_position(symbol: str, lookback: int = 250) -> float | None:
    """52 周价格位置：(当前价 - 区间最低) / (区间最高 - 区间最低)，取值 0~1。

    十倍股研究（Yartseva 2025）发现多数十倍股从 12 个月低点附近启动，
    低位置（左侧）优于追高。走 datafeed 免费日 K（约 250 交易日 ≈ 52 周）。

    Returns:
        0~1 的位置值；数据不足或拉取失败返回 None。
    """
    try:
        from datafeed import fetch_ohlcv

        df = fetch_ohlcv(symbol, period="1d", count=lookback)
    except Exception:
        return None

    if df is None or len(df) < 60:  # 至少一个季度数据才有意义
        return None

    close = float(df["close"].iloc[-1])
    high = float(df["high"].max()) if "high" in df.columns else float(df["close"].max())
    low = float(df["low"].min()) if "low" in df.columns else float(df["close"].min())
    if high <= low:
        return None
    return (close - low) / (high - low)


def fetch_yfinance_metrics(symbol: str) -> dict | None:
    """港美股单标的指标：PE/PB/ROE/股息率/负债率/增速（yfinance .info）。

    Returns:
        归一化指标 dict；接口异常时返回 None。
    """
    try:
        import yfinance as yf

        from data.sources import _to_yahoo_symbol

        ticker = yf.Ticker(_to_yahoo_symbol(symbol))
        info = ticker.info or {}
    except Exception:
        return None

    if not info:
        return None

    # yfinance ROE 为小数（如 0.168 = 16.8%），转百分数
    roe_raw = info.get("returnOnEquity")
    roe = roe_raw * 100.0 if roe_raw is not None else None

    # yfinance dividendYield 为小数（如 0.042 = 4.2%），转百分数
    div_raw = info.get("dividendYield")
    div_yield = div_raw * 100.0 if div_raw is not None else None

    # yfinance debtToEquity 为百分比（如 85.3 = 85.3%），已是百分数
    debt_ratio = info.get("debtToEquity")

    # yfinance earningsGrowth 为小数（如 0.12 = 12%），转百分数
    growth_raw = info.get("earningsGrowth")
    profit_growth = growth_raw * 100.0 if growth_raw is not None else None

    # 市值：yfinance 为美元/港元，转亿（近似）
    market_cap = info.get("marketCap")
    total_mv = market_cap / 1e8 if market_cap else None

    # 现金流收益率：自由现金流 / 市值（十倍股研究的最强单一预测因子）
    fcf = info.get("freeCashflow")
    cash_yield = fcf / market_cap * 100.0 if fcf is not None and market_cap else None

    # 52 周价格位置：(现价 - 52周低) / (52周高 - 52周低)
    close = info.get("currentPrice") or info.get("regularMarketPrice")
    hi, lo = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow")
    price_pos = None
    if close is not None and hi is not None and lo is not None and hi > lo:
        price_pos = (close - lo) / (hi - lo)

    return {
        "name": info.get("shortName") or info.get("longName") or symbol,
        "close": info.get("currentPrice") or info.get("regularMarketPrice"),
        "pe": info.get("trailingPE"),
        "pb": info.get("priceToBook"),
        "roe": roe,
        "div_yield": div_yield,
        "debt_ratio": debt_ratio,
        "profit_growth": profit_growth,
        "total_mv": total_mv,
        "cash_yield": cash_yield,
        "price_pos": price_pos,
        "asset_growth": None,  # yfinance .info 无资产增速，聪明增长维度仅 A 股支持
    }
