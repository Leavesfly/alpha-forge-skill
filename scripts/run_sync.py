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

    # 缓存治理：查看用量 / 清理超过 180 天未更新的条目（先试跑）
    uv run python run_sync.py --cache-usage
    uv run python run_sync.py --prune-days 180 --dry-run
    uv run python run_sync.py --prune-days 180

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
from data.cache import cache_usage, prune_cache, resolve_cache_dir
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
    parser.add_argument(
        "--cache-usage", action="store_true",
        help="只输出缓存用量统计（条目数/占用空间/最旧条目）后退出",
    )
    parser.add_argument(
        "--prune-days", type=int, default=None,
        help="只清理抓取时间超过 N 天的缓存条目后退出（退市标的的文件会永久残留）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="配合 --prune-days：只统计待删除量，不真的删",
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


def _report_cache_usage(args: argparse.Namespace, log) -> None:
    """输出缓存用量统计。"""
    usage = cache_usage()
    log(f"\n{'═' * 60}")
    log(f"  缓存目录：{usage['cache_dir']}")
    if not usage["exists"]:
        log("  （目录不存在，尚未同步任何数据）")
    else:
        log(f"  K 线条目：{usage['kline_entries']}")
        log(f"  快照/指标条目：{usage['table_entries']}")
        log(f"  占用空间：{usage['total_mb']} MB")
        log(f"  最旧条目：{usage['oldest_entry'] or '未知'}")
    log(f"{'═' * 60}")

    if args.json is not None:
        payload = attach_meta(
            {
                **usage,
                "summary": (
                    f"缓存用量：{usage['kline_entries']} 个 K 线条目 + "
                    f"{usage['table_entries']} 个快照条目，共 {usage['total_mb']} MB，"
                    f"目录 {usage['cache_dir']}。"
                ),
                "next_steps": build_next_steps(
                    {"action": "prune", "reason": "清理长期未更新的陈旧条目（先试跑）",
                     "command": "run_sync.py --prune-days 180 --dry-run"},
                ),
            },
            command="sync",
        )
        emit_json(args.json, payload, log)


def _report_prune(args: argparse.Namespace, log) -> None:
    """执行（或试跑）过期缓存清理。"""
    if args.prune_days < 0:
        raise SystemExit("[error] --prune-days 不能为负数。")
    report = prune_cache(args.prune_days, dry_run=args.dry_run, config=None)
    mode = "试跑（未删除）" if args.dry_run else "已删除"
    log(f"\n{'═' * 60}")
    log(f"  缓存清理{mode}：{report.removed} 个条目，"
        f"{report.freed_bytes / 1024 / 1024:.2f} MB（阈值 {args.prune_days} 天）")
    for name in report.entries[:10]:
        log(f"  - {name}")
    if len(report.entries) > 10:
        log(f"  ...（其余 {len(report.entries) - 10} 个略）")
    log(f"{'═' * 60}")

    if args.json is not None:
        payload = attach_meta(
            {
                **report.to_dict(),
                "max_age_days": args.prune_days,
                "cache_dir": str(resolve_cache_dir()),
                "summary": (
                    f"缓存清理{mode}：{report.removed} 个条目，"
                    f"{report.freed_bytes / 1024 / 1024:.2f} MB（保留 {args.prune_days} 天内）。"
                ),
                "next_steps": build_next_steps(
                    {"action": "usage", "reason": "清理后确认当前用量",
                     "command": "run_sync.py --cache-usage --json"},
                ),
            },
            command="sync",
        )
        emit_json(args.json, payload, log)


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    # 缓存治理子命令：与同步互斥，处理完直接退出
    if args.cache_usage:
        _report_cache_usage(args, log)
        return
    if args.prune_days is not None:
        _report_prune(args, log)
        return

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
