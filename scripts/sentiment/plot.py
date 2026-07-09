"""新闻情绪策略可视化：价格叠加情绪、净值 vs 基准、日度情绪柱状。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .model import SentimentResult  # noqa: E402

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_sentiment(
    result: SentimentResult,
    title: str = "新闻情绪策略",
    output: str = "../outputs/sentiment.png",
) -> str:
    """绘制新闻情绪策略结果并保存为图片，返回图片路径。"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)
    fig.suptitle(title, fontsize=14, fontweight="bold")

    _plot_equity(axes[0], result)
    _plot_price(axes[1], result)
    _plot_sentiment(axes[2], result)

    for ax in axes:
        if hasattr(result.backtest.equity.index, "to_pydatetime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_equity(ax, result: SentimentResult) -> None:
    bt = result.backtest
    ax.plot(bt.equity.index, bt.equity.values, label="情绪策略净值", color="#c0392b")
    ax.plot(
        bt.benchmark_equity.index,
        bt.benchmark_equity.values,
        label="基准(Buy&Hold)",
        color="#7f8c8d",
        linestyle="--",
    )
    ax.set_ylabel("净值")
    ax.set_title("净值曲线")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_price(ax, result: SentimentResult) -> None:
    close = result.backtest.close
    ax.plot(close.index, close.values, label="收盘价", color="#34495e", linewidth=1)
    trades = result.backtest.trades
    if not trades.empty:
        buys = trades[trades["action"] == "BUY"]
        sells = trades[trades["action"] == "SELL"]
        ax.scatter(buys["time"], buys["price"], marker="^", color="#e74c3c", s=60, label="买入")
        ax.scatter(sells["time"], sells["price"], marker="v", color="#27ae60", s=60, label="卖出")
    ax.set_ylabel("价格")
    ax.set_title("价格与买卖点")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_sentiment(ax, result: SentimentResult) -> None:
    s = result.daily_sentiment
    colors = ["#c0392b" if v > 0 else "#27ae60" if v < 0 else "#95a5a6" for v in s.values]
    ax.bar(s.index, s.values, color=colors, width=1.0)
    ax.axhline(result.entry, color="#e67e22", linestyle=":", linewidth=1, label=f"入场阈值 ±{result.entry}")
    ax.axhline(-result.entry, color="#e67e22", linestyle=":", linewidth=1)
    ax.set_ylabel("情绪")
    ax.set_title("日度情绪（平滑后，红=正/绿=负）")
    ax.legend(loc="best")
    ax.grid(True, axis="y", alpha=0.3)
