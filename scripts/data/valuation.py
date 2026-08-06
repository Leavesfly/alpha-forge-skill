"""估值历史分位（PE/PB Band）：计算当前估值在自身历史中的百分位。

定位：screener 的绝对阈值（PE<20）无法区分行业差异——银行 PE 5 和成长股 PE 30
不可比。历史分位回答的是「相对于这只股票自身，当前估值贵不贵」：

- PE 分位 20% → 当前 PE 处于近 N 年最低 20% 区间，相对低估；
- PB 分位 80% → 当前 PB 处于近 N 年最高 20% 区间，相对高估。

数据源：
- A 股：``ak.stock_a_indicator_lg``（乐咕乐股，旧版名 ``stock_a_lg_indicator``），
  接口不存在时降级 ``ak.stock_value_em``（东财估值分析，日频 PE/PB）；
- 港美股：openbb 关键指标主力（失败降级 yfinance .info）+ 历史价格 /
  当前 TTM EPS / BVPS 近似推算（精度有限，标注近似）。

分位计算采用中位秩（并列值各计一半），避免估值恒定时分位恒为 0 或 1。

本地优先：结果经 ``cache.load_json_obj`` 按「标的 + 回看年数」落盘
（TTL 24 小时）。估值分位需拉数年日频 PE/PB 历史，是全市场筛选中最慢的
环节之一；且分位本身日频更新，没必要重复筛选时反复碰免费源限频。
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
    source: str                   # akshare / openbb_approx / yfinance_approx
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

    @classmethod
    def from_dict(cls, payload: dict) -> ValuationPercentile:
        """从 :meth:`to_dict` 的输出重建（缓存反序列化用）。

        分位保留 4 位小数（千分之一精度），对「低估/合理/高估」标签判定无影响。
        """
        return cls(
            symbol=payload.get("symbol", ""),
            pe_current=payload.get("pe_current"),
            pb_current=payload.get("pb_current"),
            pe_percentile=payload.get("pe_percentile"),
            pb_percentile=payload.get("pb_percentile"),
            n_samples=int(payload.get("n_samples") or 0),
            lookback_years=float(payload.get("lookback_years") or 0.0),
            source=payload.get("source", ""),
            note=payload.get("note") or "",
        )

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


def _fetch_astock_indicator(code: str) -> pd.DataFrame | None:
    """拉取 A 股日频估值指标，兼容 akshare 接口更名/移除。

    优先乐咕乐股（``stock_a_indicator_lg`` / 旧名 ``stock_a_lg_indicator``），
    接口不存在或失败时降级东财 ``stock_value_em``（列：数据日期/PE(TTM)/市净率）。
    """
    import akshare as ak

    for fn_name, kwargs in (
        ("stock_a_indicator_lg", {"symbol": code}),
        ("stock_a_lg_indicator", {"symbol": code}),
        ("stock_value_em", {"symbol": code}),
    ):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            with contextlib.redirect_stdout(sys.stderr):
                df = fn(**kwargs)
        except Exception as exc:
            print(
                f"[warn] 估值接口 {fn_name} 失败（{type(exc).__name__}），尝试下一接口。",
                file=sys.stderr,
            )
            continue
        if df is not None and len(df) > 0:
            return df
    return None


def fetch_valuation_astock(
    symbol: str,
    lookback_years: int = 5,
) -> ValuationPercentile | None:
    """A 股估值历史分位（乐咕乐股，接口不可用时降级东财估值分析）。

    Args:
        symbol: 带市场后缀的标的代码（如 600000.SH）。
        lookback_years: 回看年数，默认 5。

    Returns:
        ValuationPercentile；接口异常或数据不足时返回 None。
    """
    code = symbol.split(".")[0]
    try:
        df = _fetch_astock_indicator(code)
    except Exception:
        return None

    if df is None or len(df) == 0:
        return None

    # 列名归一化（乐咕乐股 pe_ttm/pb；东财 stock_value_em 为中文列）
    col_map = {
        "date": ["trade_date", "日期", "数据日期"],
        "pe": ["pe_ttm", "pe", "PE(TTM)", "市盈率"],
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
# 港美股：openbb 主力（关键指标 + 项目缓存周 K）
# ---------------------------------------------------------------------------


def fetch_valuation_openbb(
    symbol: str,
    lookback_years: int = 5,
) -> ValuationPercentile | None:
    """港美股估值历史分位（openbb 近似：历史价格 / 当前 TTM EPS/BVPS）。

    与 yfinance 路径同一近似口径；EPS 由现价/PE 反推，BVPS 直接取自
    openbb 关键指标。历史价格走 datafeed 周 K（命中本地缓存，不重复拉取）。

    Returns:
        ValuationPercentile；接口异常或数据不足时返回 None（调用方降级 yfinance）。
    """
    try:
        from data.openbb import fetch_obb_metrics, supports_hkus

        if not supports_hkus(symbol):
            return None
        with contextlib.redirect_stdout(sys.stderr):
            m = fetch_obb_metrics(symbol)
    except Exception:
        return None

    pe = m.get("pe")
    pb = m.get("pb")
    bvps = m.get("bvps")
    if pe is None and pb is None:
        return None

    # 历史价格：项目缓存周 K（OpenBB 主力链），与 yf 路径的 5y 周线同口径
    try:
        from datafeed import fetch_ohlcv

        df = fetch_ohlcv(symbol, period="1w", count=lookback_years * 52)
    except Exception:
        return None
    if df is None or len(df) < 30:
        return None
    prices = pd.Series(
        df["close"].astype(float).to_numpy(), index=pd.to_datetime(df["trade_date"])
    ).dropna()
    if len(prices) < 30:
        return None

    close = float(prices.iloc[-1])
    # TTM EPS 由现价/PE 反推（openbb 关键指标无直接 EPS 字段）
    eps = close / pe if pe and pe > 0 else None

    pe_current = None
    pe_pct = None
    pb_current = None
    pb_pct = None

    if eps and eps > 0:
        pe_hist = prices / eps
        pe_hist = pe_hist[pe_hist > 0]
        if len(pe_hist) >= 30:
            pe_current = float(pe)
            pe_pct = _percentile_rank(pe_hist, pe_current)

    if bvps and bvps > 0:
        pb_hist = prices / bvps
        pb_hist = pb_hist[pb_hist > 0]
        if len(pb_hist) >= 30:
            pb_current = float(pb) if pb else float(pb_hist.iloc[-1])
            pb_pct = _percentile_rank(pb_hist, pb_current)

    if pe_pct is None and pb_pct is None:
        return None

    span_days = (prices.index.max() - prices.index.min()).days
    return ValuationPercentile(
        symbol=symbol,
        pe_current=pe_current,
        pb_current=pb_current,
        pe_percentile=pe_pct,
        pb_percentile=pb_pct,
        n_samples=len(prices),
        lookback_years=span_days / 365.25,
        source="openbb_approx",
        note="近似口径：历史价格/当前EPS(BVPS)，未考虑盈利增长，仅反映价格相对位置",
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

#: 估值分位缓存 TTL：PE/PB 日频更新，24 小时
VALUATION_TTL = 24 * 3600


def fetch_valuation_percentile(
    symbol: str,
    lookback_years: int = 5,
) -> ValuationPercentile | None:
    """统一入口：自动分流 A 股（akshare 精确）/ 港美股（openbb 主力、yfinance 兜底，近似）。

    结果本地优先缓存 24 小时（按标的 + 回看年数分键）；远端返回 None 时不写缓存。

    Args:
        symbol: 带市场后缀的标的代码。
        lookback_years: 回看年数，默认 5。

    Returns:
        ValuationPercentile；数据不可用时返回 None。
    """
    from .cache import config_with_ttl, load_json_obj

    code = _sanitize_key(symbol)
    payload = load_json_obj(
        lambda: _valuation_payload(symbol, lookback_years),
        f"valuation_{code}_{lookback_years}y",
        config_with_ttl(VALUATION_TTL),
    )
    return ValuationPercentile.from_dict(payload) if payload else None


def _sanitize_key(symbol: str) -> str:
    """标的代码 -> 文件名安全的缓存键片段。"""
    return "".join(c if c.isalnum() else "_" for c in symbol)


def _valuation_payload(symbol: str, lookback_years: int) -> dict | None:
    """远端拉取并序列化；不可用返回 None（不缓存失败结果）。"""
    vp = _fetch_valuation_remote(symbol, lookback_years)
    return vp.to_dict() if vp is not None else None


def _fetch_valuation_remote(
    symbol: str,
    lookback_years: int,
) -> ValuationPercentile | None:
    """按市场分流直连拉取（不经缓存）。"""
    if symbol.upper().endswith(_A_SUFFIXES):
        return fetch_valuation_astock(symbol, lookback_years)
    return fetch_valuation_openbb(symbol, lookback_years) or fetch_valuation_yfinance(
        symbol, lookback_years
    )


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
