"""定投回测可视化。

绘制市值 vs 累计投入本金、成本调整净值（市值/投入）与回撤、价格与买入点，
保存为 PNG。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境后端
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .engine import DCAResult  # noqa: E402

# 中文字体，避免图中中文乱码
plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_dca(
    result: DCAResult,
    title: str = "",
    output: str = "../outputs/dca.png",
) -> str:
    """绘制定投结果并保存为图片，返回图片路径。"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)
    head = title or f"{result.symbol} 定投回测".strip()
    fig.suptitle(head, fontsize=14, fontweight="bold")

    _plot_value_vs_invested(axes[0], result)
    _plot_nav_drawdown(axes[1], result)
    _plot_price_trades(axes[2], result)

    for ax in axes:
        if hasattr(result.close.index, "to_pydatetime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(out_path)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _plot_value_vs_invested(ax, result: DCAResult) -> None:
    ax.plot(
        result.market_value.index,
        result.market_value.values,
        label="持仓市值",
        color="#c0392b",
    )
    ax.plot(
        result.invested.index,
        result.invested.values,
        label="累计投入本金",
        color="#7f8c8d",
        linestyle="--",
    )
    ax.fill_between(
        result.market_value.index,
        result.market_value.values,
        result.invested.values,
        where=(result.market_value.values >= result.invested.values),
        color="#27ae60",
        alpha=0.25,
    )
    ax.fill_between(
        result.market_value.index,
        result.market_value.values,
        result.invested.values,
        where=(result.market_value.values < result.invested.values),
        color="#c0392b",
        alpha=0.25,
    )
    ax.set_ylabel("金额")
    ax.set_title("持仓市值 vs 累计投入")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_nav_drawdown(ax, result: DCAResult) -> None:
    started = result.invested > 0
    mv = result.market_value[started]
    inv = result.invested[started]
    nav = (mv / inv).replace([float("inf"), float("-inf")], float("nan")).dropna()
    ax.plot(nav.index, nav.values, label="成本调整净值(市值/投入)", color="#8e44ad")
    ax.axhline(1.0, color="#95a5a6", linestyle=":", linewidth=1)
    ax.set_ylabel("净值")
    ax.set_title("成本调整净值（>1 即累计盈利）")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_price_trades(ax, result: DCAResult) -> None:
    close = result.close
    ax.plot(close.index, close.values, label="收盘价", color="#34495e", linewidth=1)

    trades = result.transactions
    if not trades.empty:
        amt = trades["amount"].abs()
        base = amt[amt > 0].min() if (amt > 0).any() else 1.0
        buys = trades[trades["action"] == "BUY"]
        sells = trades[trades["action"] == "SELL"]
        if not buys.empty:
            ax.scatter(
                buys["time"], buys["price"], marker="^", color="#e67e22",
                s=30.0 * (buys["amount"].abs() / base), alpha=0.8, label="定投买入",
            )
        if not sells.empty:
            ax.scatter(
                sells["time"], sells["price"], marker="v", color="#27ae60",
                s=30.0 * (sells["amount"].abs() / base), alpha=0.8, label="减仓卖出",
            )
    ax.set_ylabel("价格")
    ax.set_title("价格与定投交易点（点越大金额越多）")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
