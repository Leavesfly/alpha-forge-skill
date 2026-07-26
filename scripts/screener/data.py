"""价值筛选数据获取：A 股批量快照（akshare）+ 逐只深度指标 + 港美股（yfinance）。

数据源策略：
- A 股 Phase 1：``ak.stock_zh_a_spot_em()`` 一次拉取全市场 PE/PB/市值/名称；
- A 股 Phase 2：``ak.stock_financial_analysis_indicator(symbol)`` 逐只取 ROE/负债率/增速；
- 美股 universe Phase 1：东财 push2 分页接口拉全市场 PE/市值快照（自实现
  限速+重试+部分成功可用，比 akshare 全量封装抗断连）；快照不可用时
  降级 Wikipedia S&P 500 成分股名单；
- 港美股逐只：``yf.Ticker(sym).info`` 取全部指标（无免费批量接口）。

所有接口异常返回 None（调用方跳过该标的，不中断整体扫描）。

本地优先：全市场快照（A 股/美股）、S&P 500 名单与逐只基本面指标均经
``data.cache.load_table / load_json_obj`` 落盘（TTL 内读本地 → 过期重拉 →
拉取失败回退陈旧缓存 → ``ALPHA_FORGE_OFFLINE=1`` 只读本地）；配合
run_sync.py 预热后，重复筛选不再反复碰免费源限频。
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
    """A 股全市场快照（本地优先）：代码/名称/PE/PB/总市值/股息率。

    TTL（默认 1 天）内直接读本地缓存；过期重拉，拉取失败回退陈旧缓存；
    离线模式只读本地。远端为 ``ak.stock_zh_a_spot_em()``（东财实时行情，
    ~5000 只，无需 API Key）。

    Returns:
        归一化 DataFrame（列：code, name, close, pe, pb, total_mv, div_yield）；
        远端与缓存都不可用时返回 None。
    """
    from data.cache import load_table

    return load_table(lambda: _fetch_astock_snapshot_remote(log), "astock_spot_em")


def _fetch_astock_snapshot_remote(log: Callable[..., None] | None = None) -> pd.DataFrame | None:
    """A 股全市场快照远端拉取（无缓存）。"""
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


#: 东财 push2 美股列表接口（m:105 纳斯达克 / m:106 纽交所 / m:107 美交所）
_US_SPOT_URL = "https://72.push2.eastmoney.com/api/qt/clist/get"

#: 东财字段→标准列：f12 代码 / f14 名称 / f2 最新价 / f9 市盈率 / f23 市净率 / f20 总市值
_US_SPOT_FIELDS = {"f12": "code", "f14": "name", "f2": "close",
                   "f9": "pe", "f23": "pb", "f20": "total_mv"}


def _us_ticker_to_symbol(ticker: str) -> str | None:
    """东财/维基美股代码 → 本项目符号（BRK_B / BRK.B → BRK-B.US）。"""
    t = str(ticker).strip().upper().replace("_", "-").replace(".", "-")
    if not t or not t.replace("-", "").isalnum():
        return None
    return f"{t}.US"


def fetch_us_snapshot(
    log: Callable[..., None] | None = None,
    page_size: int = 200,
    pause: float = 0.3,
) -> pd.DataFrame | None:
    """美股全市场快照（本地优先）：代码/名称/最新价/PE/PB/总市值（亿美元）。

    TTL（默认 1 天）内直接读本地缓存，避免每次全量分页拉取被限流；
    过期重拉，拉取失败回退陈旧缓存；离线模式只读本地。

    Returns:
        归一化 DataFrame（列：code, name, close, pe, pb, total_mv, div_yield；
        code 为本项目符号如 AAPL.US，total_mv 单位亿美元，div_yield 恒为
        None 由 Phase 2 补齐）；远端与缓存都不可用时返回 None。
    """
    from data.cache import load_table

    return load_table(
        lambda: _fetch_us_snapshot_remote(log, page_size, pause), "us_spot_em"
    )


def _fetch_us_snapshot_remote(
    log: Callable[..., None] | None = None,
    page_size: int = 200,
    pause: float = 0.3,
) -> pd.DataFrame | None:
    """美股全市场快照远端拉取（无缓存）。

    直接分页调用东财 push2 接口（~13000+ 只，无需 API Key）：逐页限速
    重试，连续失败即提前终止；已获得过半数据时接受部分结果（带告警），
    否则视为失败（调用方降级 S&P 500 名单）。
    """
    import time

    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    params = {
        "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f20",
        "fs": "m:105,m:106,m:107", "fields": ",".join(_US_SPOT_FIELDS),
        "pz": str(page_size),
    }

    records: list[dict] = []
    total = None
    page, consecutive_fail = 1, 0
    while True:
        got = None
        for attempt in range(3):  # 单页重试 3 次（退避 1s/2s）
            try:
                resp = session.get(
                    _US_SPOT_URL, params={**params, "pn": str(page)}, timeout=15
                )
                data = resp.json().get("data") or {}
                got = data.get("diff") or []
                if total is None:
                    total = int(data.get("total") or 0)
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if got is None:
            consecutive_fail += 1
            if consecutive_fail >= 3:
                break  # 连续 3 页失败：网络不可用，提前终止
            page += 1
            continue
        consecutive_fail = 0
        records.extend(got)
        if not got or (total and len(records) >= total):
            break
        page += 1
        time.sleep(pause)

    if not records or not total:
        if log:
            log("[warn] 东财美股快照拉取失败（接口不可用或返回空）")
        return None
    if len(records) < total * 0.5:
        if log:
            log(f"[warn] 东财美股快照仅获得 {len(records)}/{total} 只（不足半数），视为不可用")
        return None
    if log and len(records) < total:
        log(f"[warn] 东财美股快照部分成功：{len(records)}/{total} 只，继续使用已获数据")

    df = pd.DataFrame(records).rename(columns=_US_SPOT_FIELDS)
    for col in ("close", "pe", "pb", "total_mv"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = None
    # 总市值：东财口径为美元，转亿美元（与 A 股快照同构，但币种不同）
    df["total_mv"] = df["total_mv"] / 1e8
    df["code"] = df["code"].map(_us_ticker_to_symbol)
    df["div_yield"] = None  # 列表接口无股息率，Phase 2 yfinance 补齐
    df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"]).reset_index(drop=True)
    return df[["code", "name", "close", "pe", "pb", "total_mv", "div_yield"]]


def fetch_sp500_symbols(log: Callable[..., None] | None = None) -> list[str] | None:
    """S&P 500 成分股名单（本地优先）：美股快照不可用时的降级 universe。

    成分股变动低频，TTL 默认 7 天（``ALPHA_FORGE_CACHE_TTL`` 显式设置时
    以环境变量为准）；拉取失败回退陈旧缓存；离线模式只读本地。

    Returns:
        本项目符号列表（如 ["AAPL.US", "BRK-B.US", ...]）；远端与缓存都
        不可用时返回 None。
    """
    import os

    from data.cache import default_config, load_json_obj

    config = default_config("1d")
    if not os.environ.get("ALPHA_FORGE_CACHE_TTL"):
        config.ttl_seconds = 7 * 24 * 3600  # 成分股变动低频，周级新鲜度足够

    def _remote() -> dict | None:
        symbols = _fetch_sp500_symbols_remote(log)
        return {"symbols": symbols} if symbols else None

    payload = load_json_obj(_remote, "sp500_symbols", config)
    return payload.get("symbols") if payload else None


def _fetch_sp500_symbols_remote(log: Callable[..., None] | None = None) -> list[str] | None:
    """S&P 500 成分股名单远端拉取（Wikipedia，无缓存）。"""
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            storage_options={"User-Agent": "Mozilla/5.0"},
        )
        col = _find_col(tables[0], ["Symbol"])
        if col is None:
            raise ValueError("成分表无 Symbol 列")
        symbols = [
            s for s in (_us_ticker_to_symbol(t) for t in tables[0][col]) if s
        ]
    except Exception as exc:
        if log:
            log(f"[warn] S&P 500 成分股名单拉取失败（{type(exc).__name__}: {exc}）")
        return None
    return symbols or None


def fetch_astock_detail(code: str) -> dict | None:
    """A 股单标的深度财务指标（本地优先）：ROE/负债率/增速/现金流等。

    财报数据按报告期更新，TTL（默认 1 天）内读本地缓存——全市场 Phase 2
    逐只拉取是漏斗最慢环节，重复筛选（换预设/调阈值）命中缓存后秒级完成。

    Returns:
        ``{"roe", "debt_ratio", "profit_growth", "revenue_growth", "asset_growth",
        "ocf_per_share", "gross_margin"}``（值均为 float|None）；远端与缓存
        都不可用时返回 None。
    """
    from data.cache import load_json_obj

    return load_json_obj(
        lambda: _fetch_astock_detail_remote(code), f"astock_detail_{code}"
    )


def _fetch_astock_detail_remote(code: str) -> dict | None:
    """A 股单标的深度财务指标远端拉取（无缓存）。

    调用 ``ak.stock_financial_analysis_indicator(symbol, start_year)``（新浪财务分析指标）。
    注意：start_year 缺省值（1900）会返回空表，必须传近年份；取近两年保证至少有一个年报期。
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
        "gross_margin": None,
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

    # 销售毛利率（DHQ 维度：高毛利=定价权与规模效应的财务信号）。
    # 注意：新浪该字段近年报告期普遍为 NaN，缺失时由调用方用
    # fetch_astock_gross_margin（同花顺口径）兜底。
    gross_col = _find_col(df, ["销售毛利率(%)", "销售毛利率", "毛利率", "gross_margin"])
    if gross_col:
        val = pd.to_numeric(latest.get(gross_col), errors="coerce")
        if pd.notna(val):
            result["gross_margin"] = float(val)

    return result


def fetch_astock_gross_margin(code: str) -> float | None:
    """A 股单标的毛利率兜底：``ak.stock_financial_abstract``（同花顺财务摘要）。

    新浪财务分析指标的「销售毛利率」近年报告期普遍为 NaN，此接口作为
    DHQ 毛利率维度的兜底数据源（仅启用该维度且主接口缺失时调用，避免
    全市场扫描额外打接口）。

    Returns:
        最新报告期毛利率(%)；接口异常或无数据返回 None。
    """
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            df = ak.stock_financial_abstract(symbol=code)
    except Exception:
        return None

    if df is None or len(df) == 0 or "指标" not in df.columns:
        return None

    rows = df[df["指标"].astype(str) == "毛利率"]
    if len(rows) == 0:
        return None

    # 报告期列为 YYYYMMDD，取最新一期非空值
    date_cols = sorted((c for c in df.columns if str(c).isdigit()), reverse=True)
    for col in date_cols:
        val = pd.to_numeric(rows.iloc[0].get(col), errors="coerce")
        if pd.notna(val):
            return float(val)
    return None


#: 新浪财报接口的市场前缀（研发强度取数用；北交所新浪无覆盖，返回 None）
_SINA_PREFIX = {"6": "sh", "0": "sz", "3": "sz"}


def fetch_astock_rd_ratio(code: str) -> float | None:
    """A 股单标的研发强度(%)：研发费用 / 营业总收入 × 100（本地优先）。

    费雪成长股维度：研发投入是成长引擎，研发转化能力决定长期增长质量。
    数据源为新浪利润表（``ak.stock_financial_report_sina``，无需 API Key），
    取最新报告期；仅启用该维度时逐只调用，避免全市场扫描额外打接口。

    Returns:
        研发费用/营收占比(%)；接口异常、北交所或未披露研发费用（如
        金融/地产）返回 None（调用方按数据缺失剔除）。
    """
    from data.cache import load_json_obj

    payload = load_json_obj(
        lambda: _fetch_astock_rd_ratio_remote(code), f"astock_rd_{code}"
    )
    return payload.get("rd_ratio") if payload else None


def _fetch_astock_rd_ratio_remote(code: str) -> dict | None:
    """A 股研发强度远端拉取（无缓存）：新浪利润表最新报告期。

    报表无研发科目时返回 ``{"rd_ratio": None}``（可缓存，避免重拉）；
    接口异常返回 None（不缓存，下次重试）。
    """
    prefix = _SINA_PREFIX.get(str(code)[:1])
    if prefix is None:
        return {"rd_ratio": None}  # 北交所等新浪无覆盖
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            df = ak.stock_financial_report_sina(stock=f"{prefix}{code}", symbol="利润表")
    except Exception:
        return None

    if df is None or len(df) == 0:
        return None

    date_col = _find_col(df, ["报告日", "报告期", "date"])
    if date_col:
        df = df.sort_values(date_col, ascending=False)
    rd_col = _find_col(df, ["研发费用", "rd_expense"])
    rev_col = _find_col(df, ["营业总收入", "营业收入", "revenue"])
    if rd_col is None or rev_col is None:
        return {"rd_ratio": None}  # 无研发科目：视为未披露研发投入

    # 取最新一期两列同时非空的记录（新浪早年报告期无研发费用列值）
    for _, row in df.iterrows():
        rd = pd.to_numeric(row.get(rd_col), errors="coerce")
        rev = pd.to_numeric(row.get(rev_col), errors="coerce")
        if pd.notna(rd) and pd.notna(rev) and rev > 0:
            return {"rd_ratio": float(rd / rev * 100.0)}
    return {"rd_ratio": None}


def fetch_yfinance_rd_ratio(symbol: str) -> float | None:
    """港美股单标的研发强度(%)：利润表 R&D / Total Revenue × 100。

    ``.info`` 无研发字段，需额外拉取年度利润表（仅启用费雪研发维度时
    调用，避免逐只多打一次接口）。

    Returns:
        最新财年研发费用/营收占比(%)；无研发科目或接口异常返回 None。
    """
    try:
        import yfinance as yf

        from data.sources import _to_yahoo_symbol

        stmt = yf.Ticker(_to_yahoo_symbol(symbol)).income_stmt
    except Exception:
        return None

    if stmt is None or len(stmt) == 0:
        return None
    if "Research And Development" not in stmt.index or "Total Revenue" not in stmt.index:
        return None
    rd = pd.to_numeric(stmt.loc["Research And Development"], errors="coerce").dropna()
    rev = pd.to_numeric(stmt.loc["Total Revenue"], errors="coerce").dropna()
    common = rd.index.intersection(rev.index)
    if not len(common):
        return None
    latest = max(common)
    if rev[latest] <= 0:
        return None
    return float(rd[latest] / rev[latest] * 100.0)


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


def fetch_drawdown_52w(symbol: str, lookback: int = 250) -> float | None:
    """自 52 周高点的回撤幅度(%)：(区间最高 - 当前价) / 区间最高 × 100。

    马哈尼《高增长科技股投资法》DHQ 策略的折扣触发条件：高质量公司
    自高点回撤 20%~30% 时进入加仓区。走 datafeed 免费日 K。

    Returns:
        回撤百分比（≥0）；数据不足或拉取失败返回 None。
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
    if high <= 0:
        return None
    return max(0.0, (high - close) / high * 100.0)


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

    # yfinance grossMargins 为小数（如 0.62 = 62%），转百分数
    gross_raw = info.get("grossMargins")
    gross_margin = gross_raw * 100.0 if gross_raw is not None else None

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

    # 自 52 周高点回撤(%)（DHQ 折扣触发条件）
    drawdown = None
    if close is not None and hi is not None and hi > 0:
        drawdown = max(0.0, (hi - close) / hi * 100.0)

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
        "gross_margin": gross_margin,
        "drawdown": drawdown,
        "asset_growth": None,  # yfinance .info 无资产增速，聪明增长维度仅 A 股支持
    }
