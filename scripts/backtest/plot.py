"""回测可视化。

绘制净值曲线（策略 vs 基准）、回撤区间、价格叠加买卖点，保存为 PNG。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .engine import BacktestResult  # noqa: E402

# 中文字体，避免图中中文乱码
plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_result(
    result: BacktestResult,
    strategy_name: str = "",
    output: str = "../outputs/backtest.png",
) -> str:
    """绘制回测结果并保存为图片，返回图片路径。"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)
    title = f"{result.symbol} {strategy_name} 回测结果".strip()
    fig.suptitle(title, fontsize=14, fontweight="bold")

    _plot_equity(axes[0], result)
    _plot_drawdown(axes[1], result)
    _plot_price_signals(axes[2], result)

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


def plot_compare(
    results: dict[str, BacktestResult],
    symbol: str = "",
    output: str = "../outputs/compare.png",
) -> str:
    """多策略净值叠加对比图（含基准），保存为图片并返回路径。

    Args:
        results: {策略显示名: BacktestResult}；基准取第一个结果的 Buy & Hold。
        symbol: 标的代码（标题展示）。
        output: 输出路径。
    """
    fig, ax = plt.subplots(figsize=(12, 6))
    for name, res in results.items():
        ax.plot(res.equity.index, res.equity.values, label=name, linewidth=1.4)
    first = next(iter(results.values()))
    be = first.benchmark_equity
    ax.plot(be.index, be.values, label="基准(Buy&Hold)", color="#7f8c8d", linestyle="--")
    ax.set_title(f"{symbol} 多策略净值对比".strip())
    ax.set_ylabel("净值")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)
    if hasattr(first.equity.index, "to_pydatetime"):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    fig.tight_layout()
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_equity(ax, result: BacktestResult) -> None:
    ax.plot(result.equity.index, result.equity.values, label="策略净值", color="#c0392b")
    ax.plot(
        result.benchmark_equity.index,
        result.benchmark_equity.values,
        label="基准(Buy&Hold)",
        color="#7f8c8d",
        linestyle="--",
    )
    ax.set_ylabel("净值")
    ax.set_title("净值曲线")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_drawdown(ax, result: BacktestResult) -> None:
    equity = result.equity
    drawdown = equity / equity.cummax() - 1.0
    ax.fill_between(
        drawdown.index, drawdown.values * 100, 0, color="#2980b9", alpha=0.4
    )
    ax.set_ylabel("回撤 (%)")
    ax.set_title("策略回撤")
    ax.grid(True, alpha=0.3)


def _plot_price_signals(ax, result: BacktestResult) -> None:
    close = result.close
    ax.plot(close.index, close.values, label="收盘价", color="#34495e", linewidth=1)

    trades = result.trades
    if not trades.empty:
        buys = trades[trades["action"] == "BUY"]
        sells = trades[trades["action"] == "SELL"]
        ax.scatter(
            buys["time"], buys["price"], marker="^", color="#e74c3c", s=60, label="买入"
        )
        ax.scatter(
            sells["time"], sells["price"], marker="v", color="#27ae60", s=60, label="卖出"
        )
    ax.set_ylabel("价格")
    ax.set_title("价格与买卖点")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
