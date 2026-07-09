"""机器学习策略可视化：OOS 净值 vs 基准、特征重要度、训练/测试分界标注。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .model import MLResult  # noqa: E402

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_ml(
    result: MLResult,
    title: str = "机器学习策略",
    output: str = "../outputs/ml.png",
    top_features: int = 15,
) -> str:
    """绘制机器学习策略结果并保存为图片，返回图片路径。"""
    fig, axes = plt.subplots(2, 1, figsize=(12, 10))
    fig.suptitle(title, fontsize=14, fontweight="bold")

    _plot_equity(axes[0], result)
    _plot_importance(axes[1], result, top_features)

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_equity(ax, result: MLResult) -> None:
    bt = result.backtest
    ax.plot(bt.equity.index, bt.equity.values, label="策略净值(含 OOS)", color="#c0392b")
    ax.plot(
        bt.benchmark_equity.index,
        bt.benchmark_equity.values,
        label="基准(Buy&Hold)",
        color="#7f8c8d",
        linestyle="--",
    )
    # 训练/测试（样本外）分界
    if result.oos_start_label is not None:
        ax.axvline(result.oos_start_label, color="#2980b9", linestyle=":", linewidth=1.5)
        ax.text(
            result.oos_start_label, ax.get_ylim()[1], " OOS 起点",
            color="#2980b9", va="top", fontsize=9,
        )
    ax.set_ylabel("净值")
    ax.set_title("净值曲线（分界线右侧为样本外，指标应以 OOS 段为准）")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    if hasattr(bt.equity.index, "to_pydatetime"):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))


def _plot_importance(ax, result: MLResult, top_features: int) -> None:
    imp = result.feature_importance.head(top_features).iloc[::-1]
    ax.barh(imp.index, imp.values, color="#16a085")
    ax.set_xlabel("平均重要度（走步各模型均值）")
    ax.set_title(f"特征重要度 Top {min(top_features, len(imp))}")
    ax.grid(True, axis="x", alpha=0.3)
