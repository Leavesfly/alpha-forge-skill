"""交易日历：为缓存新鲜度判定提供「最近一个已收盘交易日」基准。

动机：原缓存新鲜度用墙钟 TTL（``fetched_at + 24h``）。周一 15:30 跑信号时，
周日 16:00 抓的缓存只有 23.5 小时，被判「新鲜」——于是拿不到周一收盘 K 线，
实盘信号静默用了上周五的数据。这是纯墙钟 TTL 无法修复的结构性缺陷。

设计取舍（重要）：日历判错的两个方向后果**不对称**——
- 误把交易日当假日 → 基准日偏早 → 缓存被判新鲜 → **漏掉最新 K 线（危险）**；
- 误把假日当交易日 → 基准日偏晚 → 缓存被判陈旧 → 多拉一次（仅浪费）。

因此本模块区分日历的权威性，由调用方按 ``authoritative`` 决定信任程度：
- ``authoritative=True``：来自 akshare 权威交易日列表，可单独作为新鲜度依据；
- ``authoritative=False``：内置静态表 + 周末规则启发式推导，调用方须与
  原 TTL 取「与」（两者都认为新鲜才算新鲜），保证判错时只多拉不漏拉。

不引入 ``exchange_calendars`` 等新依赖；akshare 已在项目依赖内，
其交易日列表经 ``load_json_obj`` 缓存（TTL 30 天），拉取失败降级静态表。
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

import pandas as pd

#: 各市场收盘时间与时区
_MARKET_CLOSE = {
    "CN": (dt.time(15, 0), "Asia/Shanghai"),
    "HK": (dt.time(16, 0), "Asia/Hong_Kong"),
    "US": (dt.time(16, 0), "America/New_York"),
}

#: 收盘后数据落库延迟缓冲：收盘即刻拉取往往还没有当日 K 线
_CLOSE_BUFFER = dt.timedelta(minutes=30)

#: akshare 交易日列表的缓存 TTL（秒）：日历一年只变一次，30 天足够
_CALENDAR_TTL = 30 * 86400

#: 向前回溯上限：连续假日不会超过此天数，防止日历数据异常时死循环
_MAX_LOOKBACK_DAYS = 30

#: 交易日列表进程内 memo 的“未初始化”哨兵（None 本身是合法结果，不能当哨兵）
_MEMO_UNSET: object = object()

_trading_days_memo: object = _MEMO_UNSET

_MEMO_LOCK = threading.Lock()

#: A 股静态节假日兜底表（仅工作日；周末由通用规则排除）。
#: 尽力而为的降级方案——akshare 不可用时才使用，且此时 authoritative=False。
_CN_HOLIDAYS = {
    # 2024
    "2024-01-01",
    "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
    "2024-04-04", "2024-04-05",
    "2024-05-01", "2024-05-02", "2024-05-03",
    "2024-06-10",
    "2024-09-16", "2024-09-17",
    "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
    # 2025
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04",
    "2025-04-04",
    "2025-05-01", "2025-05-02", "2025-05-05",
    "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
    # 2026
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
}

#: 港股静态节假日兜底表（仅工作日）。农历节日无法算法推导，只能列举。
_HK_HOLIDAYS = {
    # 2024
    "2024-01-01", "2024-02-12", "2024-02-13", "2024-03-29", "2024-04-01",
    "2024-04-04", "2024-05-01", "2024-05-15", "2024-06-10", "2024-07-01",
    "2024-09-18", "2024-10-01", "2024-10-11", "2024-12-25", "2024-12-26",
    # 2025
    "2025-01-01", "2025-01-29", "2025-01-30", "2025-01-31", "2025-04-04",
    "2025-04-18", "2025-04-21", "2025-05-01", "2025-05-05", "2025-07-01",
    "2025-10-01", "2025-10-07", "2025-10-29", "2025-12-25", "2025-12-26",
    # 2026
    "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-04-03",
    "2026-04-06", "2026-05-01", "2026-05-25", "2026-06-19", "2026-07-01",
    "2026-09-26", "2026-10-01", "2026-10-19", "2026-12-25",
}


@dataclass(frozen=True)
class SessionInfo:
    """最近一个已收盘交易日。

    Attributes:
        last_closed: 该交易日（tz-naive，已归一到零点）。
        authoritative: True 表示来自权威交易日列表，可单独作为新鲜度依据；
            False 表示启发式推导，调用方须与原 TTL 取「与」。
        source: 日历来源标识（akshare / builtin）。
    """

    last_closed: pd.Timestamp
    authoritative: bool
    source: str


def market_of(symbol: str) -> str | None:
    """从标的后缀推导市场标识（CN/HK/US）；无法判定返回 None。

    期货等其他市场返回 None，调用方据此回退原 TTL 逻辑。
    """
    sym = (symbol or "").upper()
    if sym.endswith((".SH", ".SZ", ".BJ")):
        return "CN"
    if sym.endswith(".HK"):
        return "HK"
    if sym.endswith(".US"):
        return "US"
    return None


def _easter(year: int) -> dt.date:
    """复活节日期（Anonymous Gregorian algorithm），用于推导 Good Friday。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * lam) // 433
    month = (h + lam - 7 * m + 90) // 25
    day = (h + lam - 7 * m + 33 * month + 19) % 32
    return dt.date(year, month, day)


def _observed(date: dt.date) -> dt.date:
    """美股固定日期节假日的顺延规则：周六提前到周五，周日推后到周一。"""
    if date.weekday() == 5:
        return date - dt.timedelta(days=1)
    if date.weekday() == 6:
        return date + dt.timedelta(days=1)
    return date


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """某年某月的第 n 个指定星期（weekday: 0=周一）。"""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """某年某月最后一个指定星期。"""
    next_month = dt.date(year + month // 12, month % 12 + 1, 1)
    last = next_month - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _us_holidays(year: int) -> set[str]:
    """NYSE 年度休市日（算法推导，全部为固定规则）。"""
    days = [
        _observed(dt.date(year, 1, 1)),                  # 元旦
        _nth_weekday(year, 1, 0, 3),                     # MLK Day
        _nth_weekday(year, 2, 0, 3),                     # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),            # Good Friday
        _last_weekday(year, 5, 0),                       # Memorial Day
        _observed(dt.date(year, 6, 19)),                 # Juneteenth（2022 起）
        _observed(dt.date(year, 7, 4)),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),                     # Labor Day
        _nth_weekday(year, 11, 3, 4),                    # Thanksgiving
        _observed(dt.date(year, 12, 25)),                # Christmas
    ]
    return {d.isoformat() for d in days}


def _static_holidays(market: str, year: int) -> set[str]:
    """内置静态节假日集合（按年过滤，减少无关比对）。"""
    if market == "US":
        return _us_holidays(year)
    table = _CN_HOLIDAYS if market == "CN" else _HK_HOLIDAYS
    prefix = f"{year}-"
    return {d for d in table if d.startswith(prefix)}


def _cn_trading_days() -> set[str] | None:
    """A 股权威交易日列表（akshare，经缓存）；不可用返回 None。

    进程内 memo：缓存新鲜度判定每只标的要调两次本函数，全市场 5000 只
    就是 1 万 次磁盘读 + JSON 解析；memo 后整个进程只读一次。
    不可用时也缓存 None（负缓存），避免每只标的都重试一次网络拉取。

    延迟导入 cache 以避免 ``cache → calendar → cache`` 循环依赖
    （与 sync.py / screener.data 的函数内导入约定一致）。
    """
    global _trading_days_memo
    with _MEMO_LOCK:
        if _trading_days_memo is not _MEMO_UNSET:
            return _trading_days_memo

    from .cache import CacheConfig, load_json_obj, resolve_cache_dir

    config = CacheConfig(cache_dir=resolve_cache_dir(), ttl_seconds=_CALENDAR_TTL)
    payload = load_json_obj(_fetch_cn_trading_days, "cn_trading_days", config)
    dates = payload.get("dates") if payload else None
    result = set(dates) if dates else None

    with _MEMO_LOCK:
        _trading_days_memo = result
    return result


def reset_calendar_cache() -> None:
    """清除交易日列表的进程内 memo（测试用）。"""
    global _trading_days_memo
    with _MEMO_LOCK:
        _trading_days_memo = _MEMO_UNSET


def _fetch_cn_trading_days() -> dict | None:
    """从 akshare 拉取沪深交易日列表（1990 至今，一次性全量）。"""
    import contextlib
    import sys

    import akshare as ak

    with contextlib.redirect_stdout(sys.stderr):
        df = ak.tool_trade_date_hist_sina()
    if df is None or len(df) == 0:
        return None
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    dates = pd.to_datetime(df[col], errors="coerce").dropna()
    if dates.empty:
        return None
    return {"dates": sorted({d.strftime("%Y-%m-%d") for d in dates})}


def last_closed_session(
    market: str,
    now: pd.Timestamp | None = None,
) -> SessionInfo | None:
    """返回该市场最近一个「已收盘」的交易日。

    Args:
        market: 市场标识（CN/HK/US），可用 :func:`market_of` 从代码推导。
        now: 当前时间（用于测试注入）；None 时取市场本地当前时间。
            带时区的入参会转换到市场时区，不带时区的视为市场本地时间。

    Returns:
        SessionInfo；市场不支持或日历不可用时返回 None（调用方回退 TTL）。
    """
    spec = _MARKET_CLOSE.get(str(market).upper())
    if spec is None:
        return None
    close_time, tz = spec

    if now is None:
        local = pd.Timestamp.now(tz=tz)
    else:
        ts = pd.Timestamp(now)
        local = ts.tz_convert(tz) if ts.tzinfo is not None else ts.tz_localize(tz)

    # 今日尚未收盘（含数据落库缓冲）时，基准日从昨天算起
    close_dt = local.normalize() + pd.Timedelta(
        hours=close_time.hour, minutes=close_time.minute
    ) + _CLOSE_BUFFER
    cursor = local.normalize()
    if local < close_dt:
        cursor -= pd.Timedelta(days=1)

    trading_days = _cn_trading_days() if market.upper() == "CN" else None
    authoritative = trading_days is not None
    source = "akshare" if authoritative else "builtin"

    holidays_cache: dict[int, set[str]] = {}
    for _ in range(_MAX_LOOKBACK_DAYS):
        key = cursor.strftime("%Y-%m-%d")
        if trading_days is not None:
            if key in trading_days:
                return SessionInfo(cursor.tz_localize(None), authoritative, source)
        else:
            if cursor.weekday() < 5:
                year = cursor.year
                if year not in holidays_cache:
                    holidays_cache[year] = _static_holidays(market.upper(), year)
                if key not in holidays_cache[year]:
                    return SessionInfo(cursor.tz_localize(None), False, source)
        cursor -= pd.Timedelta(days=1)
    return None
