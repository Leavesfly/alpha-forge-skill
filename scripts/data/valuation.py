"""估值历史分位（PE/PB Band）：计算当前估值在自身历史中的百分位。

定位：screener 的绝对阈值（PE<20）无法区分行业差异——银行 PE 5 和成长股 PE 30
不可比。历史分位回答的是「相对于这只股票自身，当前估值贵不贵」：

- PE 分位 20% → 当前 PE 处于近 N 年最低 20% 区间，相对低估；
- PB 分位 80% → 当前 PB 处于近 N 年最高 20% 区间，相对高估。

数据源：
- A 股：``ak.stock_a_lg_indicator(symbol)``（乐咕乐股，免费，日频 PE/PB/PS）；
- 港美股：yfinance 历史价格 + 当前 TTM EPS / BVPS 近似推算（精度有限，标注近似）。

分位计算采用中位秩（并列值各计一半），避免估值恒定时分位恒为 0 或 1。
"""

from __future__ import annotations

import contextlib
import math
import sys
from dataclasses import dataclass

import pandas as pd


@dataclass
class ValuationPercentile:
    """单标的估值历史分位结果。"""

    symbol: str
    pe_current: float | None
    pb_current: float | None
    pe_percentile: float | None   # 0~1，越低越便宜
    pb_percentile: float | None   # 0~1，越低越便宜
    n_samples: int                # 有效样本数
    lookback_years: float         # 实际回看年数
    source: str                   # akshare / yfinance_approx
    note: str = ""                # 补充说明（如数据不足、近似口径）

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "pe_current": _safe_round(self.pe_current),
            "pb_current": _safe_round(self.pb_current),
            "pe_percentile": _safe_round(self.pe_percentile, 4),
            "pb_percentile": _safe_round(self.pb_percentile, 4),
            "n_samples": self.n_samples,
            "lookback_years": round(self.lookback_years, 1),
            "source": self.source,
            "note": self.note or None,
        }

    @property
    def valuation_label(self) -> str:
        """综合估值标签：低估 / 合理 / 偏高 / 高估 / 数据不足。"""
        pcts = [p for p in (self.pe_percentile, self.pb_percentile) if p is not None]
        if not pcts:
            return "数据不足"
        avg = sum(pcts) / len(pcts)
        if avg < 0.2:
            return "低估"
        if avg < 0.4:
            return "偏低"
        if avg < 0.6:
            return "合理"
        if avg < 0.8:
            return "偏高"
        return "高估"


def _safe_round(v, ndigits: int = 2):
    if v is None:
        return None
    try:
        if math.isnan(float(v)):
            return None
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _percentile_rank(series: pd.Series, value: float) -> float | None:
    """中位秩百分位：value 在 series 中的分位（0~1）。

    并列值各计一半，避免恒等序列分位为 0 或 1。
    """
    s = series.dropna()
    if len(s) < 30:
        return None
    below = (s < value).sum()
    equal = (s == value).sum()
    return float((below + 0.5 * equal) / len(s))


# ---------------------------------------------------------------------------
# A 股：akshare 乐咕乐股历史 PE/PB
# ---------------------------------------------------------------------------


def fetch_valuation_astock(
    symbol: str,
    lookback_years: int = 5,
) -> ValuationPercentile | None:
    """A 股估值历史分位（akshare stock_a_lg_indicator，免费日频）。

    Args:
        symbol: 带市场后缀的标的代码（如 600000.SH）。
        lookback_years: 回看年数，默认 5。

    Returns:
        ValuationPercentile；接口异常或数据不足时返回 None。
    """
    code = symbol.split(".")[0]
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            df = ak.stock_a_lg_indicator(symbol=code)
    except Exception:
        return None

    if df is None or len(df) == 0:
        return None

    # 列名归一化（akshare 版本间可能微调）
    col_map = {
        "date": ["trade_date", "日期"],
        "pe": ["pe", "pe_ttm", "市盈率"],
        "pb": ["pb", "市净率"],
    }
    normalized: dict[str, str | None] = {}
    for std, candidates in col_map.items():
        for cand in candidates:
            if cand in df.columns:
                normalized[std] = cand
                break
        else:
            normalized[std] = None

    if normalized["date"] is None:
        return None

    df = df.copy()
    df["_date"] = pd.to_datetime(df[normalized["date"]], errors="coerce")
    df = df.dropna(subset=["_date"]).sort_values("_date")

    # 截取回看窗口
    cutoff = df["_date"].max() - pd.DateOffset(years=lookback_years)
    df = df[df["_date"] >= cutoff]

    if len(df) < 60:
        return None

    # PE 分位
    pe_col = normalized.get("pe")
    pe_series = None
    pe_current = None
    pe_pct = None
    if pe_col and pe_col in df.columns:
        pe_series = pd.to_numeric(df[pe_col], errors="coerce").dropna()
        # 排除负 PE（亏损）
        pe_series = pe_series[pe_series > 0]
        if len(pe_series) >= 30:
            pe_current = float(pe_series.iloc[-1])
            pe_pct = _percentile_rank(pe_series, pe_current)

    # PB 分位
    pb_col = normalized.get("pb")
    pb_series = None
    pb_current = None
    pb_pct = None
    if pb_col and pb_col in df.columns:
        pb_series = pd.to_numeric(df[pb_col], errors="coerce").dropna()
        pb_series = pb_series[pb_series > 0]
        if len(pb_series) >= 30:
            pb_current = float(pb_series.iloc[-1])
            pb_pct = _percentile_rank(pb_series, pb_current)

    n_samples = max(
        len(pe_series) if pe_series is not None else 0,
        len(pb_series) if pb_series is not None else 0,
    )
    span_days = (df["_date"].max() - df["_date"].min()).days
    lookback_actual = span_days / 365.25

    note = ""
    if pe_pct is None and pb_pct is None:
        return None
    if pe_pct is None:
        note = "PE 数据不足（可能长期亏损），仅 PB 分位可用"
    elif pb_pct is None:
        note = "PB 数据不足，仅 PE 分位可用"

    return ValuationPercentile(
        symbol=symbol,
        pe_current=pe_current,
        pb_current=pb_current,
        pe_percentile=pe_pct,
        pb_percentile=pb_pct,
        n_samples=n_samples,
        lookback_years=lookback_actual,
        source="akshare",
        note=note,
    )


# ---------------------------------------------------------------------------
# 港美股：yfinance 近似（历史价格 / 当前 EPS/BVPS）
# ---------------------------------------------------------------------------


def fetch_valuation_yfinance(
    symbol: str,
    lookback_years: int = 5,
) -> ValuationPercentile | None:
    """港美股估值历史分位（yfinance 近似：历史价格 / 当前 TTM EPS/BVPS）。

    精度说明：EPS/BVPS 用当前值近似历史，忽略盈利增长对 PE 的影响，
    因此分位仅反映「价格相对自身历史的位置」，非严格 PE 分位。

    Args:
        symbol: 带市场后缀的标的代码（如 AAPL.US / 00700.HK）。
        lookback_years: 回看年数，默认 5。

    Returns:
        ValuationPercentile；接口异常或数据不足时返回 None。
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

    # 当前 TTM EPS 与 BVPS
    eps = info.get("trailingEps")
    bvps = info.get("bookValue")  # 每股净资产
    current_pe = info.get("trailingPE")
    current_pb = info.get("priceToBook")

    if current_pe is None and current_pb is None:
        return None

    # 拉取历史价格
    try:
        hist = ticker.history(period=f"{lookback_years}y", interval="1wk")
    except Exception:
        return None

    if hist is None or len(hist) < 30:
        return None

    prices = hist["Close"].dropna()
    if len(prices) < 30:
        return None

    pe_current = None
    pe_pct = None
    pb_current = None
    pb_pct = None

    # PE 近似分位：历史价格 / 当前 EPS
    if eps and eps > 0:
        pe_hist = prices / eps
        pe_hist = pe_hist[pe_hist > 0]
        if len(pe_hist) >= 30:
            pe_current = current_pe or float(pe_hist.iloc[-1])
            pe_pct = _percentile_rank(pe_hist, pe_current)

    # PB 近似分位：历史价格 / 当前 BVPS
    if bvps and bvps > 0:
        pb_hist = prices / bvps
        pb_hist = pb_hist[pb_hist > 0]
        if len(pb_hist) >= 30:
            pb_current = current_pb or float(pb_hist.iloc[-1])
            pb_pct = _percentile_rank(pb_hist, pb_current)

    if pe_pct is None and pb_pct is None:
        return None

    span_days = (prices.index.max() - prices.index.min()).days
    lookback_actual = span_days / 365.25

    note = "近似口径：历史价格/当前EPS(BVPS)，未考虑盈利增长，仅反映价格相对位置"

    return ValuationPercentile(
        symbol=symbol,
        pe_current=pe_current,
        pb_current=pb_current,
        pe_percentile=pe_pct,
        pb_percentile=pb_pct,
        n_samples=len(prices),
        lookback_years=lookback_actual,
        source="yfinance_approx",
        note=note,
    )


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

_A_SUFFIXES = (".SH", ".SZ", ".BJ")


def fetch_valuation_percentile(
    symbol: str,
    lookback_years: int = 5,
) -> ValuationPercentile | None:
    """统一入口：自动分流 A 股（akshare 精确）/ 港美股（yfinance 近似）。

    Args:
        symbol: 带市场后缀的标的代码。
        lookback_years: 回看年数，默认 5。

    Returns:
        ValuationPercentile；数据不可用时返回 None。
    """
    if symbol.upper().endswith(_A_SUFFIXES):
        return fetch_valuation_astock(symbol, lookback_years)
    return fetch_valuation_yfinance(symbol, lookback_years)


def format_valuation(vp: ValuationPercentile | None) -> str:
    """单行文字描述，供 CLI 输出。"""
    if vp is None:
        return "估值分位：数据不可用"
    parts = []
    if vp.pe_percentile is not None:
        parts.append(f"PE {vp.pe_current:.1f}（{vp.pe_percentile:.0%} 分位）")
    if vp.pb_percentile is not None:
        parts.append(f"PB {vp.pb_current:.2f}（{vp.pb_percentile:.0%} 分位）")
    label = vp.valuation_label
    src = "精确" if vp.source == "akshare" else "近似"
    return (
        f"估值分位（近 {vp.lookback_years:.0f} 年，{src}）：{'，'.join(parts)}"
        f" → {label}"
    )
