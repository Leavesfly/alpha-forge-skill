"""数据获取辅助：多源 K 线拉取（TickFlow 主源 + baostock / akshare / yfinance 兖底）。

统一拉取 K 线为回测所需的 OHLCV DataFrame。默认优先 TickFlow
（免费服务历史日 K 足以回测，配置 TICKFLOW_API_KEY 后支持分钟级）；
单源失败先重试（默认 2 次，退避 1s/2s，``ALPHA_FORGE_RETRIES`` 可调，0 关闭），
重试仍失败时自动降级：A 股日/周/月 K 走 baostock → akshare，
港股/美股日/周/月 K 走 yfinance。
环境变量 ``ALPHA_FORGE_DATA_SOURCE=tickflow|baostock|akshare|yfinance`` 可强制指定单源。

弃用提示：``fetch_dividends`` / ``fetch_fundamentals`` / ``get_client`` 为向后兼容
重导出，建议改用 ``from data.dividends import fetch_dividends`` 等新路径。
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import warnings

import pandas as pd

from data import find_date_column, load_klines, normalize_adjust
from data.sources import (
    API_KEY_HELP,
    get_sources,
    source_label,
)
from market import validate_symbol

# ---------------------------------------------------------------------------
# 向后兼容重导出（弃用，建议改用 data/ 子包直接导入）
# ---------------------------------------------------------------------------
_DEPRECATED_REEXPORTS = {
    "fetch_dividends": ("data.dividends", "fetch_dividends"),
    "fetch_fundamentals": ("data.fundamentals", "fetch_fundamentals"),
    "get_client": ("data.sources", "get_tickflow_client"),
}


def __getattr__(name: str):
    """惰性重导出 + 弃用警告（Python 3.7+ 模块级 __getattr__）。"""
    if name in _DEPRECATED_REEXPORTS:
        module_path, attr_name = _DEPRECATED_REEXPORTS[name]
        warnings.warn(
            f"从 datafeed 导入 {name} 已弃用，请改用 from {module_path} import {attr_name}",
            DeprecationWarning,
            stacklevel=2,
        )
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _check_symbol(symbol: str) -> None:
    """校验标的代码格式，提前拦截低级错误而非等到网络请求失败。"""
    validate_symbol(symbol)


def _retry_config() -> tuple[int, float]:
    """重试配置：(重试次数, 首次退避秒数)；``ALPHA_FORGE_RETRIES=0`` 关闭重试。"""
    from envconfig import get_env_config

    return max(0, get_env_config().retries), 1.0


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
        date_col = find_date_column(df)
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
    from tickflow import TickFlow

    tf = TickFlow()
    data = tf.universes.get(name)
    symbols = data["symbols"] if isinstance(data, dict) else list(data)
    if limit is not None and limit > 0:
        symbols = symbols[:limit]
    if not symbols:
        raise RuntimeError(f"股票池 {name} 未返回任何成分，请检查名称。")
    return symbols


