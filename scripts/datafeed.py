"""TickFlow 数据获取辅助。

统一从 TickFlow 拉取 K 线为回测所需的 OHLCV DataFrame。
默认使用免费服务（历史日 K 足以回测）；配置了 TICKFLOW_API_KEY 时
可自动切换到完整服务以支持分钟级数据。
"""

from __future__ import annotations

import os

import pandas as pd
from tickflow import TickFlow

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


def _needs_api_key(period: str) -> bool:
    """分钟级周期需要完整服务。"""
    return period.endswith("m")


def get_client(period: str = "1d") -> TickFlow:
    """根据周期与环境变量选择 TickFlow 客户端。"""
    has_key = bool(os.environ.get("TICKFLOW_API_KEY"))
    if has_key:
        return TickFlow()
    if _needs_api_key(period):
        raise RuntimeError(
            f"周期 {period} 需要实时/分钟数据，请先配置环境变量 TICKFLOW_API_KEY 后重试。\n"
            + API_KEY_HELP
        )
    return TickFlow.free()


def fetch_ohlcv(
    symbol: str,
    period: str = "1d",
    count: int = 500,
) -> pd.DataFrame:
    """拉取单标的 K 线并返回按时间升序的 OHLCV DataFrame。

    Returns:
        至少包含 ``close`` 列的 DataFrame。
    """
    tf = get_client(period)
    df = tf.klines.get(symbol, period=period, count=count, as_dataframe=True)

    if df is None or len(df) == 0:
        raise RuntimeError(f"未获取到 {symbol} 的 K 线数据，请检查代码与周期。")

    if "close" not in df.columns:
        raise RuntimeError(
            f"返回数据缺少 close 列，实际列：{list(df.columns)}"
        )

    # 保证按时间升序
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
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
) -> pd.DataFrame:
    """拉取多标的收盘价并按共同交易日对齐。

    Args:
        symbols: 标的代码列表。
        period: K 线周期。
        count: 每个标的拉取的 K 线数量。

    Returns:
        收盘价 DataFrame（索引为日期，列为标的代码），已按共同日期内连接对齐。
    """
    series: dict[str, pd.Series] = {}
    for sym in symbols:
        df = fetch_ohlcv(sym, period=period, count=count)
        date_col = _date_column(df)
        idx = pd.to_datetime(df[date_col]) if date_col else pd.RangeIndex(len(df))
        series[sym] = pd.Series(df["close"].astype(float).values, index=idx)

    prices = pd.DataFrame(series).dropna(how="any").sort_index()
    if prices.empty or prices.shape[1] < 2:
        raise RuntimeError(
            "多标的价格对齐后为空或不足 2 个标的，请检查代码、周期与共同交易日。"
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
