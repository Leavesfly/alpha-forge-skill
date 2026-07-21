"""财务数据获取：多标的财务指标（用于价值/质量/规模因子）。

从 datafeed.py 拆分而来，职责单一化。
需要 TICKFLOW_API_KEY 且账号具备财务数据权限。
"""

from __future__ import annotations

import os

import pandas as pd
from tickflow import TickFlow

from data.sources import API_KEY_HELP


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
