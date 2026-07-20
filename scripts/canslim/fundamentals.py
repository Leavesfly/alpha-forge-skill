"""CAN SLIM 基本面数据获取：A 股季度 EPS / ROE（akshare）+ 港美股（yfinance）。

C（当季 EPS 增长）与 A（年度 EPS 复合增长）需要按报告期的每股收益序列：
- A 股：akshare 财务摘要接口（东财口径，累计/YTD 值，无需 API Key）；
- 港股/美股：yfinance 利润表（单季/年度摊薄 EPS，无需 Key）自动兜底，
  以 ``eps_quarterly`` / ``eps_annual`` 预拆分序列返回（财年口径）。
接口异常或列名变更时返回 None（调用方降级：C/A 标注 unavailable）。

离线场景可用 CSV 注入（列：period_end,eps[,roe]，eps 为报告期
累计每股收益，roe 为百分比或小数均可，自动识别）。
"""

from __future__ import annotations

import contextlib
import sys

import pandas as pd

#: A 股市场后缀
A_SHARE_SUFFIXES = (".SH", ".SZ", ".BJ")

#: yfinance 兜底覆盖的市场后缀
YF_SUFFIXES = (".HK", ".US")


def is_a_share(symbol: str) -> bool:
    """是否 A 股标的（基本面自动获取仅支持 A 股）。"""
    return symbol.upper().endswith(A_SHARE_SUFFIXES)


def fetch_fundamentals(symbol: str) -> dict | None:
    """拉取按报告期的 EPS（与可选 ROE）序列，自动适配市场。

    Returns:
        A 股：``{"eps": Series(累计/YTD), "roe": Series | None, "source": "akshare"}``；
        港美股：``{"eps_quarterly": Series(单季), "eps_annual": Series(年度),
        "roe": Series | None, "source": "yfinance"}``（财年口径）。
        获取失败时返回 None（stderr 告警，不中断主流程）。
    """
    if is_a_share(symbol):
        return _fetch_akshare(symbol)
    if symbol.upper().endswith(YF_SUFFIXES):
        return _fetch_yfinance(symbol)
    return None


def _fetch_akshare(symbol: str) -> dict | None:
    """A 股：akshare 财务摘要（累计/YTD EPS + ROE）。"""
    try:
        import akshare as ak

        code = symbol.split(".")[0]
        # akshare 可能向 stdout 打印进度，重定向保证 --json 的 stdout 纯净
        with contextlib.redirect_stdout(sys.stderr):
            df = ak.stock_financial_abstract(symbol=code)
        return _parse_abstract(df)
    except Exception as exc:
        print(
            f"[warn] 获取 {symbol} 财务摘要失败（{type(exc).__name__}: {exc}），"
            "C/A 基本面检查将跳过；可用 --fundamentals-csv 手动提供。",
            file=sys.stderr,
        )
        return None


def _fetch_yfinance(symbol: str) -> dict | None:
    """港美股：yfinance 利润表摊薄 EPS（单季 + 年度，财年口径）。

    季度表通常仅返回近 4~5 个季度：不足以同比时 C 会诚实标注
    unavailable；年度表约 4 个财年，满足 A（需 ≥3 年）。
    """
    try:
        import yfinance as yf

        from data.sources import _to_yahoo_symbol

        ticker = yf.Ticker(_to_yahoo_symbol(symbol))
        eps_q = _extract_eps(ticker.quarterly_income_stmt)
        eps_a = _extract_eps(ticker.income_stmt)
        if eps_q is None and eps_a is None:
            raise RuntimeError("利润表无可用 EPS 行")

        roe = None
        with contextlib.suppress(Exception):
            v = (ticker.info or {}).get("returnOnEquity")
            if v is not None:
                anchor = (eps_a if eps_a is not None else eps_q).index[-1]
                roe = pd.Series([float(v)], index=pd.DatetimeIndex([anchor]))
        return {
            "eps_quarterly": eps_q,
            "eps_annual": eps_a,
            "roe": roe,
            "source": "yfinance",
        }
    except Exception as exc:
        print(
            f"[warn] 获取 {symbol} 基本面失败（{type(exc).__name__}: {exc}），"
            "C/A 基本面检查将跳过；可用 --fundamentals-csv 手动提供。",
            file=sys.stderr,
        )
        return None


#: yfinance 利润表中的 EPS 行名候选（优先摊薄）
_YF_EPS_ROWS = ("Diluted EPS", "Basic EPS")


def _extract_eps(stmt: pd.DataFrame | None) -> pd.Series | None:
    """从 yfinance 利润表（行=科目，列=报告期）提取 EPS 序列（升序）。"""
    if stmt is None or len(stmt) == 0:
        return None
    for row in _YF_EPS_ROWS:
        if row in stmt.index:
            s = pd.to_numeric(stmt.loc[row], errors="coerce").dropna()
            if not len(s):
                continue
            s.index = pd.to_datetime(s.index)
            return s.astype(float).sort_index()
    return None


def _parse_abstract(df: pd.DataFrame) -> dict | None:
    """解析 akshare 财务摘要宽表：行=指标，列=报告期（如 20240930）。"""
    if df is None or len(df) == 0 or "指标" not in df.columns:
        return None
    period_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    if not period_cols:
        return None

    eps = _pick_row(df, period_cols, prefer=["基本每股收益"], contains="每股收益")
    if eps is None:
        return None
    roe = _pick_row(df, period_cols, prefer=["净资产收益率(ROE)", "摊薄净资产收益率"], contains="净资产收益率")
    if roe is not None:
        # 东财口径 ROE 为百分数（如 11.2 表示 11.2%），统一转小数
        if roe.abs().max() > 1.5:
            roe = roe / 100.0
    return {"eps": eps, "roe": roe, "source": "akshare"}


def _pick_row(
    df: pd.DataFrame, period_cols: list[str], prefer: list[str], contains: str
) -> pd.Series | None:
    """按指标名取一行并转为报告期 Series（优先精确名，再退化到包含匹配）。"""
    names = df["指标"].astype(str)
    row = None
    for p in prefer:
        hit = df[names == p]
        if len(hit):
            row = hit.iloc[0]
            break
    if row is None:
        hit = df[names.str.contains(contains, na=False)]
        if not len(hit):
            return None
        row = hit.iloc[0]
    values = pd.to_numeric(row[period_cols], errors="coerce")
    idx = pd.to_datetime(pd.Index(period_cols), format="%Y%m%d", errors="coerce")
    series = pd.Series(values.to_numpy(dtype=float), index=idx).dropna().sort_index()
    return series if len(series) else None


def load_fundamentals_csv(path: str) -> dict:
    """从 CSV 加载基本面（列：period_end,eps[,roe]）。

    Raises:
        RuntimeError: 缺少必需列或无有效数据行时。
    """
    df = pd.read_csv(path)
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "period_end" not in cols or "eps" not in cols:
        raise RuntimeError(
            f"基本面 CSV 需要 period_end,eps 两列（可选 roe），实际列：{list(df.columns)}"
        )
    idx = pd.to_datetime(df[cols["period_end"]], errors="coerce")
    eps = pd.Series(
        pd.to_numeric(df[cols["eps"]], errors="coerce").to_numpy(dtype=float), index=idx
    ).dropna().sort_index()
    if not len(eps):
        raise RuntimeError(f"基本面 CSV {path} 无有效 eps 数据行。")
    roe = None
    if "roe" in cols:
        roe = pd.Series(
            pd.to_numeric(df[cols["roe"]], errors="coerce").to_numpy(dtype=float), index=idx
        ).dropna().sort_index()
        if len(roe) and roe.abs().max() > 1.5:  # 百分数自动转小数
            roe = roe / 100.0
        roe = roe if len(roe) else None
    return {"eps": eps, "roe": roe, "source": "csv"}
