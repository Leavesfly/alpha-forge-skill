"""rich 终端渲染：绩效对比表、结果表格与进度条。

统一封装终端美化输出，所有函数在 rich 不可用时自动退回纯文本，
CLI 调用方无需感知差异。JSON 模式下调用方传 ``stderr=True``，
保证 stdout 只留给结构化输出。
"""

from __future__ import annotations

import sys

import pandas as pd

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table

    _HAS_RICH = True
except ImportError:  # pragma: no cover - 依赖缺失时纯文本兜底
    _HAS_RICH = False

#: 绩效表行定义：(指标键, 中文标签, 格式)
_METRIC_ROWS = [
    ("total_return", "累计收益率", "pct"),
    ("annual_return", "年化收益率", "pct"),
    ("annual_volatility", "年化波动率", "pct"),
    ("sharpe", "夏普比率", "num"),
    ("sortino", "索提诺比率", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar", "卡玛比率", "num"),
    ("num_trades", "交易次数", "int"),
    ("win_rate", "胜率", "pct"),
    ("num_periods", "回测周期数", "int"),
]


def _fmt(value, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:+.2f}%"
    if kind == "int":
        return f"{int(value)}"
    return f"{value:.2f}"


def _console(stderr: bool = False) -> "Console":
    target = sys.stderr if stderr else sys.stdout
    # 非交互终端（管道/重定向）不受 80 列默认宽度限制，避免列被截断
    width = None if target.isatty() else 200
    return Console(file=target, soft_wrap=True, width=width)


def print_text(text: str = "", stderr: bool = False) -> None:
    """普通文本输出（与表格走同一目标流）。"""
    print(text, file=sys.stderr if stderr else sys.stdout)


def metrics_table(
    named_metrics: dict[str, dict],
    title: str = "回测绩效报告",
    stderr: bool = False,
) -> None:
    """多列并排的绩效对比表。

    Args:
        named_metrics: {列名: 指标字典}，如 {"策略": m1, "基准": m2}。
        title: 表标题。
        stderr: True 时输出到 stderr（JSON 模式）。
    """
    if not _HAS_RICH:
        from backtest.metrics import format_report

        for name, m in named_metrics.items():
            print_text(format_report(m, title=f"{title} - {name}"), stderr=stderr)
            print_text(stderr=stderr)
        return

    table = Table(title=title, title_justify="left", header_style="bold cyan")
    table.add_column("指标")
    for name in named_metrics:
        table.add_column(name, justify="right")
    for key, label, kind in _METRIC_ROWS:
        cells = []
        for m in named_metrics.values():
            cells.append(_fmt(m.get(key), kind) if key in m else "-")
        table.add_row(label, *cells)
    _console(stderr).print(table)


def frame_table(
    df: pd.DataFrame,
    title: str = "",
    pct_cols: tuple[str, ...] = (),
    stderr: bool = False,
) -> None:
    """将 DataFrame 渲染为终端表格（寻优结果/走步折等）。

    Args:
        df: 待展示的结果表。
        title: 表标题。
        pct_cols: 需要按百分比展示的列名。
        stderr: True 时输出到 stderr。
    """
    show = df.copy()
    for col in pct_cols:
        if col in show.columns:
            show[col] = show[col].map(lambda v: f"{v * 100:+.2f}%")
    for col in show.columns:
        if show[col].dtype.kind == "f":
            show[col] = show[col].map(lambda v: f"{v:.2f}")

    if not _HAS_RICH:
        if title:
            print_text(f"===== {title} =====", stderr=stderr)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 160)
        print_text(show.to_string(index=False), stderr=stderr)
        return

    table = Table(title=title or None, title_justify="left", header_style="bold cyan")
    for col in show.columns:
        table.add_column(str(col), justify="right")
    for _, row in show.iterrows():
        table.add_row(*(str(v) for v in row))
    _console(stderr).print(table)


class ProgressBar:
    """进度条封装：``with ProgressBar(total, "寻优") as bar: bar.update(done)``。

    rich 缺失或非交互终端时静默（不打印刷屏日志）。
    """

    def __init__(self, total: int, description: str = "", stderr: bool = True):
        self.total = total
        self.description = description
        self.stderr = stderr
        self._progress = None
        self._task = None

    def __enter__(self) -> "ProgressBar":
        target = sys.stderr if self.stderr else sys.stdout
        if _HAS_RICH and target.isatty():
            self._progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=Console(file=target),
                transient=True,
            )
            self._progress.start()
            self._task = self._progress.add_task(self.description, total=self.total)
        return self

    def update(self, done: int, total: int | None = None) -> None:
        if self._progress is not None:
            if total is not None:
                self._progress.update(self._task, completed=done, total=total)
            else:
                self._progress.update(self._task, completed=done)

    def __exit__(self, *exc) -> None:
        if self._progress is not None:
            self._progress.stop()
