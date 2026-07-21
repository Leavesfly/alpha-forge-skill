#!/usr/bin/env python3
"""统一持仓账户 CLI：登记 / 查看 / 移除真实持仓（outputs/account.json）。

账户是跨命令共享的持仓注册表（仅登记，不做任何交易执行）：
- run_score.py 未显式传 --cost 时自动读取账户持仓给操作建议；
- run_scan.py 扫描结果标注「已持有」，--exclude-held 可排除已持标的。

示例：
    # 登记/更新一笔持仓（同标的重复 --set 即更新）
    uv run python run_account.py --set --symbol 600000.SH --shares 1000 --cost 8.50

    # 查看账户（默认拉最新收盘价计算浮盈亏；--no-quote 跳过联网）
    uv run python run_account.py
    uv run python run_account.py --no-quote --json

    # 移除持仓
    uv run python run_account.py --remove --symbol 600000.SH
"""

from __future__ import annotations

import argparse

from account import account_path, load_account, remove_position, set_position
from cli_common import (
    add_json_arg,
    build_next_steps,
    check_symbol,
    emit_json,
    init_log,
    log_next_steps,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from report import attach_meta


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 统一持仓账户（登记/查看/移除）", __doc__)
    parser.add_argument("--set", action="store_true", help="登记/更新持仓（需 --symbol/--shares/--cost）")
    parser.add_argument("--remove", action="store_true", help="移除持仓（需 --symbol）")
    parser.add_argument("--symbol", default=None, help="标的代码，如 600000.SH")
    parser.add_argument("--shares", type=float, default=None, help="持仓数量")
    parser.add_argument("--cost", type=float, default=None, help="持仓成本价")
    parser.add_argument("--note", default="", help="备注（可选）")
    parser.add_argument("--no-quote", action="store_true", help="查看时不拉行情（离线，只列登记信息）")
    add_json_arg(parser)
    return parser


def _fetch_last_price(symbol: str) -> float | None:
    """拉最新收盘价（走缓存）；失败返回 None 不中断展示。"""
    from datafeed import fetch_ohlcv

    try:
        df = fetch_ohlcv(symbol, period="1d", count=5)
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    if args.set and args.remove:
        raise SystemExit("[error] --set 与 --remove 不能同时使用。")

    action = "list"
    if args.set:
        action = "set"
        if not (args.symbol and args.shares is not None and args.cost is not None):
            raise SystemExit(
                "[error] 登记持仓需要 --symbol、--shares、--cost 三个参数，"
                "如：run_account.py --set --symbol 600000.SH --shares 1000 --cost 8.50"
            )
        symbol = check_symbol(args.symbol)
        try:
            set_position(symbol, args.shares, args.cost, note=args.note)
        except ValueError as exc:
            raise SystemExit(f"[error] {exc}") from exc
        log(f"已登记：{symbol} {args.shares:g} 股 @ {args.cost}")
    elif args.remove:
        action = "remove"
        if not args.symbol:
            raise SystemExit("[error] 移除持仓需要 --symbol 指定标的。")
        symbol = check_symbol(args.symbol)
        try:
            remove_position(symbol)
        except ValueError as exc:
            raise SystemExit(f"[error] {exc}") from exc
        log(f"已移除：{symbol}")

    # ---------- 账户总览 ----------
    acct = load_account()
    positions = acct["positions"]
    rows = []
    total_mv = total_cost = 0.0
    quoted = not args.no_quote
    for sym in sorted(positions):
        pos = positions[sym]
        last = _fetch_last_price(sym) if quoted else None
        cost_value = pos["shares"] * pos["cost"]
        row = {
            "symbol": sym,
            "shares": pos["shares"],
            "cost": pos["cost"],
            "note": pos.get("note", ""),
            "last_price": last,
            "market_value": (pos["shares"] * last) if last else None,
            "pnl_pct": (last / pos["cost"] - 1) if last else None,
        }
        rows.append(row)
        total_cost += cost_value
        if row["market_value"]:
            total_mv += row["market_value"]

    log()
    log(f"===== 持仓账户（{len(rows)} 只）=====")
    if not rows:
        log("（空。用 --set 登记持仓后，run_score/run_scan 会自动联动。）")
    for row in rows:
        pnl = f"{row['pnl_pct'] * 100:+.2f}%" if row["pnl_pct"] is not None else "N/A"
        last = f"{row['last_price']:.3f}" if row["last_price"] is not None else "N/A"
        note = f"  {row['note']}" if row["note"] else ""
        log(f"  {row['symbol']:<12} {row['shares']:>10g} 股  成本 {row['cost']:<8g} "
            f"现价 {last:<8} 浮盈亏 {pnl}{note}")
    if quoted and total_mv > 0 and total_cost > 0:
        log(f"  合计市值 {total_mv:,.2f}（成本 {total_cost:,.2f}，"
            f"浮盈亏 {(total_mv / total_cost - 1) * 100:+.2f}%）")
    log(f"\n账户文件：{account_path()}")

    if rows:
        log_next_steps(
            log,
            "逐只体检 run_score.py --symbol <代码>（自动带入账户成本给操作建议）",
            "扫描候选排除已持标的 run_scan.py --symbols ... --exclude-held",
        )

    if args.json is not None:
        n = len(rows)
        pnl_str = (
            f"，合计浮盈亏 {(total_mv / total_cost - 1) * 100:+.2f}%"
            if quoted and total_mv > 0 and total_cost > 0
            else ""
        )
        payload = attach_meta(
            {
                "action": action,
                "positions": rows,
                "total_market_value": total_mv if quoted and total_mv > 0 else None,
                "total_cost_value": total_cost if total_cost > 0 else None,
                "account_file": str(account_path()),
                "summary": f"账户当前持有 {n} 只标的{pnl_str}。仅登记持仓，不做交易执行。",
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "对持仓逐只体检（自动带入成本）",
                     "command": "run_score.py --symbol <代码> --json"},
                    {"action": "scan", "reason": "扫描新候选并排除已持标的",
                     "command": "run_scan.py --symbols <列表> --exclude-held --json"},
                ),
            },
            command="account",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
