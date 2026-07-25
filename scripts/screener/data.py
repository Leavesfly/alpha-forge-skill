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
    """A 股单标的深度财务指标：ROE/资产负债率/净利润增速/营收增速/资产增速/每股经营现金流。

    调用 ``ak.stock_financial_analysis_indicator(symbol, start_year)``（新浪财务分析指标）。
    注意：start_year 缺省值（1900）会返回空表，必须传近年份；取近两年保证至少有一个年报期。

    Returns:
        ``{"roe", "debt_ratio", "profit_growth", "revenue_growth", "asset_growth", "ocf_per_share"}``
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
        "revenue_growth": None, "asset_growth": None, "ocf_per_share": None,
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

    # 主营业务收入增长率（百倍股维度：增长须由营收驱动，防纯削减成本的假增长）
    rev_col = _find_col(df, [
        "主营业务收入增长率(%)", "主营业务收入增长率", "营业收入增长率", "revenue_growth",
    ])
    if rev_col:
        val = pd.to_numeric(latest.get(rev_col), errors="coerce")
        if pd.notna(val):
            result["revenue_growth"] = float(val)

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


#: 同基准收盘序列的进程内缓存（A 股全市场扫描时数千只标的共用一个基准）
_BENCH_CACHE: dict[str, pd.Series | None] = {}


def fetch_benchmark_close(symbol: str, lookback: int = 260) -> pd.Series | None:
    """标的对应默认基准的收盘序列（RS 线与大势判断用）。

    基准映射复用 ``scoring.default_benchmark``（A 股→510300.SH、
    港股→02800.HK、美股→SPY.US）；同一基准只拉取一次（进程内缓存）。

    Returns:
        基准收盘价 Series；无基准或拉取失败返回 None。
    """
    from scoring import default_benchmark

    bench = default_benchmark(symbol)
    if bench is None:
        return None
    if bench in _BENCH_CACHE:
        return _BENCH_CACHE[bench]
    close: pd.Series | None = None
    try:
        from datafeed import fetch_ohlcv

        df = fetch_ohlcv(bench, period="1d", count=lookback)
        if df is not None and len(df):
            close = df["close"].astype(float).reset_index(drop=True)
    except Exception:
        close = None
    _BENCH_CACHE[bench] = close
    return close


def fetch_technical_profile(
    symbol: str,
    benchmark_close: pd.Series | None = None,
    lookback: int = 260,
) -> dict | None:
    """猛兽股技术面画像：52 周位置 / MA50-MA200 趋势 / 加权 RS / 量价关系。

    取自波伊克《猛兽股》总结的共同技术特征：接近 52 周新高（买强
    不买弱）、沿 MA50 上行的多头结构、RS 线跑赢基准、上涨放量下跌
    缩量（吸筹重于派发）。走 datafeed 免费日 K（260 根 ≈ 52 周 + RS 窗口）。

    Returns:
        ``{"price_pos", "close", "ma50", "ma200", "trend_ok", "rs_excess",
        "updown_vol_ratio"}``；rs_excess 为相对基准的超额百分点（无基准时
        None）；日 K 不足 200 根或拉取失败返回 None。
    """
    try:
        from datafeed import fetch_ohlcv

        df = fetch_ohlcv(symbol, period="1d", count=lookback)
    except Exception:
        return None

    if df is None or len(df) < 200:  # MA200/52 周高点至少需要 200 根
        return None

    close = df["close"].astype(float).reset_index(drop=True)
    last = float(close.iloc[-1])

    # 52 周价格位置（与 fetch_price_position 同口径）
    high = float(df["high"].max()) if "high" in df.columns else float(close.max())
    low = float(df["low"].min()) if "low" in df.columns else float(close.min())
    price_pos = (last - low) / (high - low) if high > low else None

    # 多头趋势结构：收盘站上 MA50 且 MA50 在 MA200 上（沿 50 日线上行）
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    trend_ok = last > ma50 > ma200

    # RS 线：加权相对强度（3/6/9/12 个月，复用 CAN SLIM 的 IBD RS 近似）
    from canslim.engine import rs_weighted_return

    rs_excess = None
    rs_raw = rs_weighted_return(close)
    if rs_raw is not None and benchmark_close is not None:
        bench_rs = rs_weighted_return(benchmark_close.dropna().astype(float))
        if bench_rs is not None:
            rs_excess = (rs_raw - bench_rs) * 100.0  # 百分点

    # 量价关系：近 50 日上涨日均量 / 下跌日均量（吸筹 vs 派发）
    updown = None
    if "volume" in df.columns and float(df["volume"].tail(50).sum()) > 0:
        chg = close.diff().tail(50).reset_index(drop=True)
        vol = df["volume"].astype(float).tail(50).reset_index(drop=True)
        up_vol = float(vol[chg > 0].mean()) if (chg > 0).any() else 0.0
        down_vol = float(vol[chg < 0].mean()) if (chg < 0).any() else 0.0
        if down_vol > 0:
            updown = up_vol / down_vol
        elif up_vol > 0:
            updown = 99.0  # 近 50 日无下跌日：极端强势，封顶避免 inf 破坏 JSON

    return {
        "price_pos": price_pos,
        "close": last,
        "ma50": ma50,
        "ma200": ma200,
        "trend_ok": trend_ok,
        "rs_excess": rs_excess,
        "updown_vol_ratio": updown,
    }


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

    # yfinance revenueGrowth 为小数（如 0.15 = 15%），转百分数
    rev_raw = info.get("revenueGrowth")
    revenue_growth = rev_raw * 100.0 if rev_raw is not None else None

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
        "revenue_growth": revenue_growth,
        "total_mv": total_mv,
        "cash_yield": cash_yield,
        "price_pos": price_pos,
        "asset_growth": None,  # yfinance .info 无资产增速，聪明增长维度仅 A 股支持
    }
