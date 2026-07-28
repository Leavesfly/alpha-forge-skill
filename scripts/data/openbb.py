"""OpenBB 基本面数据适配层：港股/美股关键指标 / EPS 序列 / 研发强度 / 分红。

K 线之外进一步利用 OpenBB（Open Data Platform）的多样化数据能力：

- ``fetch_obb_metrics``：估值/质量/增速关键指标（valuation PE/PB Band 等用）；
- ``fetch_obb_eps``：季度/年度摊薄 EPS 序列（CAN SLIM C/A 检查用）；
- ``fetch_obb_rd_ratio``：研发强度 %（费雪筛选研发维度用）；
- ``fetch_obb_dividends``：每股分红历史（DCA 显式分红建模用）。

均经 openbb 的 yfinance provider（免费无需 Key），输出归一为项目口径
（百分数指标统一为百分数，序列升序）。接口异常向上抛出，由调用方决定
降级（各调用点保留原 yfinance 直连路径兜底）。

注意：openbb 内部走 anyio portal，anyio<4 会报 "This portal is not running"，
项目依赖已约束 anyio>=4.0。
"""

from __future__ import annotations

import pandas as pd

from data.sources import _to_yahoo_symbol
from errors import DataFetchError

#: OpenBB 基本面覆盖的市场后缀（与 K 线主力源一致）
HKUS_SUFFIXES = (".HK", ".US")


def supports_hkus(symbol: str) -> bool:
    """该标的是否可走 OpenBB 港美股基本面（港股代码需纯数字）。"""
    if not symbol.upper().endswith(HKUS_SUFFIXES):
        return False
    code = symbol.rsplit(".", 1)[0]
    if symbol.upper().endswith(".HK") and not code.isdigit():
        return False
    return True


def _pct(v) -> float | None:
    """小数比率 -> 百分数（0.168 -> 16.8）；None 透传。"""
    return float(v) * 100.0 if v is not None else None


def fetch_obb_metrics(symbol: str) -> dict:
    """港美股关键指标（估值/质量/增速），归一为项目口径。

    Returns:
        ``{"pe", "pb", "roe", "div_yield", "debt_ratio", "profit_growth",
        "revenue_growth", "gross_margin", "total_mv", "bvps"}``；
        百分数指标已转百分数，市值单位为亿（原币种）。

    Raises:
        DataFetchError: 接口未返回数据时。
    """
    from openbb import obb

    df = obb.equity.fundamental.metrics(
        _to_yahoo_symbol(symbol), provider="yfinance"
    ).to_dataframe()
    if df is None or len(df) == 0:
        raise DataFetchError(f"openbb 未返回 {symbol} 的关键指标。")
    row = df.iloc[0]

    def _get(col):
        v = row.get(col)
        return None if v is None or pd.isna(v) else float(v)

    market_cap = _get("market_cap")
    return {
        "pe": _get("pe_ratio"),
        "pb": _get("price_to_book"),
        # yfinance 口径：ROE/股息率/增速/毛利率为小数，负债权益比已是百分数
        "roe": _pct(_get("return_on_equity")),
        "div_yield": _pct(_get("dividend_yield")),
        "debt_ratio": _get("debt_to_equity"),
        "profit_growth": _pct(_get("earnings_growth")),
        "revenue_growth": _pct(_get("revenue_growth")),
        "gross_margin": _pct(_get("gross_margin")),
        "total_mv": market_cap / 1e8 if market_cap else None,
        "bvps": _get("book_value"),
    }


def _eps_from_income(df: pd.DataFrame | None) -> pd.Series | None:
    """从 openbb 利润表提取摊薄 EPS 序列（升序，优先摊薄再基本）。"""
    if df is None or len(df) == 0 or "period_ending" not in df.columns:
        return None
    for col in ("diluted_earnings_per_share", "basic_earnings_per_share"):
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            idx = pd.to_datetime(df["period_ending"], errors="coerce")
            series = pd.Series(s.to_numpy(dtype=float), index=idx).dropna().sort_index()
            if len(series):
                return series
    return None


def fetch_obb_eps(symbol: str) -> tuple[pd.Series | None, pd.Series | None]:
    """港美股季度 + 年度摊薄 EPS 序列（财年口径，升序）。

    yfinance provider 的 limit 上限为 5：季度约 5 期（同比检查刚好够），
    年度约 4~5 个财年（满足 CAN SLIM A 项 ≥3 年要求）。

    Returns:
        ``(eps_quarterly, eps_annual)``，各自无数据时为 None。
    """
    from openbb import obb

    ticker = _to_yahoo_symbol(symbol)
    eps_q = _eps_from_income(
        obb.equity.fundamental.income(
            ticker, period="quarter", provider="yfinance", limit=5
        ).to_dataframe()
    )
    eps_a = _eps_from_income(
        obb.equity.fundamental.income(
            ticker, period="annual", provider="yfinance", limit=5
        ).to_dataframe()
    )
    return eps_q, eps_a


def fetch_obb_rd_ratio(symbol: str) -> float | None:
    """港美股最新财年研发强度(%)：研发费用 / 营业总收入 × 100。

    Returns:
        研发占比百分数；无研发科目（未披露）返回 None。

    Raises:
        DataFetchError: 接口未返回利润表时。
    """
    from openbb import obb

    df = obb.equity.fundamental.income(
        _to_yahoo_symbol(symbol), period="annual", provider="yfinance", limit=5
    ).to_dataframe()
    if df is None or len(df) == 0:
        raise DataFetchError(f"openbb 未返回 {symbol} 的年度利润表。")
    if "research_and_development_expense" not in df.columns or "total_revenue" not in df.columns:
        return None  # 无研发科目：视为未披露研发投入
    rd = pd.to_numeric(df["research_and_development_expense"], errors="coerce")
    rev = pd.to_numeric(df["total_revenue"], errors="coerce")
    dates = pd.to_datetime(df.get("period_ending"), errors="coerce")
    # 取最新一期两列同时非空的记录
    frame = pd.DataFrame({"rd": rd, "rev": rev, "date": dates}).dropna()
    frame = frame[frame["rev"] > 0].sort_values("date")
    if not len(frame):
        return None
    last = frame.iloc[-1]
    return float(last["rd"] / last["rev"] * 100.0)


def fetch_obb_dividends(symbol: str) -> pd.Series:
    """港美股每股现金分红历史（索引为除息日，升序，原币种）。

    供 run_dca.py 显式分红建模（--dividends auto）使用，应搭配不复权价格
    （与 A 股 akshare 路径同约定）。

    Returns:
        每股分红 Series（float，DatetimeIndex 为除息日，升序）。

    Raises:
        DataFetchError: 接口未返回记录或无有效分红时。
    """
    from openbb import obb

    df = obb.equity.fundamental.dividends(
        _to_yahoo_symbol(symbol), provider="yfinance"
    ).to_dataframe()
    if df is None or len(df) == 0:
        raise DataFetchError(f"openbb 未返回 {symbol} 的分红记录（可能从未分红）。")
    if "ex_dividend_date" not in df.columns or "amount" not in df.columns:
        raise DataFetchError(
            f"openbb 分红数据列名不兼容（实际列：{list(df.columns)}）。"
        )
    dps = pd.to_numeric(df["amount"], errors="coerce")
    dates = pd.to_datetime(df["ex_dividend_date"], errors="coerce")
    series = pd.Series(dps.to_numpy(dtype=float), index=pd.DatetimeIndex(dates))
    series = series[series > 0].dropna().sort_index()
    if series.empty:
        raise DataFetchError(f"{symbol} 无有效现金分红记录（可能从未分红）。")
    return series
