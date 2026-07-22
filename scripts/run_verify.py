#!/usr/bin/env python3
"""多源数据交叉验证 CLI：同时拉取 TickFlow 与对照源的 K 线，比对 OHLCV 差异。

适用场景：
- 实盘信号发出前确认数据一致性；
- 因子研究时校验复权口径是否统一；
- 定期巡检数据源质量。

对照源可选 baostock（默认，仅沪深）或 akshare（含北交所）；
其他市场/周期会明确报错。

示例：
    # 验证单只标的日 K 数据一致性（默认对照源 baostock）
    uv run python run_verify.py --symbols 600000.SH

    # 指定对照源为 akshare
    uv run python run_verify.py --symbols 600000.SH --source-b akshare

    # 多标的 + 自定义阈值
    uv run python run_verify.py --symbols 600000.SH,000001.SZ --threshold 0.3

    # 结构化 JSON 输出（供 agent/脚本消费）
    uv run python run_verify.py --symbols 600519.SH --json

    # 周 K 验证
    uv run python run_verify.py --symbols 600000.SH --period 1w --count 200
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

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
from data.verify import (
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_VOLUME_THRESHOLD,
    VERIFY_SOURCES,
    VerifyResult,
    verify_symbol,
)
from report import attach_meta, frame_table


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 多源数据交叉验证", __doc__)
    parser.add_argument(
        "--symbols", required=True, help="标的代码，逗号分隔（仅 A 股），如 600000.SH,000001.SZ"
    )
    parser.add_argument(
        "--period", default="1d", choices=["1d", "1w", "1M"],
        help="K 线周期（仅日/周/月），默认 1d",
    )
    parser.add_argument("--count", type=int, default=1250, help="拉取 K 线数量，默认 1250（约 5 年）")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_PRICE_THRESHOLD,
        help=f"价格列（OHLC）相对误差阈值（%%），默认 {DEFAULT_PRICE_THRESHOLD}",
    )
    parser.add_argument(
        "--volume-threshold", type=float, default=DEFAULT_VOLUME_THRESHOLD,
        help=f"成交量列相对误差阈值（%%），默认 {DEFAULT_VOLUME_THRESHOLD}",
    )
    parser.add_argument(
        "--source-b", default="baostock", choices=list(VERIFY_SOURCES),
        help="对照数据源（默认 baostock，可选 akshare/tickflow）",
    )
    add_json_arg(parser)
    return parser


def _print_result(result: VerifyResult, log) -> None:
    """终端友好地打印单个验证结果。"""
    status = "✓ PASS" if result.passed else "✗ FAIL"
    log(f"\n{'─' * 60}")
    log(f"  {result.symbol}  [{result.period}]  {status}")
    log(f"  数据源：{result.source_a} vs {result.source_b}")
    log(f"  行数：{result.source_a}={result.rows_a}  {result.source_b}={result.rows_b}  对齐={result.aligned_rows}")

    if result.columns:
        rows = []
        for c in result.columns:
            flag = "✓" if c.passed else "✗"
            rows.append({
                "列": c.column,
                "最大偏差%": f"{c.max_rel_pct:.4f}",
                "平均偏差%": f"{c.mean_rel_pct:.4f}",
                "超阈值行数": c.mismatch_count,
                "阈值%": f"{c.threshold_pct:.2f}",
                "结果": flag,
            })
        table = pd.DataFrame(rows)
        log("")
        frame_table(table, title=f"{result.symbol} 逐列比对", stderr=False)

    if result.warnings:
        log("")
        for w in result.warnings:
            log(f"  ⚠ {w}")


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    symbols = split_symbols(args.symbols, min_count=1, what="交叉验证")

    results: list[VerifyResult] = []
    for sym in symbols:
        log(f"验证 {sym} {args.period}（TickFlow vs {args.source_b}）...")
        try:
            r = verify_symbol(
                sym,
                period=args.period,
                count=args.count,
                adjust=args.adjust,
                price_threshold_pct=args.threshold,
                volume_threshold_pct=args.volume_threshold,
                source_b_name=args.source_b,
            )
            results.append(r)
            _print_result(r, log)
        except RuntimeError as exc:
            log(f"  [skip] {sym}: {exc}", file=sys.stderr)
            results.append(None)  # type: ignore[arg-type]

    # 汇总
    valid = [r for r in results if r is not None]
    passed_count = sum(1 for r in valid if r.passed)
    failed_count = len(valid) - passed_count

    log(f"\n{'═' * 60}")
    log(f"  验证完成：{len(valid)} 只标的，{passed_count} PASS / {failed_count} FAIL")
    if failed_count > 0:
        log("  ⚠ 存在数据不一致，建议排查复权口径或数据源异常后再用于实盘决策。")
    log(f"{'═' * 60}")

    if args.json is not None:
        records = []
        for r in valid:
            records.append({
                "symbol": r.symbol,
                "period": r.period,
                "source_a": r.source_a,
                "source_b": r.source_b,
                "rows_a": r.rows_a,
                "rows_b": r.rows_b,
                "aligned_rows": r.aligned_rows,
                "passed": r.passed,
                "columns": [
                    {
                        "column": c.column,
                        "max_rel_pct": round(c.max_rel_pct, 6),
                        "mean_rel_pct": round(c.mean_rel_pct, 6),
                        "mismatch_count": c.mismatch_count,
                        "threshold_pct": c.threshold_pct,
                        "passed": c.passed,
                    }
                    for c in r.columns
                ],
                "warnings": r.warnings,
            })
        status_text = "全部通过" if failed_count == 0 else f"{failed_count} 只标的不一致"
        payload = attach_meta(
            {
                "period": args.period,
                "count": args.count,
                "adjust": args.adjust,
                "price_threshold_pct": args.threshold,
                "volume_threshold_pct": args.volume_threshold,
                "results": records,
                "passed": passed_count,
                "failed": failed_count,
                "summary": (
                    f"多源交叉验证（TickFlow vs {args.source_b}）：{len(valid)} 只 A 股标的，"
                    f"{status_text}。阈值：价格 {args.threshold}%、成交量 {args.volume_threshold}%。"
                ),
                "next_steps": build_next_steps(
                    {"action": "backtest", "reason": "数据验证通过后进行策略回测",
                     "command": "run_backtest.py --symbol <代码> --strategy ma_cross --json"},
                    {"action": "signal", "reason": "获取最新策略信号",
                     "command": "run_signal.py --symbols <代码> --strategy ma_cross --json"},
                ),
            },
            command="verify",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
