"""自包含 HTML 研究报告（tear sheet）。

把回测结果渲染成单文件 HTML：净值/回撤/买卖点图（base64 内嵌）、
策略 vs 基准指标对照表、月度收益表、交易明细。无需外部 CSS/JS/图床，
便于 agent 直接交付给用户或归档。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
import pandas as pd

_METRIC_LABELS = [
    ("total_return", "累计收益率", "pct"),
    ("annual_return", "年化收益率", "pct"),
    ("annual_volatility", "年化波动率", "pct"),
    ("sharpe", "夏普比率", "num"),
    ("sortino", "索提诺比率", "num"),
    ("omega", "欧米茄比率", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("max_dd_duration", "最长回撤期", "int"),
    ("calmar", "卡玛比率", "num"),
    ("win_rate", "胜率", "pct"),
    ("profit_factor", "盈亏比", "num"),
    ("num_trades", "交易次数", "int"),
]

#: 指标解释文字（报告中随指标行展示，帮助非专业读者理解）
_METRIC_EXPLAIN = {
    "total_return": "区间总收益，需与基准对比才有意义",
    "annual_return": "折算为年的复利收益率",
    "annual_volatility": "收益波动的年化标准差，越小越稳",
    "sharpe": "每承担一单位波动换来的超额收益，>1 尚可，>2 优秀，>3 先怀疑过拟合",
    "sortino": "只惩罚下行波动的夏普变体，对上涨波动友好",
    "omega": "收益总和/亏损总和，>1 赚多亏少，对分布形状不敏感",
    "max_drawdown": "净值从最高点到最低点的最大跌幅，代表最坏情况能亏多少",
    "max_dd_duration": "净值低于前高的最长连续周期数，衡量套牛时长",
    "calmar": "年化收益/最大回撤，越高越好，看重回撤控制",
    "win_rate": "盈利交易占比；需与盈亏比一起看，高胜率低盈亏比未必赚钱",
    "profit_factor": "毛利/毛损，>1 才有正期望，>1.5 较健康",
    "num_trades": "交易越频繁成本越高，样本越多统计越可靠",
}


def _fmt(value: float, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:+.2f}%"
    if kind == "int":
        return f"{int(value)}"
    return f"{value:.2f}"


def _chart_base64(result) -> str:
    """把净值/回撤/价格买卖点渲染为 PNG 并返回 base64 data URI。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        "Arial Unicode MS", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    eq, be = result.equity, result.benchmark_equity
    axes[0].plot(eq.index, eq.values, label="策略净值", color="#c0392b")
    axes[0].plot(be.index, be.values, label="基准(Buy&Hold)", color="#7f8c8d", linestyle="--")
    axes[0].set_title("净值曲线")
    axes[0].legend(loc="best")
    axes[0].grid(True, alpha=0.3)

    dd = eq / eq.cummax() - 1.0
    axes[1].fill_between(dd.index, dd.values * 100, 0, color="#2980b9", alpha=0.4)
    axes[1].set_title("策略回撤 (%)")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


def _rolling_chart_base64(result, window: int = 60) -> str:
    """滚动夏普（60 期窗口）+ 收益分布直方图的双联图 base64 data URI。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        "Arial Unicode MS", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    from backtest.metrics import periods_per_year

    ret = result.returns.dropna()
    ann = periods_per_year(getattr(result, "period", "1d"))

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    # 左：滚动夏普（窗口内均值/标准差年化）
    if len(ret) > window:
        roll_mean = ret.rolling(window).mean()
        roll_std = ret.rolling(window).std(ddof=0)
        roll_sharpe = (roll_mean / roll_std * np.sqrt(ann)).replace(
            [np.inf, -np.inf], np.nan
        )
        axes[0].plot(roll_sharpe.index, roll_sharpe.values, color="#8e44ad", linewidth=1.2)
        axes[0].axhline(0, color="#7f8c8d", linewidth=0.8, linestyle="--")
    else:
        axes[0].text(0.5, 0.5, "样本不足", ha="center", va="center", color="#7f8c8d")
    axes[0].set_title(f"滚动夏普（{window} 期窗口）")
    axes[0].grid(True, alpha=0.3)

    # 右：收益分布直方图（肥尾直观可见）
    axes[1].hist(ret.values * 100, bins=50, color="#2980b9", alpha=0.75)
    axes[1].axvline(0, color="#7f8c8d", linewidth=0.8, linestyle="--")
    axes[1].set_title("逐周期收益分布 (%)")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


def _metrics_table_html(metrics: dict, benchmark: dict) -> str:
    rows = []
    for key, label, kind in _METRIC_LABELS:
        # 旧版结果缺新增指标键时显示 '-'，保证向后兼容
        s = _fmt(metrics.get(key), kind) if key in metrics else "-"
        b = _fmt(benchmark.get(key), kind) if key in benchmark else "-"
        note = _METRIC_EXPLAIN.get(key, "")
        rows.append(
            f"<tr><td>{label}</td><td class='num'>{s}</td>"
            f"<td class='num muted'>{b}</td><td class='note'>{note}</td></tr>"
        )
    return "\n".join(rows)


def _heat_color(value: float, scale: float = 0.08) -> str:
    """收益幅度 -> 热力图背景色（正红负绿，A 股习惯），幅度越大颜色越深。"""
    ratio = max(-1.0, min(1.0, value / scale))
    if ratio >= 0:
        # 白 -> 红
        g = b = int(255 - 90 * ratio)
        return f"rgb(255,{g},{b})"
    # 白 -> 绿
    r = int(255 - 90 * -ratio)
    b = int(255 - 60 * -ratio)
    return f"rgb({r},255,{b})"


def _monthly_table_html(returns: pd.Series) -> str:
    """按 年×月 展示月度收益率热力图（背景色按幅度渐变）。"""
    if not isinstance(returns.index, pd.DatetimeIndex):
        idx = pd.to_datetime(returns.index, errors="coerce")
        if idx.isna().all():
            return "<p class='muted'>（无时间索引，跳过月度表）</p>"
        returns = returns.copy()
        returns.index = idx
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    if monthly.empty:
        return "<p class='muted'>（数据不足）</p>"
    tbl = monthly.to_frame("r")
    tbl["year"] = tbl.index.year
    tbl["month"] = tbl.index.month
    pivot = tbl.pivot_table(index="year", columns="month", values="r")
    header = "".join(f"<th>{m}月</th>" for m in range(1, 13))
    body = []
    for year, row in pivot.iterrows():
        cells = []
        for m in range(1, 13):
            v = row.get(m, np.nan)
            if pd.isna(v):
                cells.append("<td class='muted'>-</td>")
            else:
                color = "#c0392b" if v >= 0 else "#27ae60"
                cells.append(
                    f"<td class='num' style='color:{color};"
                    f"background:{_heat_color(v)}'>{v * 100:+.1f}</td>"
                )
        body.append(f"<tr><td>{year}</td>{''.join(cells)}</tr>")
    return (
        f"<table class='grid'><thead><tr><th>年份</th>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _trades_table_html(result, limit: int = 50) -> str:
    trades = result.trades.head(limit)
    if trades.empty:
        return "<p class='muted'>（无交易）</p>"
    rows = [
        f"<tr><td>{str(r['time'])[:10]}</td><td>{r['action']}</td>"
        f"<td class='num'>{r['price']:.3f}</td></tr>"
        for _, r in trades.iterrows()
    ]
    return (
        "<table class='grid'><thead><tr><th>时间</th><th>方向</th><th>价格</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _config_html(config: dict) -> str:
    if not config:
        return ""
    items = "".join(f"<li><b>{k}</b>：{v}</li>" for k, v in config.items())
    return f"<ul class='config'>{items}</ul>"


def _stress_html(stress: dict | None) -> str:
    """压力测试区块：历史情景表 + 蒙特卡洛回撤分位表。"""
    if not stress:
        return ""
    parts = ["<h2>压力测试</h2>"]
    scen = stress.get("scenarios")
    if scen is not None and len(scen):
        rows = "".join(
            f"<tr><td>{r['情景']}</td><td>{r['区间']}</td>"
            f"<td class='num'>{r['期间收益'] * 100:+.2f}%</td>"
            f"<td class='num'>{r['最大回撤'] * 100:.2f}%</td>"
            f"<td class='num'>{'-' if pd.isna(r['恢复天数']) else int(r['恢复天数'])}</td></tr>"
            for _, r in scen.iterrows()
        )
        parts.append(
            "<h3>历史情景重放</h3>"
            "<table><thead><tr><th>情景</th><th>区间</th><th class='num'>期间收益</th>"
            "<th class='num'>最大回撤</th><th class='num'>恢复天数</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        parts.append("<p class='muted'>（回测区间未覆盖预置历史情景）</p>")
    mc = stress.get("monte_carlo")
    if mc is not None and len(mc):
        rows = "".join(
            f"<tr><td>{r['情景']}</td>"
            f"<td class='num'>{r['回撤p50'] * 100:.2f}%</td>"
            f"<td class='num'>{r['回撤p95'] * 100:.2f}%</td>"
            f"<td class='num'>{r['回撤p99'] * 100:.2f}%</td></tr>"
            for _, r in mc.iterrows()
        )
        parts.append(
            "<h3>蒙特卡洛冲击（最大回撤分位）</h3>"
            "<table><thead><tr><th>情景</th><th class='num'>p50</th>"
            "<th class='num'>p95</th><th class='num'>p99</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return "".join(parts)


def _yearly_chart_base64(result) -> str:
    """年度收益对比条形图（策略 vs 基准）；无法按年聚合时返回空串。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        "Arial Unicode MS", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    def _yearly(ret: pd.Series) -> pd.Series | None:
        if not isinstance(ret.index, pd.DatetimeIndex):
            idx = pd.to_datetime(ret.index, errors="coerce")
            if pd.isna(idx).all():
                return None
            ret = pd.Series(ret.to_numpy(), index=idx)
        y = (1.0 + ret.dropna()).resample("YE").prod() - 1.0
        return y if len(y) else None

    strat = _yearly(result.returns)
    bench_eq = getattr(result, "benchmark_equity", None)
    bench = _yearly(bench_eq.pct_change()) if bench_eq is not None else None
    if strat is None:
        return ""

    years = [str(t.year) for t in strat.index]
    x = np.arange(len(years))
    width = 0.38
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.bar(x - width / 2, strat.values * 100, width, label="策略", color="#c0392b", alpha=0.85)
    if bench is not None and len(bench) == len(strat):
        ax.bar(x + width / 2, bench.values * 100, width, label="基准", color="#7f8c8d", alpha=0.7)
    ax.axhline(0, color="#7f8c8d", linewidth=0.8)
    ax.set_xticks(x, years)
    ax.set_title("年度收益对比 (%)")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


def drawdown_episodes(equity: pd.Series, top_n: int = 5) -> list[dict]:
    """提取最深的 top_n 次回撤事件（开始/谷底/恢复/深度/持续周期数）。

    回撤事件 = 净值从前高回落到重新创高之间的完整区间；末尾未恢复的
    事件照常纳入（recover 为 None）。返回按深度降序。
    """
    eq = equity.dropna()
    if len(eq) < 2:
        return []
    cummax = eq.cummax()
    dd = eq / cummax - 1.0
    episodes: list[dict] = []
    in_dd = False
    start = trough = None
    trough_val = 0.0
    for i, (ts, v) in enumerate(dd.items()):
        if not in_dd and v < 0:
            in_dd = True
            start = dd.index[i - 1] if i > 0 else ts
            trough, trough_val = ts, v
        elif in_dd:
            if v < trough_val:
                trough, trough_val = ts, v
            if v >= 0:  # 创新高，事件结束
                episodes.append({
                    "start": start, "trough": trough, "recover": ts,
                    "depth": trough_val,
                    "length": int(dd.index.get_loc(ts) - dd.index.get_loc(start)),
                })
                in_dd = False
    if in_dd:  # 末尾未恢复
        episodes.append({
            "start": start, "trough": trough, "recover": None,
            "depth": trough_val,
            "length": int(len(dd) - 1 - dd.index.get_loc(start)),
        })
    episodes.sort(key=lambda e: e["depth"])
    return episodes[:top_n]


def _drawdown_table_html(equity: pd.Series, top_n: int = 5) -> str:
    """Top N 最深回撤区间表（QuantStats 风格 worst drawdowns）。"""
    eps = drawdown_episodes(equity, top_n)
    if not eps:
        return "<p class='muted'>（无回撤事件）</p>"
    rows = []
    for e in eps:
        recover = str(e["recover"])[:10] if e["recover"] is not None else "未恢复"
        rows.append(
            f"<tr><td>{str(e['start'])[:10]}</td><td>{str(e['trough'])[:10]}</td>"
            f"<td>{recover}</td><td class='num'>{e['depth'] * 100:.2f}%</td>"
            f"<td class='num'>{e['length']}</td></tr>"
        )
    return (
        "<table class='grid'><thead><tr><th>开始</th><th>谷底</th>"
        "<th>恢复</th><th class='num'>深度</th><th class='num'>持续周期</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _assumptions_html(config: dict, period_range: str) -> str:
    """固定的「假设与局限」声明区块。"""
    cfg = "".join(f"<li><b>{k}</b>：{v}</li>" for k, v in (config or {}).items())
    return (
        "<h2>假设与局限</h2><ul class='config'>"
        f"<li><b>样本区间</b>：{period_range}（历史窗口外的行情未经检验）</li>"
        f"{cfg}"
        "<li>信号按 shift(1) 次日生效；成交假设见上方配置，未建模盘口冲击与容量限制</li>"
        "<li>分红除权现金流未显式建模，依赖复权价格近似</li>"
        "<li>参数若经寻优得出，存在多重检验偏差，应参考 DSR/PBO 与样本外验证</li>"
        "</ul>"
    )


def render_backtest_report(
    result,
    strategy_name: str = "",
    config: dict | None = None,
    output: str = "../outputs/report.html",
    stress: dict | None = None,
) -> str:
    """生成自包含 HTML 报告并保存，返回文件路径。

    Args:
        stress: 可选压力测试结果（{"scenarios": DataFrame, "monte_carlo": DataFrame}）。
    """
    idx = result.equity.index
    period_range = (
        f"{str(idx[0])[:10]} ~ {str(idx[-1])[:10]}" if len(idx) else "-"
    )
    html = _TEMPLATE.format(
        title=f"{result.symbol} {strategy_name} 回测报告",
        symbol=result.symbol,
        strategy=strategy_name,
        period=result.period,
        period_range=period_range,
        config=_config_html(config or {}),
        chart=_chart_base64(result),
        rolling_chart=_rolling_chart_base64(result),
        yearly_chart=_yearly_chart_base64(result),
        metrics=_metrics_table_html(result.metrics, result.benchmark_metrics),
        monthly=_monthly_table_html(result.returns),
        drawdown_table=_drawdown_table_html(result.equity),
        trades=_trades_table_html(result),
        stress=_stress_html(stress),
        assumptions=_assumptions_html(config or {}, period_range),
    )
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


def render_compare_report(
    results: dict[str, object],
    symbol: str = "",
    config: dict | None = None,
    output: str = "../outputs/compare.html",
) -> str:
    """生成多策略对比的自包含 HTML 报告并保存，返回文件路径。

    Args:
        results: {策略显示名: BacktestResult}，至少 1 个；基准取第一个
            结果的 Buy & Hold。
        symbol: 标的代码（展示用）。
        config: 回测配置说明（复权/成本/成交价等）。
        output: 输出路径。
    """
    first = next(iter(results.values()))
    idx = first.equity.index
    period_range = f"{str(idx[0])[:10]} ~ {str(idx[-1])[:10]}" if len(idx) else "-"

    html = _COMPARE_TEMPLATE.format(
        title=f"{symbol} 多策略对比报告",
        symbol=symbol,
        period=first.period,
        period_range=period_range,
        n=len(results),
        config=_config_html(config or {}),
        chart=_compare_chart_base64(results),
        metrics=_compare_metrics_html(results),
        monthly=_compare_monthly_html(results),
    )
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


def _compare_chart_base64(results: dict[str, object]) -> str:
    """多策略净值叠加图（含基准）的 base64 data URI。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        "Arial Unicode MS", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(11, 5.5))
    for name, res in results.items():
        ax.plot(res.equity.index, res.equity.values, label=name, linewidth=1.4)
    first = next(iter(results.values()))
    be = first.benchmark_equity
    ax.plot(be.index, be.values, label="基准(Buy&Hold)", color="#7f8c8d", linestyle="--")
    ax.set_title("多策略净值对比")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


def _compare_metrics_html(results: dict[str, object]) -> str:
    """行=指标、列=策略（末列基准）的对比表。"""
    first = next(iter(results.values()))
    header = "".join(f"<th class='num'>{name}</th>" for name in results)
    header += "<th class='num muted'>基准</th>"
    rows = []
    for key, label, kind in _METRIC_LABELS:
        cells = "".join(
            f"<td class='num'>{_fmt(res.metrics.get(key), kind)}</td>"
            for res in results.values()
        )
        bench = _fmt(first.benchmark_metrics.get(key), kind)
        rows.append(
            f"<tr><td>{label}</td>{cells}<td class='num muted'>{bench}</td></tr>"
        )
    return (
        f"<table><thead><tr><th>指标</th>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _compare_monthly_html(results: dict[str, object]) -> str:
    """各策略月度收益热力图小表并排展示。"""
    parts = []
    for name, res in results.items():
        parts.append(f"<h3>{name}</h3>")
        parts.append(_monthly_table_html(res.returns))
    return "".join(parts)


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    max-width: 960px; margin: 24px auto; padding: 0 16px; color: #2c3e50; }}
  h1 {{ font-size: 20px; border-bottom: 2px solid #c0392b; padding-bottom: 8px; }}
  h2 {{ font-size: 16px; margin-top: 28px; color: #34495e; }}
  .meta {{ color: #7f8c8d; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px; }}
  th, td {{ border: 1px solid #e1e4e8; padding: 6px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.note {{ color: #95a5a6; font-size: 12px; }}
  .muted {{ color: #b2bec3; }}
  img {{ width: 100%; margin-top: 8px; border: 1px solid #e1e4e8; border-radius: 6px; }}
  ul.config {{ font-size: 13px; color: #636e72; }}
  .disclaimer {{ margin-top: 32px; font-size: 12px; color: #b2bec3; }}
</style></head>
<body>
  <h1>{title}</h1>
  <p class="meta">标的 {symbol} · 周期 {period} · 区间 {period_range}</p>
  {config}
  <h2>净值与回撤</h2>
  <img src="{chart}" alt="净值曲线与回撤">
  <h2>滚动风险与收益分布</h2>
  <img src="{rolling_chart}" alt="滚动夏普与收益分布">
  <h2>年度收益对比</h2>
  <img src="{yearly_chart}" alt="年度收益对比">
  <h2>绩效指标（策略 vs 基准）</h2>
  <table><thead><tr><th>指标</th><th class="num">策略</th><th class="num">基准</th><th>说明</th></tr></thead>
  <tbody>{metrics}</tbody></table>
  <h2>月度收益 (%)</h2>
  {monthly}
  <h2>最深回撤 Top 5</h2>
  {drawdown_table}
  <h2>交易明细（前 50 笔）</h2>
  {trades}
  {stress}
  {assumptions}
  <p class="disclaimer">本报告基于历史数据回测，不代表未来收益；请警惕过拟合，
  夏普 &gt; 3 应优先怀疑数据泄露。</p>
</body></html>
"""

_COMPARE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    max-width: 960px; margin: 24px auto; padding: 0 16px; color: #2c3e50; }}
  h1 {{ font-size: 20px; border-bottom: 2px solid #c0392b; padding-bottom: 8px; }}
  h2 {{ font-size: 16px; margin-top: 28px; color: #34495e; }}
  .meta {{ color: #7f8c8d; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px; }}
  th, td {{ border: 1px solid #e1e4e8; padding: 6px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .muted {{ color: #b2bec3; }}
  img {{ width: 100%; margin-top: 8px; border: 1px solid #e1e4e8; border-radius: 6px; }}
  ul.config {{ font-size: 13px; color: #636e72; }}
  .disclaimer {{ margin-top: 32px; font-size: 12px; color: #b2bec3; }}
</style></head>
<body>
  <h1>{title}</h1>
  <p class="meta">标的 {symbol} · 周期 {period} · 区间 {period_range} · 策略数 {n}</p>
  {config}
  <h2>净值对比</h2>
  <img src="{chart}" alt="多策略净值对比">
  <h2>绩效指标对照</h2>
  {metrics}
  <h2>各策略月度收益 (%)</h2>
  {monthly}
  <p class="disclaimer">本报告基于历史数据回测，不代表未来收益；多策略同标的比较
  存在选择性偏差，建议用 run_validate.py 做样本外验证。</p>
</body></html>
"""
