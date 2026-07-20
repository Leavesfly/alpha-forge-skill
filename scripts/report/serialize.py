"""结果序列化：把回测结果转成 JSON 友好的结构，便于 agent 消费。

所有 CLI 的 ``--json`` 输出遵循统一约定：
- 顶层固定包含 ``schema`` / ``command`` / ``generated_at`` 三个元信息键
  （由 :func:`attach_meta` 附加），agent 可据 ``command`` 分发解析；
- 数值均为 Python 原生类型（numpy/pandas 标量经 :func:`_to_native` 转换）；
- 时间一律 ISO-8601 字符串；表格类结果经 :func:`frame_records` 转为记录列表。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

# JSON 输出结构版本：字段只增不改不删；若未来有破坏性变更再升版本号
SCHEMA_VERSION = "alpha-forge/1"


def _to_native(obj: Any) -> Any:
    """递归地把 numpy / pandas 标量转成 Python 原生类型。"""
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    return obj


def attach_meta(payload: dict, command: str) -> dict:
    """为 JSON 输出附加统一元信息（schema/command/generated_at）。

    Args:
        payload: 命令自身的结果字段（保持在顶层，向后兼容）。
        command: 命令名，如 ``backtest`` / ``optimize`` / ``portfolio``。
    """
    return {
        "schema": SCHEMA_VERSION,
        "command": command,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }


def frame_records(df: pd.DataFrame, max_rows: int = 500) -> list[dict]:
    """把 DataFrame 转为 JSON 友好的记录列表（原生类型，最多 max_rows 行）。"""
    return [_to_native(dict(row)) for _, row in df.head(max_rows).iterrows()]


def result_to_dict(
    result,
    strategy_name: str = "",
    config: dict | None = None,
    max_trades: int = 200,
) -> dict:
    """把 BacktestResult 转为可 JSON 序列化的字典。

    Args:
        result: BacktestResult 实例。
        strategy_name: 策略显示名。
        config: 运行配置（成本、成交价、复权等），原样带出便于复现。
        max_trades: 交易明细最多导出条数。
    """
    idx = result.equity.index
    trades_df = result.trades
    trades = [
        {
            "time": str(row["time"]),
            "action": row["action"],
            "price": float(row["price"]),
        }
        for _, row in trades_df.head(max_trades).iterrows()
    ]
    return _to_native(
        {
            "symbol": result.symbol,
            "period": result.period,
            "strategy": strategy_name,
            "config": config or {},
            "range": {
                "start": str(idx[0]) if len(idx) else None,
                "end": str(idx[-1]) if len(idx) else None,
                "num_periods": int(len(idx)),
            },
            "metrics": dict(result.metrics),
            "benchmark_metrics": dict(result.benchmark_metrics),
            "equity_end": float(result.equity.iloc[-1]) if len(result.equity) else 1.0,
            "benchmark_equity_end": (
                float(result.benchmark_equity.iloc[-1])
                if len(result.benchmark_equity) else 1.0
            ),
            "trades_count": int(len(trades_df)),
            "trades": trades,
        }
    )


def to_json(payload: dict) -> str:
    """序列化为带缩进的 UTF-8 JSON 字符串。"""
    return json.dumps(_to_native(payload), ensure_ascii=False, indent=2, default=str)
