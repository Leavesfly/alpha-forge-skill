#!/usr/bin/env python3
"""批量预同步 CLI：一次性把股票池 K 线同步到本地缓存（先同步、后研究）。

借鉴 free-stockdb 本地优先思路：数据工程（拉取/增量更新/多源降级）在同步
阶段完成，之后全市场扫描/回测直接命中本地缓存不再逐只联网；配合
``ALPHA_FORGE_OFFLINE=1`` 可完全离线研究。缓存目录与 skill 目录解耦
（新默认 ~/.alpha-forge/klines，重装 skill 不丢数据；ALPHA_FORGE_CACHE_DIR 可覆盖）。

示例：
    # 同步指定标的的日 K（默认 1250 根，约 5 年）
    uv run python run_sync.py --symbols 600000.SH,000001.SZ

    # 全市场 A 股预同步（无 TICKFLOW_API_KEY 时自动经 akshare 快照取代码）
    uv run python run_sync.py --universe CN_Equity_A

    # 美股池预同步（无 Key 时经东财美股快照取代码，顺带预热快照本地缓存；
    # 快照不可用时降级 S&P 500 名单）
    uv run python run_sync.py --universe US_Equity --limit 500

    # 只同步池内前 100 只并提高并发（自担免费源限频风险）
    uv run python run_sync.py --universe CN_Equity_A --limit 100 --workers 4

    # 结构化 JSON 输出（供 agent/脚本消费）
    uv run python run_sync.py --symbols 600000.SH --json
"""

from __future__ import annotations

import argparse
import os
import sys

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
from data.cache import resolve_cache_dir
from data.sync import DEFAULT_WORKERS, sync_symbols
from report import attach_meta


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 批量预同步：股票池 K 线落盘本地缓存", __doc__)
    parser.add_argument(
        "--symbols", default=None,
        help="标的代码，逗号分隔（与 --universe 二选一），如 600000.SH,000001.SZ",
    )
    parser.add_argument(
        "--universe", default=None,
        help="股票池名称（与 --symbols 二选一），如 CN_Equity_A / US_Equity；"
             "无 TICKFLOW_API_KEY 时 A 股池降级 akshare 快照、美股池降级东财"
             "美股快照（再降级 S&P 500 名单）",
    )
    parser.add_argument("--limit", type=int, default=None, help="股票池截断数量（默认全部）")
    parser.add_argument(
        "--period", default="1d", choices=["1d", "1w", "1M"],
        help="K 线周期（日/周/月），默认 1d",
    )
    parser.add_argument("--count", type=int, default=1250, help="每只标的同步 K 线数量，默认 1250（约 5 年）")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"并发线程数，默认 {DEFAULT_WORKERS}（调高自担免费源限频风险）",
    )
    add_json_arg(parser)
    return parser


def resolve_universe(universe: str, limit: int | None, log) -> list[str]:
    """解析股票池为标的代码列表（两级策略）。

    1. 有 ``TICKFLOW_API_KEY`` → ``datafeed.fetch_universe``（支持 CN/US/HK 池）；
    2. 无 Key → A 股池降级 akshare 全市场快照；美股池降级东财美股快照
       （本地优先，顺带预热筛选器 Phase 1 缓存），快照不可用再降级
       S&P 500 成分股名单（stderr 告警）。
    """
    if os.environ.get("TICKFLOW_API_KEY"):
        from datafeed import fetch_universe

        return fetch_universe(universe, limit=limit)

    uni = universe.upper()
    if uni.startswith("CN"):
        log("[warn] 未配置 TICKFLOW_API_KEY，降级用 akshare 全市场快照获取 A 股代码")

        from market import code_to_symbol
        from screener.data import fetch_astock_snapshot

        snapshot = fetch_astock_snapshot(log=log)
        if snapshot is None or len(snapshot) == 0:
            raise RuntimeError(
                "akshare 全市场快照拉取失败，无法解析股票池；"
                "请检查网络或配置 TICKFLOW_API_KEY 后重试。"
            )
        symbols = [code_to_symbol(str(c)) for c in snapshot["code"].tolist()]
    elif uni.startswith("US"):
        log("[warn] 未配置 TICKFLOW_API_KEY，降级用东财美股快照获取美股代码（顺带预热快照缓存）")

        from screener.data import fetch_sp500_symbols, fetch_us_snapshot

        snapshot = fetch_us_snapshot(log=log)
        if snapshot is not None and len(snapshot):
            # 按市值降序：--limit 截断时优先保留大市值（流动性好、yfinance 覆盖稳）
            symbols = (
                snapshot.sort_values("total_mv", ascending=False)["code"]
                .astype(str).tolist()
            )
        else:
            log("[warn] 东财美股快照不可用，再降级 S&P 500 成分股名单")
            symbols = fetch_sp500_symbols(log=log) or []
        if not symbols:
            raise RuntimeError(
                "美股快照与 S&P 500 名单都不可用，无法解析股票池；"
                "请检查网络或配置 TICKFLOW_API_KEY 后重试。"
            )
    else:
        raise RuntimeError(
            f"股票池 {universe} 需要配置 TICKFLOW_API_KEY；"
            "无 Key 时仅支持 A 股池（CN_Equity_A）与美股池（US_Equity）降级获取。"
        )

    if limit is not None and limit > 0:
        symbols = symbols[:limit]
    return symbols


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    if bool(args.symbols) == bool(args.universe):
        raise SystemExit("[error] --symbols 与 --universe 必须二选一。")

    if args.symbols:
        symbols = split_symbols(args.symbols, min_count=1, what="批量同步")
    else:
        symbols = resolve_universe(args.universe, args.limit, log)

    cache_dir = resolve_cache_dir()
    log(f"开始同步 {len(symbols)} 只标的（{args.period}，{args.count} 根，并发 {args.workers}）")
    log(f"缓存目录：{cache_dir}")

    report = sync_symbols(
        symbols,
        period=args.period,
        count=args.count,
        adjust=args.adjust,
        workers=args.workers,
        log=log,
    )

    log(f"\n{'═' * 60}")
    log(
        f"  同步完成：{report.total} 只，成功 {report.synced}，"
        f"失败 {len(report.failed)}，耗时 {report.elapsed_seconds:.1f}s"
    )
    if report.failed:
        for sym, err in report.failed[:10]:
            log(f"  ✗ {sym}: {err}")
        if len(report.failed) > 10:
            log(f"  ...（其余 {len(report.failed) - 10} 只失败略）")
    log(f"{'═' * 60}")

    if args.json is not None:
        payload = attach_meta(
            {
                "period": args.period,
                "count": args.count,
                "adjust": args.adjust,
                "workers": args.workers,
                "cache_dir": str(cache_dir),
                "total": report.total,
                "synced": report.synced,
                "failed": [
                    {"symbol": sym, "error": err} for sym, err in report.failed
                ],
                "elapsed_seconds": round(report.elapsed_seconds, 2),
                "summary": (
                    f"批量预同步 {report.total} 只标的（{args.period}）："
                    f"成功 {report.synced}，失败 {len(report.failed)}，"
                    f"耗时 {report.elapsed_seconds:.1f}s；缓存目录 {cache_dir}。"
                ),
                "next_steps": build_next_steps(
                    {"action": "screener", "reason": "数据已落盘，全市场筛选将命中本地缓存",
                     "command": "run_screener.py --preset value --json"},
                    {"action": "offline", "reason": "设置离线模式后回测/扫描完全不联网",
                     "command": "export ALPHA_FORGE_OFFLINE=1"},
                ),
            },
            command="sync",
        )
        emit_json(args.json, payload, log)

    if report.total and report.synced == 0:
        print("[error] 全部标的同步失败，请检查网络或数据源配置。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_cli(main)
