"""财务数据获取：多标的财务指标（用于价值/质量/规模因子）。

从 datafeed.py 拆分而来，职责单一化。
需要 TICKFLOW_API_KEY 且账号具备财务数据权限。

本地优先：结果经 ``cache.load_table`` 按标的集合的哈希落盘（TTL 24 小时）。
财务指标季度更新，而因子研究会对同一股票池反复调用；缓存后重复运行不再
消耗 API 配额，且 ``ALPHA_FORGE_OFFLINE=1`` 对其同样生效。
"""

from __future__ import annotations

import hashlib
import os

import pandas as pd

from data.sources import API_KEY_HELP

#: 财务指标缓存 TTL：季度更新，24 小时
FUNDAMENTALS_TTL = 24 * 3600


def fetch_fundamentals(symbols: list[str]) -> pd.DataFrame | None:
    """获取多标的财务指标（用于价值/质量/规模因子）。

    需要 TICKFLOW_API_KEY 且账号具备财务数据权限。无权限、未配置或
    接口异常时返回 None（调用方据此跳过基本面因子）。结果本地优先缓存 24 小时。

    Returns:
        含 symbol、period_end 及各财务指标列的 DataFrame；不可用时返回 None。
    """
    if not os.environ.get("TICKFLOW_API_KEY"):
        print(
            "[warn] 未配置 TICKFLOW_API_KEY，价值/质量/规模等基本面因子将被跳过。\n"
            + API_KEY_HELP
        )
        return None

    from data.cache import config_with_ttl, load_table

    return load_table(
        lambda: _fetch_fundamentals_remote(symbols),
        f"fundamentals_{_symbols_key(symbols)}",
        config_with_ttl(FUNDAMENTALS_TTL),
    )


def _symbols_key(symbols: list[str]) -> str:
    """标的集合 -> 稳定的短缓存键。

    股票池可能上千只，代码直接拼进文件名不可行；按排序后内容取哈希，
    保证「同一集合不同顺序」命中同一份缓存。
    """
    joined = ",".join(sorted(symbols))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return f"{len(symbols)}_{digest}"


def _fetch_fundamentals_remote(symbols: list[str]) -> pd.DataFrame | None:
    """直连 TickFlow 拉取财务指标（不经缓存）。"""
    from tickflow import TickFlow

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
