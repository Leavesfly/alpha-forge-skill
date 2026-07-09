"""组合回测可视化。

绘制组合净值 vs 等权基准、回撤、各标的权重堆叠面积图，保存为 PNG。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .engine import PortfolioResult  # noqa: E402

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_portfolio(
    result: PortfolioResult,
    strategy_name: str = "",
    output: str = "../outputs/portfolio.png",
) -> str:
    """绘制组合回测结果并保存为图片，返回图片路径。"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)
    title = f"组合轮动回测 {strategy_name}".strip()
    fig.suptitle(title, fontsize=14, fontweight="bold")

    _plot_equity(axes[0], result)
    _plot_drawdown(axes[1], result)
    _plot_weights(axes[2], result)

    for ax in axes:
        if hasattr(result.equity.index, "to_pydatetime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_equity(ax, result: PortfolioResult) -> None:
    ax.plot(result.equity.index, result.equity.values, label="组合净值", color="#c0392b")
    ax.plot(
        result.benchmark_equity.index,
        result.benchmark_equity.values,
        label="等权基准",
        color="#7f8c8d",
        linestyle="--",
    )
    ax.set_ylabel("净值")
    ax.set_title("组合净值 vs 等权基准")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_drawdown(ax, result: PortfolioResult) -> None:
    equity = result.equity
    drawdown = equity / equity.cummax() - 1.0
    ax.fill_between(drawdown.index, drawdown.values * 100, 0, color="#2980b9", alpha=0.4)
    ax.set_ylabel("回撤 (%)")
    ax.set_title("组合回撤")
    ax.grid(True, alpha=0.3)


def _plot_weights(ax, result: PortfolioResult) -> None:
    weights = result.weights
    ax.stackplot(
        weights.index,
        *[weights[col].values for col in weights.columns],
        labels=list(weights.columns),
        alpha=0.8,
    )
    ax.set_ylabel("权重")
    ax.set_title("各标的权重")
    ax.legend(loc="upper left", ncol=min(len(weights.columns), 5), fontsize=8)
    ax.grid(True, alpha=0.3)
