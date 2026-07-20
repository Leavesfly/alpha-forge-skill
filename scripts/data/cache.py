"""K 线本地缓存与复权口径管理。

动机：
- 原 datafeed 每次调用都直连网络，重复回测/寻优会反复拉取相同数据；
- 复权口径（前/后/不复权）未显式声明，回测可能因口径不一致而失真。

本模块提供：
- ``normalize_adjust``：把 qfq/hfq/none 等别名归一到 TickFlow 的 forward/backward/none；
- ``load_klines``：带缓存的 K 线读取——命中且新鲜则读本地，否则拉取并落盘。

存储格式优先 Parquet（若安装了 pyarrow/fastparquet），否则回退 pickle，
两者都零心智负担地保留 dtype 与列结构；旁挂一个 ``.meta.json`` 记录
标的/周期/复权/行数/抓取时间，便于审计与新鲜度判断。
"""

from __future__ import annotations

import json
import os
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

#: 缓存默认存活时长（秒）；日 K 数据一天更新一次，默认 1 天
DEFAULT_TTL = 24 * 3600


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


def default_config() -> CacheConfig:
    """从环境变量构造默认缓存配置。

    - ``ALPHA_FORGE_CACHE_DIR``：自定义缓存目录；
    - ``ALPHA_FORGE_NO_CACHE=1``：全局关闭缓存；
    - ``ALPHA_FORGE_CACHE_TTL``：新鲜度阈值（秒）。
    """
    env_dir = os.environ.get("ALPHA_FORGE_CACHE_DIR")
    cache_dir = Path(env_dir) if env_dir else _project_cache_dir()
    ttl = int(os.environ.get("ALPHA_FORGE_CACHE_TTL", DEFAULT_TTL))
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
) -> pd.DataFrame:
    """带缓存地读取 K 线。

    Args:
        fetch_fn: 无参回调，命中失败时用它拉取原始数据（返回升序 DataFrame）。
        symbol/period/adjust: 缓存键要素（adjust 应已归一化）。
        count: 请求的 K 线数量；缓存行数不少于它且新鲜时才复用。
        config: 缓存配置；None 时用 ``default_config()``。
        source: 数据源标签（tickflow/akshare/auto），不同源的缓存互不混用。

    Returns:
        至少含 ``close`` 列、按时间升序的 DataFrame（尾部 count 行）。
    """
    config = config or default_config()
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
                    f"（{meta.get('rows')} 行，抓取于 {meta.get('fetched_date')}）。"
                )
                df = _read_df(base, meta.get("format", "pickle"))
                return df.tail(count).reset_index(drop=True)
        raise

    fmt = _write_df(df, base)
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
    return df.tail(count).reset_index(drop=True) if count < len(df) else df
