"""K 线本地缓存与复权口径管理。

动机：
- 原 datafeed 每次调用都直连网络，重复回测/寻优会反复拉取相同数据；
- 复权口径（前/后/不复权）未显式声明，回测可能因口径不一致而失真。

本模块提供：
- ``normalize_adjust``：把 qfq/hfq/none 等别名归一到 TickFlow 的 forward/backward/none；
- ``load_klines``：带缓存的 K 线读取——命中且新鲜则读本地，否则拉取并落盘；
  提供 ``fetch_tail_fn`` 时，日级及以上周期的陈旧缓存走**增量更新**：
  只拉少量尾部 K 线，经重叠区复权一致性校验后合并回写（适合每日全市场
  扫描的增量刷新）；重叠区不一致（除权除息导致前复权历史修订）或缺口
  过大时自动回退全量拉取；环境变量 ``ALPHA_FORGE_INCR_CACHE=0`` 可关闭。

存储格式优先 Parquet（若安装了 pyarrow/fastparquet），否则回退 pickle，
两者都零心智负担地保留 dtype 与列结构；旁挂一个 ``.meta.json`` 记录
标的/周期/复权/行数/抓取时间，便于审计与新鲜度判断。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

#: 复权口径别名 -> TickFlow 原生取值
_ADJUST_ALIASES = {
    "qfq": "forward",
    "forward": "forward",
    "front": "forward",
    "前复权": "forward",
    "hfq": "backward",
    "backward": "backward",
    "后复权": "backward",
    "none": "none",
    "raw": "none",
    "不复权": "none",
    "": "forward",
}

#: 缓存默认存活时长（秒）；日 K 及以上周期一天更新一次，默认 1 天
DEFAULT_TTL = 24 * 3600

#: 分钟级周期盘中持续更新，默认 30 分钟即视为陈旧
MINUTE_TTL = 30 * 60

#: 增量更新时与缓存尾部比对的重叠 K 线数（校验复权口径未修订）
INCR_OVERLAP = 5

#: 增量拉取的尾部上限；估算缺口超过此数直接全量重拉更划算
INCR_MAX_TAIL = 120

#: 各周期把日历天换算为 K 线根数的除数（仅日级及以上支持增量）
_PERIOD_DAYS = {"1d": 1, "1w": 7, "1M": 28, "1Q": 90, "1Y": 365}


def normalize_adjust(adjust: str | None) -> str:
    """把复权别名归一化为 ``forward`` / ``backward`` / ``none``。

    默认（None 或未知）返回 ``forward``（前复权），这是回测推荐口径。
    """
    if adjust is None:
        return "forward"
    key = str(adjust).strip().lower()
    return _ADJUST_ALIASES.get(key, "forward")


@dataclass
class CacheConfig:
    """缓存配置。

    Attributes:
        cache_dir: 缓存根目录。
        ttl_seconds: 缓存新鲜度阈值（秒）；超过则重新拉取。
        enabled: 是否启用缓存。
    """

    cache_dir: Path
    ttl_seconds: int = DEFAULT_TTL
    enabled: bool = True


def _project_cache_dir() -> Path:
    """默认缓存目录：项目根目录下的 ``.cache/klines``。"""
    # 本文件位于 scripts/data/cache.py -> parents[2] 为项目根
    root = Path(__file__).resolve().parents[2]
    return root / ".cache" / "klines"


def default_config(period: str = "1d") -> CacheConfig:
    """从环境变量构造默认缓存配置（TTL 按周期分级）。

    - ``ALPHA_FORGE_CACHE_DIR``：自定义缓存目录；
    - ``ALPHA_FORGE_NO_CACHE=1``：全局关闭缓存；
    - ``ALPHA_FORGE_CACHE_TTL``：显式设置时全局覆盖分级默认值（秒）；
    - 未显式设置时：分钟级周期默认 30 分钟，日级及以上默认 1 天。
    """
    env_dir = os.environ.get("ALPHA_FORGE_CACHE_DIR")
    cache_dir = Path(env_dir) if env_dir else _project_cache_dir()
    env_ttl = os.environ.get("ALPHA_FORGE_CACHE_TTL")
    if env_ttl:
        ttl = int(env_ttl)
    else:
        ttl = MINUTE_TTL if str(period).endswith("m") else DEFAULT_TTL
    enabled = os.environ.get("ALPHA_FORGE_NO_CACHE", "") not in ("1", "true", "True")
    return CacheConfig(cache_dir=cache_dir, ttl_seconds=ttl, enabled=enabled)


def _sanitize(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(text))


def _key(symbol: str, period: str, adjust: str, source: str = "auto") -> str:
    return (
        f"{_sanitize(symbol)}__{_sanitize(period)}__{_sanitize(adjust)}"
        f"__{_sanitize(source)}"
    )


def _write_df(df: pd.DataFrame, base: Path) -> str:
    """写入 DataFrame，优先 Parquet，回退 pickle。返回实际格式名。"""
    try:
        df.to_parquet(base.with_suffix(".parquet"))
        return "parquet"
    except (ImportError, ValueError):
        df.to_pickle(base.with_suffix(".pkl"))
        return "pickle"


def _read_df(base: Path, fmt: str) -> pd.DataFrame:
    if fmt == "parquet":
        return pd.read_parquet(base.with_suffix(".parquet"))
    return pd.read_pickle(base.with_suffix(".pkl"))


def load_klines(
    fetch_fn: Callable[[], pd.DataFrame],
    symbol: str,
    period: str,
    count: int,
    adjust: str,
    config: CacheConfig | None = None,
    source: str = "auto",
    fetch_tail_fn: Callable[[int], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """带缓存地读取 K 线。

    Args:
        fetch_fn: 无参回调，命中失败时用它拉取原始数据（返回升序 DataFrame）。
        symbol/period/adjust: 缓存键要素（adjust 应已归一化）。
        count: 请求的 K 线数量；缓存行数不少于它且新鲜时才复用。
        config: 缓存配置；None 时用 ``default_config(period)``（TTL 按周期分级）。
        source: 数据源标签（tickflow/akshare/auto），不同源的缓存互不混用。
        fetch_tail_fn: 按根数拉取尾部 K 线的回调 ``fn(n) -> DataFrame``；
            提供时陈旧缓存优先增量更新（仅日级及以上周期）。

    Returns:
        至少含 ``close`` 列、按时间升序的 DataFrame（尾部 count 行）。
    """
    config = config or default_config(period)
    if not config.enabled:
        return fetch_fn()

    config.cache_dir.mkdir(parents=True, exist_ok=True)
    base = config.cache_dir / _key(symbol, period, adjust, source)
    meta_path = base.with_suffix(".meta.json")

    meta = None
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = None

    # 命中判定：新鲜 + 行数足够 + 数据文件存在
    if meta is not None:
        fresh = (time.time() - meta.get("fetched_at", 0)) < config.ttl_seconds
        enough = meta.get("rows", 0) >= count
        data_file = base.with_suffix(
            ".parquet" if meta.get("format") == "parquet" else ".pkl"
        )
        if fresh and enough and data_file.exists():
            df = _read_df(base, meta.get("format", "pickle"))
            return df.tail(count).reset_index(drop=True)

        # 陈旧但行数足够：尝试增量更新（只拉尾部小段，失败回退全量）
        if (
            enough
            and data_file.exists()
            and fetch_tail_fn is not None
            and _incr_enabled(period)
        ):
            cached = _read_df(base, meta.get("format", "pickle"))
            merged = _incremental_update(cached, fetch_tail_fn, symbol, period)
            if merged is not None:
                fmt = _write_df(merged, base)
                _write_meta(meta_path, symbol, period, adjust, source, merged, fmt)
                return merged.tail(count).reset_index(drop=True)

    # 未命中：拉取并落盘；失败时回退到过期缓存（如有）
    try:
        df = fetch_fn()
    except Exception:
        if meta is not None:
            data_file = base.with_suffix(
                ".parquet" if meta.get("format") == "parquet" else ".pkl"
            )
            if data_file.exists():
                print(
                    f"[warn] 拉取 {symbol} 失败，回退使用过期缓存"
                    f"（{meta.get('rows')} 行，抓取于 {meta.get('fetched_date')}）。",
                    file=sys.stderr,
                )
                df = _read_df(base, meta.get("format", "pickle"))
                return df.tail(count).reset_index(drop=True)
        raise

    fmt = _write_df(df, base)
    _write_meta(meta_path, symbol, period, adjust, source, df, fmt)
    return df.tail(count).reset_index(drop=True) if count < len(df) else df


def _incr_enabled(period: str) -> bool:
    """增量更新开关：仅日级及以上周期，且未被环境变量关闭。"""
    if os.environ.get("ALPHA_FORGE_INCR_CACHE", "") in ("0", "false", "False"):
        return False
    return str(period) in _PERIOD_DAYS


def _date_column(df: pd.DataFrame) -> str | None:
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            return col
    return None


def _incremental_update(
    cached: pd.DataFrame,
    fetch_tail_fn: Callable[[int], pd.DataFrame],
    symbol: str,
    period: str,
) -> pd.DataFrame | None:
    """拉尾部小段与缓存合并；任何不确定情况返回 None 回退全量。

    步骤：估算缺口根数 -> 拉取（缺口 + INCR_OVERLAP）根 -> 严格早于缓存末日的
    已完成 K 线逐日比对 close（相对误差 > 0.1% 视为复权历史修订，需全量）->
    缓存末日及之后的行用尾段数据替换/追加（末日可能是盘中未完成 K 线，
    收盘价会变，不参与一致性校验，直接刷新）。
    """
    date_col = _date_column(cached)
    if date_col is None or len(cached) == 0:
        return None
    try:
        cached_dates = pd.to_datetime(cached[date_col])
    except (ValueError, TypeError):
        return None

    last_cached = cached_dates.iloc[-1]
    elapsed_days = max(0.0, (pd.Timestamp.now() - last_cached).total_seconds() / 86400.0)
    est_missing = math.ceil(elapsed_days / _PERIOD_DAYS[str(period)]) + 1
    if est_missing > INCR_MAX_TAIL:
        return None  # 缺口过大，全量重拉更划算

    try:
        tail = fetch_tail_fn(est_missing + INCR_OVERLAP)
    except Exception:
        return None  # 拉取失败交给外层全量路径处理（含过期回退）
    tail_col = _date_column(tail)
    if tail_col is None or len(tail) == 0 or "close" not in tail.columns:
        return None
    tail = tail.copy()
    tail_dates = pd.to_datetime(tail[tail_col])

    # 一致性校验只看**严格早于缓存末日**的已完成 K 线：
    # 末日可能是盘中活动 K 线，收盘价天然会变，不是复权修订
    strict_mask = tail_dates < last_cached
    strict = tail.loc[strict_mask]
    if strict.empty:
        return None  # 尾段未覆盖到已完成区间，无法衔接校验，回退全量
    cached_close = pd.Series(
        cached["close"].astype(float).to_numpy(), index=cached_dates
    )
    for ts, close in zip(tail_dates[strict_mask], strict["close"].astype(float)):
        ref = cached_close.get(ts)
        if ref is None or ref == 0 or abs(close / ref - 1.0) > 0.001:
            print(
                f"[warn] {symbol} 重叠区收盘价不一致（疑似除权除息导致复权历史修订），"
                "回退全量重拉。",
                file=sys.stderr,
            )
            return None

    # 缓存末日及之后：用尾段数据替换/追加（刷新末日 + 新增 K 线）
    refresh = tail.loc[tail_dates >= last_cached]
    if refresh.empty:
        return cached  # 尾段没有末日及以后的数据（罕见），仅刷新时间戳
    keep = cached.loc[cached_dates < last_cached]
    return pd.concat([keep, refresh], ignore_index=True)


def _write_meta(
    meta_path: Path,
    symbol: str,
    period: str,
    adjust: str,
    source: str,
    df: pd.DataFrame,
    fmt: str,
) -> None:
    meta_path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "period": period,
                "adjust": adjust,
                "source": source,
                "rows": int(len(df)),
                "format": fmt,
                "fetched_at": time.time(),
                "fetched_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
