"""多因子选股可视化：Top 组合净值 vs 等权基准、分层净值对比、分层末期收益柱状。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .model import FactorResult  # noqa: E402

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_factor(
    result: FactorResult,
    title: str = "多因子选股",
    output: str = "../outputs/factor.png",
) -> str:
    """绘制多因子回测结果并保存为图片，返回图片路径。"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 13))
    fig.suptitle(title, fontsize=14, fontweight="bold")

    _plot_top_vs_benchmark(axes[0], result)
    _plot_layers(axes[1], result)
    _plot_layer_returns(axes[2], result)

    for ax in axes[:2]:
        if hasattr(result.top_portfolio.equity.index, "to_pydatetime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_top_vs_benchmark(ax, result: FactorResult) -> None:
    top = result.top_portfolio
    ax.plot(top.equity.index, top.equity.values, label="Top 组合", color="#c0392b")
    ax.plot(
        top.benchmark_equity.index,
        top.benchmark_equity.values,
        label="等权基准",
        color="#7f8c8d",
        linestyle="--",
    )
    ax.set_ylabel("净值")
    ax.set_title(f"Top {result.top_quantile:.0%} 组合 vs 等权基准")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_layers(ax, result: FactorResult) -> None:
    cmap = plt.get_cmap("RdYlGn_r")
    n = len(result.layers)
    for idx, layer in enumerate(result.layers):
        color = cmap(idx / max(n - 1, 1))
        ax.plot(layer.equity.index, layer.equity.values, label=f"L{idx + 1}", color=color)
    ax.set_ylabel("净值")
    ax.set_title("分层净值（L1=最高分层，理想应单调递减）")
    ax.legend(loc="best", ncol=n)
    ax.grid(True, alpha=0.3)


def _plot_layer_returns(ax, result: FactorResult) -> None:
    labels = [f"L{i + 1}" for i in range(len(result.layers))]
    returns = [(layer.equity.iloc[-1] - 1.0) * 100 for layer in result.layers]
    colors = plt.get_cmap("RdYlGn_r")(
        [i / max(len(labels) - 1, 1) for i in range(len(labels))]
    )
    ax.bar(labels, returns, color=colors)
    ax.axhline(0, color="#333", linewidth=0.8)
    ax.set_ylabel("累计收益 (%)")
    ax.set_title("各分层累计收益（单调性检验）")
    ax.grid(True, axis="y", alpha=0.3)
