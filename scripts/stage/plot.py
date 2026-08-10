"""阶段定位可视化。

``plot_stage``：收盘价 + MA20/MA60 + 箱体上下沿（含突破/破位价虚线），
提供阶段历史时按阶段给背景着色，直观呈现「台阶式循环」的演进。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# 中文字体，避免图中中文乱码
plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

#: 阶段 -> 背景色（与「台阶循环」语义对应：绿=进攻，红=退守，蓝=等待）
_STAGE_COLORS = {
    "base": "#2980b9",
    "breakout": "#27ae60",
    "advance": "#16a085",
    "top": "#f1c40f",
    "breakdown": "#c0392b",
    "decline": "#e67e22",
    "unknown": "#95a5a6",
}


def plot_stage(
    close: pd.Series,
    result,
    history: dict | None = None,
    title: str = "",
    output: str = "../outputs/stage.png",
    tail: int = 250,
) -> str:
    """绘制阶段定位图并保存，返回图片路径。

    Args:
        close: 收盘价序列（带时间索引）。
        result: :class:`~stage.engine.StageResult`（取箱体与关键价位）。
        history: :func:`~stage.engine.stage_history` 返回值，提供时背景按阶段着色。
        title: 图标题。
        output: 输出路径。
        tail: 只画最近 N 根，保持图面可读。
    """
    full = close
    close = close.iloc[-tail:]
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle(title or "阶段定位", fontsize=14, fontweight="bold")

    if history:
        _shade_stages(ax, close, history)

    ax.plot(close.index, close.values, color="#2c3e50", linewidth=1.4, label="收盘价")
    for window, color in ((20, "#e67e22"), (60, "#8e44ad")):
        ma = full.rolling(window).mean().iloc[-tail:]
        ax.plot(ma.index, ma.values, color=color, linewidth=1.0, label=f"MA{window}")

    _draw_box(ax, result)

    ax.set_ylabel("价格")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9)
    fig.autofmt_xdate()

    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(path), dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _draw_box(ax, result) -> None:
    """画箱体上下沿（实线）与突破/破位价（虚线）。"""
    box = result.box or {}
    if not box.get("high") or not box.get("low"):
        return
    style = "-" if box.get("valid") else ":"
    ax.axhline(box["high"], color="#c0392b", linestyle=style, linewidth=1.0,
               label=f"箱体上沿 {box['high']:.2f}")
    ax.axhline(box["low"], color="#27ae60", linestyle=style, linewidth=1.0,
               label=f"箱体下沿 {box['low']:.2f}")
    trigger = result.trigger or {}
    if trigger.get("breakout_price"):
        ax.axhline(trigger["breakout_price"], color="#c0392b", linestyle="--",
                   linewidth=0.8, alpha=0.7)
    if trigger.get("breakdown_price"):
        ax.axhline(trigger["breakdown_price"], color="#27ae60", linestyle="--",
                   linewidth=0.8, alpha=0.7)


def _shade_stages(ax, close: pd.Series, history: dict) -> None:
    """按阶段历史给背景着色（同一阶段的连续区间合并为一块）。"""
    series = history.get("series") or []
    if not series:
        return
    # 阶段序列按日期对齐到绘图区间
    stages = {item["date"]: item["stage"] for item in series}
    dates = [d.strftime("%Y-%m-%d") if isinstance(d, pd.Timestamp) else str(d)
             for d in close.index]
    labeled: set[str] = set()
    start = 0
    while start < len(dates):
        stage = stages.get(dates[start])
        end = start
        while end + 1 < len(dates) and stages.get(dates[end + 1]) == stage:
            end += 1
        if stage:
            label = None
            if stage not in labeled:
                from .engine import STAGE_CN

                label = STAGE_CN[stage]
                labeled.add(stage)
            ax.axvspan(close.index[start], close.index[end],
                       color=_STAGE_COLORS.get(stage, "#95a5a6"),
                       alpha=0.12, label=label)
        start = end + 1
