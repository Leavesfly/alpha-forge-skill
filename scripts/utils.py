"""跨模块公共工具函数。

提取各引擎模块中重复出现的通用逻辑，避免 DRY 违反：
- ``resolve_time_index``：从 OHLCV DataFrame 解析时间索引（4 处重复）
- ``series_last``：安全取序列末值（2 处重复）
- ``safe_round``：对浮点值安全四舍五入（NaN/inf → None）（2 处重复）

延迟导入规范（项目约定）：
- **重量级/可选依赖**（matplotlib, lightgbm, akshare, scoring.plot）仅在功能分支内延迟导入，
  避免拖慢 CLI 启动速度（用户可能只跑 --help）；
- **核心依赖**（pandas, numpy, tickflow）一律顶部导入；
- **循环依赖规避**：模块间双向引用时用函数内延迟导入；
- **TYPE_CHECKING**：仅用于类型注解的导入放在 ``if TYPE_CHECKING:`` 块内。
"""

from __future__ import annotations

import math

import pandas as pd


def resolve_time_index(df: pd.DataFrame) -> pd.Index:
    """从常见时间列构造 DatetimeIndex，找不到则用序号索引。

    依次尝试 trade_date / date / datetime / time 列；
    显式转为 ``pd.DatetimeIndex``（而非保留 Series），确保后续
    ``isinstance(idx, pd.DatetimeIndex)`` 判断正确（影响周/月重采样逻辑）。

    Args:
        df: 含 OHLCV 数据的 DataFrame。

    Returns:
        时间索引（DatetimeIndex）或序号索引（RangeIndex）。
    """
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            try:
                return pd.DatetimeIndex(pd.to_datetime(df[col]))
            except (ValueError, TypeError):
                return pd.Index(df[col])
    return pd.RangeIndex(len(df))


def series_last(series: pd.Series) -> float:
    """取序列末值为 float；空序列返回 NaN。"""
    return float(series.iloc[-1]) if len(series) else float("nan")


def safe_round(v, digits: int = 4):
    """对浮点值安全四舍五入：有限值 round，NaN/inf 返回 None，非浮点原样返回。"""
    if isinstance(v, float) and math.isfinite(v):
        return round(v, digits)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def extract_close(df: pd.DataFrame) -> pd.Series:
    """从 OHLCV DataFrame 提取带时间索引的收盘价序列。

    优先使用 trade_date 列作为 DatetimeIndex，否则用序号索引。
    """
    close = df["close"].astype(float).reset_index(drop=True)
    if "trade_date" in df.columns:
        close.index = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
    return close
