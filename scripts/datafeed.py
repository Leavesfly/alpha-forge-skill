"""数据获取辅助：多源 K 线拉取（TickFlow 主源 + baostock / akshare / yfinance 兜底）。

统一拉取 K 线为回测所需的 OHLCV DataFrame。默认优先 TickFlow
（免费服务历史日 K 足以回测，配置 TICKFLOW_API_KEY 后支持分钟级）；
单源失败先重试（默认 2 次，退避 1s/2s，``ALPHA_FORGE_RETRIES`` 可调，0 关闭），
重试仍失败时自动降级：A 股日/周/月 K 走 baostock → akshare，
港股/美股日/周/月 K 走 yfinance。
环境变量 ``ALPHA_FORGE_DATA_SOURCE=tickflow|baostock|akshare|yfinance`` 可强制指定单源。
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import time

import pandas as pd
from tickflow import TickFlow

from data import load_klines, normalize_adjust
from data.sources import (
    API_KEY_HELP,
    get_sources,
    get_tickflow_client,
    source_label,
)

# 向后兼容：旧代码从 datafeed 导入 get_client
get_client = get_tickflow_client

# 标的代码统一格式：代码.市场后缀（与 cli_common 保持一致）
_SYMBOL_RE = re.compile(r"^[0-9A-Za-z]+\.[A-Za-z]{2,4}$")


def _check_symbol(symbol: str) -> None:
    """校验标的代码格式，提前拦截低级错误而非等到网络请求失败。"""
    if not _SYMBOL_RE.match((symbol or "").strip()):
        raise RuntimeError(
            f"标的代码不合法：'{symbol}'。格式应为「代码.市场后缀」，如 600000.SH（A股）/ "
            "AAPL.US（美股）/ 00700.HK（港股）/ cu2501.SHF（期货）；"
            "完整后缀见 references/data-fetching.md。"
        )


def _retry_config() -> tuple[int, float]:
    """重试配置：(重试次数, 首次退避秒数)；``ALPHA_FORGE_RETRIES=0`` 关闭重试。"""
    try:
        retries = int(os.environ.get("ALPHA_FORGE_RETRIES", "2"))
    except ValueError:
        retries = 2
    return max(0, retries), 1.0


def _fetch_with_retry(source, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
    """单数据源拉取，失败时指数退避重试（网络抖动兜底）。

    重试仍失败时抛出最后一次异常，由调用方决定是否降级下一源。
    """
    retries, backoff = _retry_config()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            # SDK 可能向 stdout 打印服务横幅，重定向到 stderr 保证 --json 的 stdout 纯净
            with contextlib.redirect_stdout(sys.stderr):
                return source.fetch(symbol, period, count, adjust)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = backoff * (2 ** attempt)
                print(
                    f"[warn] {source.name} 拉取 {symbol} 失败（第 {attempt + 1} 次："
                    f"{type(exc).__name__}），{wait:.0f}s 后重试...",
                    file=sys.stderr,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _fetch_ohlcv_raw(
    symbol: str,
    period: str,
    count: int,
    adjust: str,
) -> pd.DataFrame:
    """按数据源优先级拉取单标的 K 线（不经缓存）。

    单源失败先重试（退避），仍失败且存在可用兜底源时自动降级（stderr 告警）；
    全部失败时抛出汇总错误。
    """
    sources = [s for s in get_sources() if s.supports(symbol, period)]
    if not sources:
        raise RuntimeError(
            f"没有数据源支持 {symbol} {period}（兜底源仅限 A 股/港股/美股的日/周/月 K）。"
        )
    errors: list[str] = []
    for i, source in enumerate(sources):
        try:
            df = _fetch_with_retry(source, symbol, period, count, adjust)
            if i > 0:
                print(
                    f"[warn] 主源失败，已降级使用 {source.name} 数据源："
                    f"{'; '.join(errors)}",
                    file=sys.stderr,
                )
            return df
        except Exception as exc:
            errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
    raise RuntimeError(
        f"拉取 {symbol} {period} K 线失败（已尝试 {len(sources)} 个数据源）：\n  "
        + "\n  ".join(errors)
    )


def fetch_ohlcv(
    symbol: str,
    period: str = "1d",
    count: int = 500,
    adjust: str = "forward",
    use_cache: bool = True,
) -> pd.DataFrame:
    """拉取单标的 K 线并返回按时间升序的 OHLCV DataFrame（带本地缓存）。

    Args:
        symbol: 标的代码。
        period: K 线周期。
        count: 拉取的 K 线数量。
        adjust: 复权口径，``forward``/``qfq``（前复权，默认，回测推荐）、
            ``backward``/``hfq``（后复权）、``none``（不复权）。
        use_cache: 是否使用本地缓存（命中且新鲜则不走网络）。

    Returns:
        至少包含 ``close`` 列的 DataFrame。

    Raises:
        RuntimeError: 标的代码格式非法、所有数据源均失败或返回空数据时，
            错误信息包含可操作的排查建议。
    """
    _check_symbol(symbol)
    adj = normalize_adjust(adjust)
    if not use_cache:
        df = _fetch_ohlcv_raw(symbol, period, count, adj)
    else:
        df = load_klines(
            lambda: _fetch_ohlcv_raw(symbol, period, count, adj),
            symbol=symbol,
            period=period,
            count=count,
            adjust=adj,
            source=source_label(),
            # 陈旧缓存增量更新：只拉尾部小段合并（每日扫描不再全量重拉）
            fetch_tail_fn=lambda n: _fetch_ohlcv_raw(symbol, period, n, adj),
        )
    if df is None or len(df) == 0:
        raise RuntimeError(
            f"{symbol} {period} 返回 0 根 K 线。请检查：① 代码与市场后缀是否匹配；"
            "② 该标的是否已退市/停牌；③ 周期是否需要 API Key（分钟线）。"
        )
    if "close" not in df.columns:
        raise RuntimeError(
            f"{symbol} 返回数据缺少 close 列（实际列：{list(df.columns)}），"
            "无法用于回测；可能是数据源异常，可加 --no-cache 重试。"
        )
    return df


def _date_column(df: pd.DataFrame) -> str | None:
    """返回 DataFrame 中的时间列名。"""
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            return col
    return None


def fetch_prices(
    symbols: list[str],
    period: str = "1d",
    count: int = 500,
    adjust: str = "forward",
) -> pd.DataFrame:
    """拉取多标的收盘价并按共同交易日对齐。

    Args:
        symbols: 标的代码列表。
        period: K 线周期。
        count: 每个标的拉取的 K 线数量。
        adjust: 复权口径（默认前复权）。

    Returns:
        收盘价 DataFrame（索引为日期，列为标的代码），已按共同日期内连接对齐。
    """
    series: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym, period=period, count=count, adjust=adjust)
        except Exception as exc:
            # 多标的场景下明确指出是哪个标的失败，便于定位
            raise RuntimeError(f"拉取 {sym} 失败：{exc}") from exc
        date_col = _date_column(df)
        idx = pd.to_datetime(df[date_col]) if date_col else pd.RangeIndex(len(df))
        series[sym] = pd.Series(df["close"].astype(float).values, index=idx)

    prices = pd.DataFrame(series).dropna(how="any").sort_index()
    if prices.empty or prices.shape[1] < 2:
        raise RuntimeError(
            "多标的价格对齐后为空或不足 2 个标的。常见原因：① 跨市场标的交易日历重叠过少；"
            "② 某标的上市时间过短；可减小 --count 或更换标的组合。"
        )
    return prices


def fetch_universe(name: str = "CN_Equity_A", limit: int | None = None) -> list[str]:
    """获取股票池成分代码（需 TICKFLOW_API_KEY）。

    Args:
        name: 股票池名称，如 CN_Equity_A / US_Equity / HK_Equity。
        limit: 返回成分数量上限（None 表示全部）。

    Returns:
        标的代码列表。
    """
    if not os.environ.get("TICKFLOW_API_KEY"):
        raise RuntimeError(
            "获取股票池需要配置环境变量 TICKFLOW_API_KEY。\n" + API_KEY_HELP
        )
    tf = TickFlow()
    data = tf.universes.get(name)
    symbols = data["symbols"] if isinstance(data, dict) else list(data)
    if limit is not None and limit > 0:
        symbols = symbols[:limit]
    if not symbols:
        raise RuntimeError(f"股票池 {name} 未返回任何成分，请检查名称。")
    return symbols


def fetch_dividends(symbol: str) -> pd.Series:
    """拉取 A 股现金分红历史（每股派现，索引为除权除息日）。

    数据源 akshare 分红送配详情（东财），无需 API Key；仅支持 A 股。
    供 run_dca.py 显式分红建模（--dividends auto）使用，应搭配不复权价格。

    Returns:
        每股现金分红 Series（float，DatetimeIndex 为除权除息日，升序）。

    Raises:
        RuntimeError: 非 A 股、接口异常或无分红记录时。
    """
    _check_symbol(symbol)
    if not symbol.upper().endswith((".SH", ".SZ", ".BJ")):
        raise RuntimeError(
            f"分红数据目前仅支持 A 股（收到 {symbol}）；"
            "其他市场可用 --dividends <CSV> 提供（列：date,dps）。"
        )
    import akshare as ak

    code = symbol.split(".")[0]
    with contextlib.redirect_stdout(sys.stderr):
        df = ak.stock_fhps_detail_em(symbol=code)
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


def fetch_fundamentals(symbols: list[str]) -> pd.DataFrame | None:
    """获取多标的财务指标（用于价值/质量/规模因子）。

    需要 TICKFLOW_API_KEY 且账号具备财务数据权限。无权限、未配置或
    接口异常时返回 None（调用方据此跳过基本面因子）。

    Returns:
        含 symbol、period_end 及各财务指标列的 DataFrame；不可用时返回 None。
    """
    if not os.environ.get("TICKFLOW_API_KEY"):
        print(
            "[warn] 未配置 TICKFLOW_API_KEY，价值/质量/规模等基本面因子将被跳过。\n"
            + API_KEY_HELP
        )
        return None
    tf = TickFlow()
    try:
        df = tf.financials.metrics(symbols, as_dataframe=True)
    except Exception as exc:  # 权限不足/接口异常均降级处理
        print(
            f"[warn] 获取财务数据失败（{type(exc).__name__}: {exc}），基本面因子将被跳过。"
        )
        return None
    if df is None or len(df) == 0:
        print("[warn] 财务数据为空，基本面因子将被跳过。")
        return None
    return df
