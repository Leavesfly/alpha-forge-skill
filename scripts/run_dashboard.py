#!/usr/bin/env python3
"""统一 Dashboard：聚合真实持仓 + 模拟盘组合 + 今日信号于一页 HTML。

作为「每日一页」入口，一条命令纵览全部研究/跟踪状态，无需逐个命令查看。
输出自包含 HTML（无外部依赖），可直接浏览器打开或交付。

示例：
    # 最简：聚合账户 + 全部模拟盘（无需网络）
    uv run python run_dashboard.py

    # 附加今日信号（需拉行情）
    uv run python run_dashboard.py --symbols 600000.SH,600519.SH --strategy ma_cross

    # 自定义输出路径
    uv run python run_dashboard.py --output ../outputs/my_dashboard.html
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from account import load_account
from cli_common import (
    add_json_arg,
    build_next_steps,
    emit_json,
    init_log,
    make_parser,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config
from naming import outputs_dir
from report import attach_meta


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 统一 Dashboard（每日一页）", __doc__)
    parser.add_argument(
        "--symbols", default=None,
        help="附加今日信号的标的（逗号分隔）；不传则跳过信号区块",
    )
    parser.add_argument("--strategy", default="ma_cross", help="信号策略，默认 ma_cross")
    parser.add_argument(
        "--output", default=None,
        help="HTML 输出路径；默认 ../outputs/dashboard.html",
    )
    add_json_arg(parser)
    return parser



def _load_papers() -> list[dict]:
    """扫描全部模拟盘状态文件。"""
    papers = []
    for p in sorted(outputs_dir().glob("paper_*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(s, dict) and {"symbol", "cash", "shares"} <= s.keys():
            papers.append(s)
    return papers


def _fetch_signals(symbols: list[str], strategy_name: str) -> list[dict]:
    """拉取最新信号（复用 run_signal 逻辑）。"""
    from datafeed import fetch_ohlcv
    from run_signal import latest_signal
    from strategies import get_strategy

    rows = []
    strategy = get_strategy(strategy_name)
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym, period="1d", count=500)
            sig = latest_signal(df, strategy)
            rows.append({"symbol": sym, **sig})
        except Exception:
            rows.append({"symbol": sym, "action": "获取失败", "date": "-",
                         "close": None, "current_position": None, "target_position": None})
    return rows


def _render_html(
    account: dict,
    papers: list[dict],
    signals: list[dict] | None,
    strategy_name: str,
) -> str:
    """渲染自包含 Dashboard HTML。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    positions = account.get("positions", {})

    # ── 真实持仓区块
    acct_rows = ""
    if positions:
        for sym, pos in sorted(positions.items()):
            acct_rows += (
                f"<tr><td>{sym}</td><td class='num'>{pos['shares']:g}</td>"
                f"<td class='num'>{pos['cost']:.3f}</td>"
                f"<td>{pos.get('note', '')}</td></tr>"
            )
    else:
        acct_rows = "<tr><td colspan='4' class='muted'>（空仓，用 run_account.py --set 登记）</td></tr>"

    # ── 模拟盘区块
    paper_rows = ""
    total_equity = 0.0
    total_initial = 0.0
    warnings: list[str] = []
    for s in papers:
        equity = s["cash"] + s["shares"] * (
            s["trades"][-1]["price"] if s.get("trades") else 0.0
        )
        initial = s.get("initial_capital", 100000.0)
        nav = equity / initial if initial > 0 else None
        total_equity += equity
        total_initial += initial
        nav_str = f"{nav:.4f}" if nav else "-"
        paper_rows += (
            f"<tr><td>{s['symbol']}</td><td>{s.get('strategy', '?')}</td>"
            f"<td class='num'>{s['shares']}</td><td class='num'>{s['cash']:,.0f}</td>"
            f"<td class='num'>{nav_str}</td><td>{s.get('last_date', '-')}</td></tr>"
        )
        if nav is not None and nav < 0.90:
            warnings.append(f"{s['symbol']}({s.get('strategy','?')}) 净值 {nav:.3f}，回撤>10%")
    if not papers:
        paper_rows = "<tr><td colspan='6' class='muted'>（无模拟盘，用 run_paper.py 开启）</td></tr>"

    # 集中度检查
    by_sym: dict[str, float] = {}
    for s in papers:
        mv = s["shares"] * (s["trades"][-1]["price"] if s.get("trades") else 0.0)
        by_sym[s["symbol"]] = by_sym.get(s["symbol"], 0.0) + mv
    if total_equity > 0:
        for sym, mv in sorted(by_sym.items(), key=lambda kv: -kv[1]):
            w = mv / total_equity
            if w > 0.40:
                warnings.append(f"{sym} 市值占比 {w:.0%}，集中度偏高")

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warn_html = f"<div class='warn'><b>⚠️ 风控提示</b><ul>{items}</ul></div>"

    # ── 信号区块
    signal_html = ""
    if signals:
        sig_rows = ""
        for r in signals:
            tp = f"{r['target_position']:.0%}" if r.get("target_position") is not None else "-"
            sig_rows += (
                f"<tr><td>{r['symbol']}</td><td>{r.get('date', '-')}</td>"
                f"<td class='num'>{r.get('close', '-')}</td>"
                f"<td class='num'>{tp}</td><td>{r['action']}</td></tr>"
            )
        signal_html = f"""
  <h2>今日信号（{strategy_name}）</h2>
  <table><thead><tr><th>标的</th><th>信号日</th><th class="num">收盘价</th>
  <th class="num">目标仓位</th><th>动作</th></tr></thead>
  <tbody>{sig_rows}</tbody></table>"""

    total_nav = total_equity / total_initial if total_initial > 0 else None
    summary_line = (
        f"模拟盘 {len(papers)} 个 · 合并净值 {total_nav:.4f}" if total_nav else
        f"模拟盘 {len(papers)} 个"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alpha Forge Dashboard</title>
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
  .warn {{ background: #fdf2f2; border: 1px solid #e74c3c; border-radius: 6px;
    padding: 12px 16px; margin-top: 16px; font-size: 13px; }}
  .warn ul {{ margin: 6px 0 0; padding-left: 20px; }}
  .disclaimer {{ margin-top: 32px; font-size: 12px; color: #b2bec3; }}
</style></head>
<body>
  <h1>📈 Alpha Forge Dashboard</h1>
  <p class="meta">生成时间 {now} · {summary_line}</p>
  {warn_html}

  <h2>真实持仓账户</h2>
  <table><thead><tr><th>标的</th><th class="num">持股</th><th class="num">成本</th><th>备注</th></tr></thead>
  <tbody>{acct_rows}</tbody></table>

  <h2>模拟盘组合（{len(papers)} 个）</h2>
  <table><thead><tr><th>标的</th><th>策略</th><th class="num">持股</th>
  <th class="num">现金</th><th class="num">净值</th><th>最后执行</th></tr></thead>
  <tbody>{paper_rows}</tbody></table>
  {signal_html}

  <p class="disclaimer">本页面聚合研究/跟踪状态，仅供研究参考，不构成投资建议；
  模拟盘为纸面交易，不自动下单。</p>
</body></html>"""


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    account = load_account()
    papers = _load_papers()

    signals = None
    if args.symbols:
        symbols = split_symbols(args.symbols, min_count=1, what="Dashboard 信号")
        log(f"拉取 {len(symbols)} 只标的最新信号（{args.strategy}）...")
        signals = _fetch_signals(symbols, args.strategy)

    html = _render_html(account, papers, signals, args.strategy)
    output = args.output or str(outputs_dir() / "dashboard.html")
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    log(f"Dashboard 已生成：{out_path}")
    log(f"  真实持仓 {len(account.get('positions', {}))} 只 · 模拟盘 {len(papers)} 个"
        + (f" · 信号 {len(signals)} 只" if signals else ""))

    if args.json is not None:
        n_pos = len(account.get("positions", {}))
        payload = attach_meta(
            {
                "output_file": str(out_path),
                "account_positions": n_pos,
                "paper_count": len(papers),
                "signals": signals,
                "summary": (
                    f"Dashboard 已生成（{out_path.name}）：真实持仓 {n_pos} 只，"
                    f"模拟盘 {len(papers)} 个"
                    + (f"，今日信号 {len(signals)} 只。" if signals else "。")
                    + "仅供研究参考。"
                ),
                "next_steps": build_next_steps(
                    {"action": "paper", "reason": "查看模拟盘组合详情",
                     "command": "run_paper.py --summary --json"},
                    {"action": "signal", "reason": "每日信号巡检",
                     "command": "run_signal.py --symbols <代码> --strategy ma_cross --json"},
                ),
            },
            command="dashboard",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
