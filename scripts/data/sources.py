"""数据源抽象：TickFlow 主源 + akshare 兜底源。

动机：datafeed 原先绑定 TickFlow 单一数据源，服务不可用时整个链路失效。
本模块把「拉取单标的 K 线」抽象为 ``DataSource`` 协议：

- ``TickFlowSource``：主源，多市场全周期；
- ``AkshareSource``：兜底源，仅 A 股日/周/月 K（免费、无需 Key）；
- ``get_sources``：按环境变量 ``ALPHA_FORGE_DATA_SOURCE`` 返回数据源链
  （``tickflow`` / ``akshare`` 强制单源，缺省 auto = TickFlow 失败自动降级）。

所有源返回列名归一的升序 DataFrame：``trade_date/open/high/low/close/volume``。
"""

from __future__ import annotations

import os
from typing import Protocol

import pandas as pd

# 需要 TICKFLOW_API_KEY 的接口在报错/告警时统一附带此指引，
# 提醒用户去哪里申请、如何设置与验证。
API_KEY_HELP = (
    "如何获取并配置 TICKFLOW_API_KEY：\n"
    "  1. 前往 https://tickflow.org 注册并在控制台申请 API Key；\n"
    '  2. 设置环境变量（macOS/Linux）：export TICKFLOW_API_KEY="your-api-key"；\n'
    "     持久化写入 shell 配置：\n"
    "       echo 'export TICKFLOW_API_KEY=\"your-api-key\"' >> ~/.zshrc && source ~/.zshrc\n"
    "  3. 验证：执行 echo $TICKFLOW_API_KEY 应输出你的 Key。"
)

#: A 股市场后缀（akshare 兜底仅覆盖这些市场）
_ASTOCK_SUFFIXES = (".SH", ".SZ", ".BJ")


class DataSource(Protocol):
    """单标的 K 线数据源协议。"""

    name: str

    def supports(self, symbol: str, period: str) -> bool:
        """该源是否覆盖此标的与周期。"""
        ...

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        """拉取 K 线，返回列名归一、按时间升序的 DataFrame。"""
        ...


def _needs_api_key(period: str) -> bool:
    """分钟级周期需要 TickFlow 完整服务。"""
    return period.endswith("m")


def get_tickflow_client(period: str = "1d"):
    """根据周期与环境变量选择 TickFlow 客户端。"""
    from tickflow import TickFlow

    has_key = bool(os.environ.get("TICKFLOW_API_KEY"))
    if has_key:
        return TickFlow()
    if _needs_api_key(period):
        raise RuntimeError(
            f"周期 {period} 需要实时/分钟数据，请先配置环境变量 TICKFLOW_API_KEY 后重试。\n"
            + API_KEY_HELP
        )
    return TickFlow.free()


def _validate_and_sort(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """校验必需列并按时间升序。"""
    if df is None or len(df) == 0:
        raise RuntimeError(f"未获取到 {symbol} 的 K 线数据，请检查代码与周期。")
    if "close" not in df.columns:
        raise RuntimeError(f"返回数据缺少 close 列，实际列：{list(df.columns)}")
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
    return df


class TickFlowSource:
    """TickFlow 主数据源：多市场、全周期。"""

    name = "tickflow"

    def supports(self, symbol: str, period: str) -> bool:
        return True

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        tf = get_tickflow_client(period)
        df = tf.klines.get(
            symbol, period=period, count=count, adjust=adjust, as_dataframe=True
        )
        return _validate_and_sort(df, symbol)


#: akshare 周期映射：本项目周期 -> ak period 参数
_AK_PERIODS = {"1d": "daily", "1w": "weekly", "1M": "monthly"}

#: 复权口径映射：归一化口径 -> ak adjust 参数
_AK_ADJUSTS = {"forward": "qfq", "backward": "hfq", "none": ""}

#: akshare 中文列名 -> 标准列名
_AK_COLUMNS = {
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}


class AkshareSource:
    """akshare 兜底数据源：仅 A 股日/周/月 K，免费无需 Key。"""

    name = "akshare"

    def supports(self, symbol: str, period: str) -> bool:
        return (
            symbol.upper().endswith(_ASTOCK_SUFFIXES) and period in _AK_PERIODS
        )

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        import akshare as ak

        code = symbol.split(".")[0]
        df = ak.stock_zh_a_hist(
            symbol=code,
            period=_AK_PERIODS[period],
            adjust=_AK_ADJUSTS.get(adjust, "qfq"),
        )
        if df is None or len(df) == 0:
            raise RuntimeError(f"akshare 未返回 {symbol} 的 K 线数据。")
        df = df.rename(columns=_AK_COLUMNS)
        keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = _validate_and_sort(df, symbol)
        return df.tail(count).reset_index(drop=True)


def source_label() -> str:
    """当前数据源配置标签（缓存键的一部分）：tickflow/akshare/auto。"""
    forced = os.environ.get("ALPHA_FORGE_DATA_SOURCE", "").strip().lower()
    return forced if forced in ("tickflow", "akshare") else "auto"


def get_sources() -> list[DataSource]:
    """按环境变量返回数据源链（顺序即优先级）。"""
    label = source_label()
    if label == "tickflow":
        return [TickFlowSource()]
    if label == "akshare":
        return [AkshareSource()]
    return [TickFlowSource(), AkshareSource()]
