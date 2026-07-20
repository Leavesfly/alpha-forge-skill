"""多源交叉验证：同时拉取两个数据源的 K 线并比对 OHLCV 差异。

动机：单数据源可能存在复权口径偏差、缺失交易日、爬虫延迟等问题；
对于实盘信号或因子研究场景，需要多源交叉确认数据可靠性。

本模块提供：
- ``verify_symbol``：对单标的拉取两个源并输出结构化差异报告；
- ``VerifyResult``：差异报告数据类（含逐列统计与 pass/fail 判定）。

设计约束：
- 仅对两个源**共同覆盖**的标的/周期有效；
- 不影响现有 auto/tickflow/baostock/akshare 数据源切换逻辑——验证是独立旁路；
- 阈值可由调用方指定，默认价格列 0.5%、成交量列 2%。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import pandas as pd

from .sources import AkshareSource, BaostockSource, TickFlowSource

#: 可用对照源注册表
VERIFY_SOURCES: dict[str, type] = {
    "tickflow": TickFlowSource,
    "baostock": BaostockSource,
    "akshare": AkshareSource,
}

#: 默认相对误差阈值（百分比）
DEFAULT_PRICE_THRESHOLD = 0.5  # OHLC 列
DEFAULT_VOLUME_THRESHOLD = 2.0  # 成交量列

#: 需要比对的数值列
_COMPARE_COLS = ("open", "high", "low", "close", "volume")


@dataclass
class ColumnDiff:
    """单列差异统计。"""

    column: str
    max_rel_pct: float  # 最大相对误差 (%)
    mean_rel_pct: float  # 平均相对误差 (%)
    mismatch_count: int  # 超阈值行数
    threshold_pct: float  # 使用的阈值 (%)

    @property
    def passed(self) -> bool:
        return self.mismatch_count == 0


@dataclass
class VerifyResult:
    """单标的交叉验证结果。"""

    symbol: str
    period: str
    source_a: str  # 主源名称
    source_b: str  # 对照源名称
    rows_a: int  # 主源行数
    rows_b: int  # 对照源行数
    aligned_rows: int  # 对齐后共同行数
    columns: list[ColumnDiff] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """所有列均通过则 pass。"""
        return all(c.passed for c in self.columns) and self.aligned_rows > 0

    def summary(self) -> str:
        """一句话摘要。"""
        if self.aligned_rows == 0:
            return f"{self.symbol}: 两源无共同交易日，无法比对。"
        status = "PASS" if self.passed else "FAIL"
        worst = max(self.columns, key=lambda c: c.max_rel_pct) if self.columns else None
        detail = f"最大偏差 {worst.column} {worst.max_rel_pct:.3f}%" if worst else ""
        return (
            f"{self.symbol} [{status}] 对齐 {self.aligned_rows} 行，{detail}"
        )


def _date_column(df: pd.DataFrame) -> str | None:
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            return col
    return None


def _align_frames(
    df_a: pd.DataFrame, df_b: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按交易日内连接对齐两个 DataFrame。"""
    col_a = _date_column(df_a)
    col_b = _date_column(df_b)
    if col_a is None or col_b is None:
        return df_a.iloc[0:0], df_b.iloc[0:0]

    a = df_a.copy()
    b = df_b.copy()
    a["_vdate"] = pd.to_datetime(a[col_a]).dt.normalize()
    b["_vdate"] = pd.to_datetime(b[col_b]).dt.normalize()

    merged = a.merge(b, on="_vdate", suffixes=("_a", "_b"), how="inner")
    if merged.empty:
        return merged, merged

    # 拆回两个对齐后的子帧
    cols_a = [c for c in merged.columns if c.endswith("_a") or c == "_vdate"]
    cols_b = [c for c in merged.columns if c.endswith("_b") or c == "_vdate"]
    return merged[cols_a].reset_index(drop=True), merged[cols_b].reset_index(drop=True)


def _compare_column(
    series_a: pd.Series,
    series_b: pd.Series,
    col_name: str,
    threshold_pct: float,
) -> ColumnDiff:
    """比对单列，返回差异统计。"""
    a = series_a.astype(float)
    b = series_b.astype(float)
    # 避免除零：分母取两值绝对值的较大者
    denom = pd.concat([a.abs(), b.abs()], axis=1).max(axis=1).replace(0, 1.0)
    rel_pct = ((a - b).abs() / denom * 100.0)
    mismatch = int((rel_pct > threshold_pct).sum())
    return ColumnDiff(
        column=col_name,
        max_rel_pct=float(rel_pct.max()) if len(rel_pct) > 0 else 0.0,
        mean_rel_pct=float(rel_pct.mean()) if len(rel_pct) > 0 else 0.0,
        mismatch_count=mismatch,
        threshold_pct=threshold_pct,
    )


def verify_symbol(
    symbol: str,
    period: str = "1d",
    count: int = 500,
    adjust: str = "forward",
    price_threshold_pct: float = DEFAULT_PRICE_THRESHOLD,
    volume_threshold_pct: float = DEFAULT_VOLUME_THRESHOLD,
    source_b_name: str = "baostock",
) -> VerifyResult:
    """对单标的执行多源交叉验证。

    从 TickFlow（主源）和指定对照源拉取 K 线，按交易日对齐后逐列比对 OHLCV。
    任一列最大相对误差超过阈值则判定 FAIL。

    Args:
        symbol: 标的代码（如 600000.SH）。
        period: K 线周期。
        count: 拉取根数。
        adjust: 复权口径。
        price_threshold_pct: 价格列（OHLC）相对误差阈值（%）。
        volume_threshold_pct: 成交量列相对误差阈值（%）。
        source_b_name: 对照源名称（baostock/akshare/tickflow），默认 baostock。

    Returns:
        VerifyResult 结构化报告。

    Raises:
        RuntimeError: 对照源不支持该标的/周期，或两源均拉取失败。
    """
    source_a = TickFlowSource()
    source_b_cls = VERIFY_SOURCES.get(source_b_name)
    if source_b_cls is None:
        raise RuntimeError(
            f"未知对照源 '{source_b_name}'，可选：{', '.join(VERIFY_SOURCES)}"
        )
    source_b = source_b_cls()

    if not source_b.supports(symbol, period):
        raise RuntimeError(
            f"对照源 {source_b_name} 不支持 {symbol} {period}"
            f"（baostock 仅沪深日/周/月 K，akshare 仅 A 股日/周/月 K）。"
        )

    errors: list[str] = []

    # 拉取主源
    try:
        df_a = source_a.fetch(symbol, period, count, adjust)
    except Exception as exc:
        errors.append(f"TickFlow: {type(exc).__name__}: {exc}")
        df_a = None

    # 拉取对照源
    try:
        df_b = source_b.fetch(symbol, period, count, adjust)
    except Exception as exc:
        errors.append(f"{source_b_name}: {type(exc).__name__}: {exc}")
        df_b = None

    if df_a is None and df_b is None:
        raise RuntimeError(
            f"交叉验证失败：两个数据源均无法拉取 {symbol} {period}：\n  "
            + "\n  ".join(errors)
        )
    if df_a is None:
        raise RuntimeError(
            f"主源 TickFlow 拉取失败，无法完成交叉验证：{errors[0]}"
        )
    if df_b is None:
        raise RuntimeError(
            f"对照源 {source_b_name} 拉取失败，无法完成交叉验证：{errors[-1]}"
        )

    # 对齐
    aligned_a, aligned_b = _align_frames(df_a, df_b)
    aligned_rows = len(aligned_a)

    result = VerifyResult(
        symbol=symbol,
        period=period,
        source_a=source_a.name,
        source_b=source_b.name,
        rows_a=len(df_a),
        rows_b=len(df_b),
        aligned_rows=aligned_rows,
    )

    if aligned_rows == 0:
        result.warnings.append("两源无共同交易日，无法比对。")
        return result

    # 行数差异告警
    row_diff = abs(len(df_a) - len(df_b))
    if row_diff > max(5, count * 0.02):
        result.warnings.append(
            f"两源行数差异较大：{source_a.name} {len(df_a)} 行 vs {source_b.name} {len(df_b)} 行"
            f"（差 {row_diff} 行），可能存在缺失交易日。"
        )

    # 逐列比对
    for col in _COMPARE_COLS:
        col_a_name = f"{col}_a"
        col_b_name = f"{col}_b"
        if col_a_name not in aligned_a.columns or col_b_name not in aligned_b.columns:
            # 尝试不带后缀（merge 可能无冲突时保留原名）
            if col in aligned_a.columns and col in aligned_b.columns:
                col_a_name = col
                col_b_name = col
            else:
                result.warnings.append(f"列 {col} 在某一源中缺失，跳过比对。")
                continue

        threshold = volume_threshold_pct if col == "volume" else price_threshold_pct
        diff = _compare_column(
            aligned_a[col_a_name], aligned_b[col_b_name], col, threshold
        )
        result.columns.append(diff)

    return result
