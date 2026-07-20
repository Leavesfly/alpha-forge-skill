"""纪律评分可视化。

- ``plot_score``：收盘价 + MA20/60/200 + 交易计划价位水平线；
  提供回放结论时，背景按结论着色（是=绿、观察=黄、否=红）。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
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

#: 结论 -> 背景色（回放着色）
_VERDICT_COLORS = {
    "yes": "#27ae60",
    "watch": "#f1c40f",
    "no": "#c0392b",
    "reduce_risk": "#e67e22",
    "unrated": "#95a5a6",
}


def plot_score(
    close: pd.Series,
    plan: dict | None = None,
    verdicts: pd.Series | None = None,
    title: str = "",
    output: str = "../outputs/score.png",
    tail: int = 250,
) -> str:
    """绘制评分图并保存，返回图片路径。

    Args:
        close: 收盘价序列（带索引）。
        plan: 交易计划价位（可选，画水平线）。
        verdicts: 回放结论序列（可选，背景着色）。
        tail: 只画最近 N 根，保持图面可读。
    """
    full = close
    close = close.iloc[-tail:]
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle(title or "纪律评分", fontsize=14, fontweight="bold")

    ax.plot(close.index, close.values, label="收盘价", color="#34495e", linewidth=1.2)
    for window, color in ((20, "#e67e22"), (60, "#2980b9"), (200, "#8e44ad")):
        # 均线用全量数据计算后再截尾，避免图内均线起点缺口
        ma = full.rolling(window).mean().iloc[-tail:]
        ax.plot(ma.index, ma.values, label=f"MA{window}", color=color, linewidth=0.9, alpha=0.85)

    if plan is not None:
        for key, label, color, style in (
            ("entry", "入场", "#2c3e50", "-"),
            ("stop", "止损", "#c0392b", "--"),
            ("target_2r", "2R 止盈", "#27ae60", ":"),
            ("target_3r", "3R 止盈", "#16a085", ":"),
        ):
            if plan.get(key):
                ax.axhline(plan[key], color=color, linestyle=style, linewidth=1, alpha=0.8)
                ax.annotate(
                    f"{label} {plan[key]:.2f}",
                    xy=(close.index[-1], plan[key]),
                    fontsize=8,
                    color=color,
                    va="bottom",
                )

    if verdicts is not None and len(verdicts):
        v = verdicts.reindex(close.index).ffill() if isinstance(
            verdicts.index, type(close.index)
        ) else verdicts
        _shade_verdicts(ax, v)

    if isinstance(close.index, pd.DatetimeIndex):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.set_ylabel("价格")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


def _shade_verdicts(ax, verdicts: pd.Series) -> None:
    """按结论对背景连续区段着色。"""
    if verdicts.empty:
        return
    idx = verdicts.index
    start = 0
    values = verdicts.to_numpy()
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            color = _VERDICT_COLORS.get(str(values[start]))
            if color:
                ax.axvspan(idx[start], idx[i - 1], color=color, alpha=0.10)
            start = i
