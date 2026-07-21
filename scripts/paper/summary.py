"""模拟盘组合级聚合：扫描全部状态文件，输出账户级视图与风控提示。"""

from __future__ import annotations

import json
import sys

from cli_common import build_next_steps, emit_json, init_log
from datafeed import fetch_ohlcv
from naming import outputs_dir
from report import attach_meta, frame_table

DISCLAIMER = "模拟盘为纸面交易，仅供研究参考，不构成投资建议。"


def run_summary(args) -> None:
    """组合级聚合：扫描 outputs/paper_*.json，拉最新价聚合为账户视图。

    风控提示：单标的市值权重 > 40% 视为集中度偏高；行情拉取失败的
    模拟盘用最后一笔成交价估值并标注。
    """
    json_stdout, log = init_log(args)
    out_dir = outputs_dir()
    paths = sorted(out_dir.glob("paper_*.json"))
    states = []
    for p in paths:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(s, dict) and {"symbol", "cash", "shares"} <= s.keys():
            s["_path"] = p
            states.append(s)
    if not states:
        raise SystemExit(
            "[error] 未找到任何模拟盘状态文件（outputs/paper_*.json）；"
            "先用 run_paper.py --symbol <代码> --mode score 开启一个模拟盘。"
        )

    log(f"发现 {len(states)} 个模拟盘，拉取最新价估值...")
    rows = []
    price_cache: dict[str, float | None] = {}
    for s in states:
        sym = s["symbol"]
        if sym not in price_cache:
            try:
                px_df = fetch_ohlcv(sym, period="1d", count=5)
                price_cache[sym] = float(px_df["close"].iloc[-1])
            except Exception as exc:
                print(f"[warn] {sym} 行情拉取失败（{type(exc).__name__}），用最后成交价估值", file=sys.stderr)
                price_cache[sym] = None
        price = price_cache[sym]
        stale = price is None
        if stale:
            price = float(s["trades"][-1]["price"]) if s.get("trades") else 0.0
        mv = s["shares"] * price
        equity = s["cash"] + mv
        rows.append({
            "symbol": sym,
            "strategy": s.get("strategy", "?"),
            "shares": int(s["shares"]),
            "price": price,
            "market_value": mv,
            "cash": float(s["cash"]),
            "equity": equity,
            "nav": equity / s["initial_capital"] if s.get("initial_capital") else None,
            "initial_capital": float(s.get("initial_capital", 0.0)),
            "last_date": s.get("last_date"),
            "stale_price": stale,
        })

    total_initial = sum(r["initial_capital"] for r in rows)
    total_equity = sum(r["equity"] for r in rows)
    total_mv = sum(r["market_value"] for r in rows)
    total_nav = total_equity / total_initial if total_initial > 0 else None
    cash_ratio = 1.0 - total_mv / total_equity if total_equity > 0 else 1.0

    # 集中度：同一标的多个模拟盘合并市值后算权重
    by_symbol: dict[str, float] = {}
    for r in rows:
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0.0) + r["market_value"]
    warnings = []
    if total_equity > 0:
        for sym, mv in sorted(by_symbol.items(), key=lambda kv: -kv[1]):
            w = mv / total_equity
            if w > 0.40:
                warnings.append(f"{sym} 市值占总净值 {w:.0%}，集中度偏高（>40%）")
    losers = [r for r in rows if r["nav"] is not None and r["nav"] < 0.90]
    for r in losers:
        warnings.append(
            f"{r['symbol']}({r['strategy']}) 净值 {r['nav']:.3f} 已计入账面回撤 >10%，建议复核"
        )

    import pandas as pd

    table = pd.DataFrame(rows)
    frame_table(
        table[["symbol", "strategy", "shares", "price", "market_value", "cash", "nav", "last_date"]],
        title=f"模拟盘组合总览（{len(rows)} 个）",
        stderr=json_stdout,
    )
    log("")
    log("===== 账户级汇总 =====")
    log(f"总初始资金  : {total_initial:,.0f}")
    log(f"总净值      : {total_equity:,.2f}" + (f"（{total_nav:.4f}）" if total_nav else ""))
    log(f"持仓市值    : {total_mv:,.2f}（现金占比 {cash_ratio:.0%}）")
    if warnings:
        log("")
        log("⚠️  风控提示：")
        for w in warnings:
            log(f"  - {w}")
    else:
        log("风控检查    : 无集中度/回撤告警")
    log(f"\n{DISCLAIMER}")

    if args.json is not None:
        nav_str = f"，合并净值 {total_nav:.4f}" if total_nav else ""
        summary = (
            f"共 {len(rows)} 个模拟盘：总净值 {total_equity:,.0f}{nav_str}，"
            f"现金占比 {cash_ratio:.0%}。"
            + (f"风控提示 {len(warnings)} 条。" if warnings else "无风控告警。")
            + "仅纸面跟踪，不自动下单。"
        )
        payload = attach_meta(
            {
                "papers": [
                    {k: v for k, v in r.items()}
                    for r in rows
                ],
                "totals": {
                    "count": len(rows),
                    "initial_capital": float(total_initial),
                    "equity": float(total_equity),
                    "market_value": float(total_mv),
                    "nav": float(total_nav) if total_nav else None,
                    "cash_ratio": float(cash_ratio),
                },
                "symbol_weights": {
                    sym: (mv / total_equity if total_equity > 0 else 0.0)
                    for sym, mv in by_symbol.items()
                },
                "risk_warnings": warnings,
                "disclaimer": DISCLAIMER,
                "summary": summary,
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "对告警标的复核评分结论",
                     "command": "run_score.py --symbol <告警标的> --json"},
                    {"action": "account", "reason": "对照真实持仓账户",
                     "command": "run_account.py --json"},
                ),
            },
            command="paper",
        )
        emit_json(args.json, payload, log)
