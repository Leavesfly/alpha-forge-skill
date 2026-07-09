"""配对交易可视化：价差 z-score + 开平仓阈值/标记，以及配对组合净值。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_pairs(
    z: pd.Series,
    position: pd.Series,
    equity: pd.Series,
    title: str = "配对交易",
    entry: float = 2.0,
    exit: float = 0.5,
    stop: float = 3.5,
    output: str = "../outputs/pairs.png",
) -> str:
    """绘制价差 z-score（含阈值与开平仓点）与组合净值，保存为图片。"""
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    fig.suptitle(title, fontsize=14, fontweight="bold")

    _plot_zscore(axes[0], z, position, entry, exit, stop)
    _plot_equity(axes[1], equity)

    for ax in axes:
        if hasattr(z.index, "to_pydatetime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_zscore(ax, z, position, entry, exit, stop) -> None:
    ax.plot(z.index, z.values, color="#2c3e50", linewidth=1.0, label="价差 z-score")
    for level, color, style in [
        (entry, "#c0392b", "--"), (-entry, "#c0392b", "--"),
        (exit, "#27ae60", ":"), (-exit, "#27ae60", ":"),
        (stop, "#8e44ad", "-."), (-stop, "#8e44ad", "-."),
    ]:
        ax.axhline(level, color=color, linestyle=style, linewidth=0.8)
    ax.axhline(0, color="#7f8c8d", linewidth=0.6)

    # 开平仓点：持仓变动处
    change = position.diff().fillna(position)
    opens = position[(change != 0) & (position != 0)]
    closes = position[(change != 0) & (position == 0)]
    ax.scatter(opens.index, z.reindex(opens.index), marker="^", color="#e67e22",
               s=40, zorder=5, label="开仓")
    ax.scatter(closes.index, z.reindex(closes.index), marker="v", color="#2980b9",
               s=40, zorder=5, label="平仓")
    ax.set_ylabel("z-score")
    ax.set_title("价差 z-score 与开平仓（红=开仓阈值 绿=平仓 紫=止损）")
    ax.legend(loc="best", ncol=3, fontsize=8)
    ax.grid(True, alpha=0.3)


def _plot_equity(ax, equity) -> None:
    ax.plot(equity.index, equity.values, color="#c0392b", label="配对组合净值")
    ax.axhline(1.0, color="#7f8c8d", linewidth=0.6)
    ax.set_ylabel("净值")
    ax.set_title("市场中性配对组合净值")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
